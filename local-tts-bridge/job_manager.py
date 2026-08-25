"""Persistent incoming job queue and serial TTS worker."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from audio_package import build_package, utc_now
from runtime_manager import RuntimeManager


LOCAL_CUSTOM_VOICES = {
    "aiden": "Aiden", "dylan": "Dylan", "eric": "Eric", "ono_anna": "Ono_Anna",
    "ryan": "Ryan", "serena": "Serena", "sohee": "Sohee", "uncle_fu": "Uncle_Fu",
    "vivian": "Vivian",
}


class JobManager:
    def __init__(self, data_dir: Path, settings: dict[str, Any], event_callback=None):
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.package_dir = data_dir / "packages"
        self.jobs_file = data_dir / "jobs.json"
        self.logs_file = data_dir / "bridge.log"
        self.settings = settings
        self.event_callback = event_callback or (lambda _kind, _payload: None)
        self.log_callback = lambda _message: None
        self.lock = threading.RLock()
        self.log_lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.logs: list[dict[str, Any]] = []
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.job_stop_events: dict[str, threading.Event] = {}
        self.runtime = RuntimeManager(log_path=data_dir / "tts-runtime.log", event_callback=self.log_event)
        self._load_logs()
        self._load()

    def log_event(self, message: str, level: str = "info", job_id: str = "", details: str = "") -> None:
        entry = {"timestamp": utc_now(), "level": level, "job_id": job_id,
                 "message": str(message), "details": str(details or "")}
        with self.log_lock:
            self.logs.append(entry)
            self.logs = self.logs[-2000:]
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                with self.logs_file.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass
        self.log_callback(entry)
        self.event_callback("log", entry)

    def _load_logs(self) -> None:
        if not self.logs_file.exists():
            return
        try:
            lines = self.logs_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        loaded: list[dict[str, Any]] = []
        for line in lines[-2000:]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                loaded.append(value)
        self.logs = loaded

    def recent_logs(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.log_lock:
            return [dict(item) for item in self.logs[-max(1, min(limit, 2000)):]]

    def logs_for_job(self, job_id: str) -> list[dict[str, Any]]:
        return [item for item in self.recent_logs(2000) if item.get("job_id") == job_id]

    def runtime_log_tail(self, max_bytes: int = 200_000) -> str:
        path = self.runtime.log_path
        if not path or not path.exists():
            return "（TTS 运行时日志尚未生成）"
        try:
            raw = path.read_bytes()[-max_bytes:]
            return raw.decode("utf-8", errors="replace") or "（TTS 运行时日志为空）"
        except OSError as exc:
            return f"（读取 TTS 运行时日志失败：{exc}）"

    def clear_logs(self) -> None:
        with self.log_lock:
            self.logs = []
            try:
                self.logs_file.unlink(missing_ok=True)
            except OSError:
                pass
        self.event_callback("logs_cleared", None)

    def _load(self) -> None:
        if self.jobs_file.exists():
            try:
                self.jobs = json.loads(self.jobs_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.jobs = {}
        for job in self.jobs.values():
            if job.get("status") in {"running", "queued"}:
                job["status"] = "queued"
                job["message"] = "应用重启后重新排队"
        self._persist()

    def _persist(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_file.write_text(json.dumps(self.jobs, ensure_ascii=False, indent=2), encoding="utf-8")

    def health_payload(self) -> dict[str, Any]:
        with self.lock:
            return {"ok": True, "service": "pdf-local-tts-bridge", "schema_version": "pdf-local-tts-1",
                    "queue_size": sum(j.get("status") == "queued" for j in self.jobs.values()),
                    "runtime_alive": self.runtime.is_alive()}

    def public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if key != "segments"} | {
            "segment_count": len(job.get("segments", [])),
            "failed_segments": job.get("failed_segments", []),
        }

    def _configured_model_name(self, requested: str) -> str:
        configured = str(self.settings.get("model_name") or "").strip()
        candidate = str(requested or "").strip()
        model_dir_value = str(self.settings.get("model_dir", "") or "").strip()
        model_root = Path(model_dir_value).expanduser()
        if not model_dir_value:
            _python_exe, _server, model_root = self.runtime.infer_paths(self.settings.get("runtime_dir", ""))
        available = sorted(path.name for path in model_root.iterdir() if path.is_dir()) if model_root.is_dir() else []
        if candidate and (model_root / candidate).is_dir():
            return candidate
        if configured and (model_root / configured).is_dir():
            if candidate and candidate != configured:
                self.log_event(f"请求模型 {candidate} 不存在，已回退到中介设置的本地模型 {configured}", level="warning")
            return configured
        voice_mode = str(self.settings.get("voice_mode") or "custom_voice")
        preferred = [name for name in available if ("base" in name.lower()) == (voice_mode == "voice_clone")]
        fallback = preferred[0] if preferred else (available[0] if available else "")
        if candidate or configured:
            self.log_event(f"模型 {candidate or configured} 不存在，已自动选择本地模型 {fallback or '未找到'}", level="warning")
        return fallback or configured or candidate

    def _configured_voice_name(self, requested: str, voice_mode: str) -> str:
        configured = str(self.settings.get("speaker") or "Vivian").strip()
        candidate = str(requested or "").strip()
        if voice_mode == "voice_clone":
            return candidate or configured or "voice_clone"
        candidate_voice = LOCAL_CUSTOM_VOICES.get(candidate.lower())
        if candidate_voice:
            return candidate_voice
        configured_voice = LOCAL_CUSTOM_VOICES.get(configured.lower())
        if candidate:
            self.log_event(f"请求音色 {candidate} 不是本地 CustomVoice 支持的音色，已回退到 {configured_voice or 'Vivian'}", level="warning")
        return configured_voice or "Vivian"

    def _refresh_job_local_configuration(self, job: dict[str, Any]) -> None:
        """Re-resolve persisted jobs against the current external TTS settings.

        Jobs created by an older bridge build may contain an online voice name
        such as ``Cherry``.  Retrying such a job must not send that stale name
        directly to the external Qwen runtime; apply the same local model and
        voice normalization used for newly received jobs.
        """
        model = self._configured_model_name(str(job.get("model") or ""))
        if model:
            job["model"] = model
        voice_mode = str(job.get("voice_mode") or self.settings.get("voice_mode") or "custom_voice")
        job["voice_mode"] = voice_mode
        local_voice = self._configured_voice_name(str(job.get("voice_id") or job.get("voice") or ""), voice_mode)
        job["voice"] = local_voice
        job["voice_id"] = local_voice

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.jobs.get(job_id)

    def create_job(self, request: dict[str, Any]) -> dict[str, Any]:
        required = ["schema_version", "job_type", "paper_id", "text_version_id", "segments"]
        missing = [field for field in required if not request.get(field)]
        if missing:
            raise ValueError(f"缺少字段：{', '.join(missing)}")
        if request["schema_version"] != "pdf-local-tts-1":
            raise ValueError("不支持的协议版本")
        if request["job_type"] != "pdf_audio":
            raise ValueError("不支持的任务类型")
        if not isinstance(request["segments"], list) or not request["segments"]:
            raise ValueError("segments 不能为空")
        segments = []
        for index, segment in enumerate(request["segments"]):
            if not isinstance(segment, dict) or not segment.get("text"):
                raise ValueError(f"第 {index + 1} 个分段缺少文本")
            text = str(segment["text"])
            content_hash = str(segment.get("content_hash") or hashlib.sha256(text.encode("utf-8")).hexdigest())
            segments.append({"index": int(segment.get("index", index)), "segment_id": str(segment.get("segment_id") or f"seg_{index:05d}"),
                             "content_hash": content_hash, "text": text})
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        local_model = self._configured_model_name(str(request.get("model") or ""))
        voice_mode = str(request.get("voice_mode") or self.settings.get("voice_mode") or "custom_voice")
        local_voice = self._configured_voice_name(str(request.get("voice_id") or request.get("voice") or ""), voice_mode)
        job = {"id": job_id, "status": "queued", "progress": 0, "message": "等待开始",
               "error_message": "", "created_at": utc_now(), "started_at": "", "finished_at": "",
               "paper_id": str(request["paper_id"]), "text_version_id": str(request["text_version_id"]),
               "text_version_label": str(request.get("text_version_label") or request["text_version_id"]),
               "model": local_model or "Qwen3-TTS-12Hz-0.6B-CustomVoice",
               "voice": local_voice,
               "voice_id": local_voice,
               "speed": float(request.get("speed", 1.0)),
               "voice_mode": voice_mode,
               "reference_audio": str(request.get("reference_audio") or self.settings.get("reference_audio") or ""),
               "reference_text": str(request.get("reference_text") or self.settings.get("reference_text") or ""),
               "instruct": str(request.get("instruct") or self.settings.get("instruct") or ""),
               "segments": segments, "failed_segments": [], "package_path": ""}
        if job["voice_mode"] not in {"custom_voice", "voice_clone"}:
            raise ValueError("不支持的声音模式")
        if job["voice_mode"] == "voice_clone" and not Path(job["reference_audio"]).is_file():
            raise ValueError("声音克隆模式需要一个存在的参考音频文件")
        if job["voice_mode"] == "voice_clone" and "base" not in job["model"].lower():
            raise ValueError("声音克隆建议使用名称包含 Base 的 Qwen 模型")
        with self.lock:
            self.jobs[job_id] = job
            self._persist()
        self.event_callback("job", self.public_job(job))
        self.log_event(f"收到生成任务：{job_id}，共 {len(segments)} 个分段", job_id=job_id)
        return job

    def start_job(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.get("status") not in {"queued", "failed", "completed_with_errors"}:
                return False
            self._refresh_job_local_configuration(job)
            job["status"] = "queued"
            job["message"] = "等待工作线程"
            job["error_message"] = ""
            self.job_stop_events[job_id] = threading.Event()
            self._persist()
            if not self.worker_thread or not self.worker_thread.is_alive():
                self.stop_event.clear()
                self.worker_thread = threading.Thread(target=self._worker_loop, name="pdf-tts-worker", daemon=True)
                self.worker_thread.start()
        self.event_callback("job", self.public_job(job))
        self.log_event(f"任务开始执行：{job_id}", job_id=job_id)
        return True

    def start_all(self) -> int:
        count = 0
        with self.lock:
            ids = [job_id for job_id, job in self.jobs.items() if job.get("status") == "queued"]
        for job_id in ids:
            count += int(self.start_job(job_id))
        return count

    def release_runtime_memory(self) -> tuple[bool, str]:
        with self.lock:
            running = any(job.get("status") == "running" for job in self.jobs.values())
        if running:
            self.log_event("释放显存请求被拒绝：当前仍有任务正在生成", level="warning")
            return False, "当前仍有任务正在生成，请等待任务结束或先停止任务。"
        released = self.runtime.release_memory()
        message = ("外部 TTS 运行时已停止，并已请求释放模型占用的 CUDA/ROCm 显存。"
                   if released else "当前没有运行中的外部 TTS 运行时。")
        self.log_event(message)
        return True, message

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            if job.get("status") == "running":
                self.job_stop_events.setdefault(job_id, threading.Event()).set()
                job["message"] = "正在停止当前任务"
            elif job.get("status") == "queued":
                job["status"] = "cancelled"
                job["finished_at"] = utc_now()
                job["message"] = "已取消"
            self._persist()
        self.event_callback("job", self.public_job(job))
        return job

    def clear_finished(self) -> int:
        with self.lock:
            ids = [job_id for job_id, job in self.jobs.items() if job.get("status") in {"completed", "completed_with_errors", "failed", "cancelled"}]
            for job_id in ids:
                job_dir = self.jobs_dir / job_id
                if job_dir.exists():
                    shutil.rmtree(job_dir, ignore_errors=True)
                self.jobs.pop(job_id, None)
            self._persist()
        self.event_callback("jobs_cleared", len(ids))
        return len(ids)

    def clear_job(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.get("status") == "running":
                return False
            job_dir = self.jobs_dir / job_id
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
            self.jobs.pop(job_id, None)
            self._persist()
        self.event_callback("jobs_cleared", 1)
        return True

    def package_path(self, job_id: str) -> Path | None:
        job = self.get_job(job_id)
        if not job or job.get("status") not in {"completed", "completed_with_errors"}:
            return None
        path = Path(job.get("package_path", ""))
        return path if path.exists() else None

    def completed_packages(self) -> list[dict[str, Any]]:
        with self.lock:
            return [job for job in self.jobs.values() if job.get("package_path") and Path(job["package_path"]).exists()]

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                pending = next((job for job in self.jobs.values() if job.get("status") == "queued"), None)
            if not pending:
                return
            self._run_job(pending)

    def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        stop_event = self.job_stop_events.setdefault(job_id, threading.Event())
        work_dir = self.jobs_dir / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "request.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.lock:
            job["status"] = "running"; job["started_at"] = utc_now(); job["message"] = "正在准备本地 TTS 运行时"; self._persist()
        self.event_callback("job", self.public_job(job))
        try:
            ok, message = self.runtime.start(self.settings.get("runtime_dir", ""), self.settings.get("model_dir", ""), job["model"], self.settings.get("backend", "cuda"))
            if not ok:
                raise RuntimeError(message)
            failed = []
            total = len(job["segments"])
            for position, segment in enumerate(job["segments"]):
                if stop_event.is_set():
                    raise InterruptedError("任务已停止")
                with self.lock:
                    job["message"] = f"生成第 {position + 1}/{total} 段"; job["progress"] = position / total; self._persist()
                self.event_callback("job", self.public_job(job))
                try:
                    audio = self.runtime.synthesize(segment["text"], job["voice_id"], job["speed"],
                                                     job["voice_mode"], job["reference_audio"],
                                                     job["reference_text"], job["instruct"])
                    segment_path = work_dir / "segments" / f"{position:05d}.wav"
                    segment_path.parent.mkdir(parents=True, exist_ok=True)
                    segment_path.write_bytes(audio)
                except Exception as exc:
                    failed.append({"index": segment["index"], "segment_id": segment["segment_id"], "error": str(exc)})
                    self.log_event(f"任务 {job_id} 分段 {position} 失败：{exc}", level="error", job_id=job_id,
                                   details=traceback.format_exc())
            job["failed_segments"] = failed
            package_path, _manifest = build_package(job, work_dir, self.package_dir)
            with self.lock:
                job["package_path"] = str(package_path); job["progress"] = 1; job["finished_at"] = utc_now()
                job["status"] = "completed_with_errors" if failed else "completed"
                job["message"] = f"完成，成功 {len(job['segments']) - len(failed)}/{len(job['segments'])} 段"; self._persist()
        except InterruptedError as exc:
            with self.lock:
                job["status"] = "cancelled"; job["finished_at"] = utc_now(); job["message"] = str(exc); self._persist()
        except Exception as exc:
            with self.lock:
                job["status"] = "failed"; job["finished_at"] = utc_now(); job["error_message"] = str(exc); job["message"] = "生成失败"; self._persist()
            self.log_event(f"任务 {job_id} 失败：{exc}", level="error", job_id=job_id,
                           details=traceback.format_exc())
        finally:
            self.event_callback("job", self.public_job(job))

    def shutdown(self) -> None:
        self.stop_event.set()
        for event in self.job_stop_events.values():
            event.set()
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2)
        self.runtime.stop()
