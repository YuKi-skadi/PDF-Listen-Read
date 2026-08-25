"""Launch and call an external TTS HTTP runtime."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RuntimeManager:
    def __init__(self, event_callback=None, log_path: Path | None = None):
        self.event_callback = event_callback or (lambda _message: None)
        self.log_path = log_path
        self.process: subprocess.Popen | None = None
        self.port = 47831
        self.lock = threading.RLock()

    def _emit(self, message: str) -> None:
        self.event_callback(message)

    @staticmethod
    def find_free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def infer_paths(runtime_dir: str | Path, model_dir: str | Path = "") -> tuple[Path, Path, Path]:
        runtime = Path(runtime_dir).expanduser()
        python_exe = runtime / "python" / ("python.exe" if os.name == "nt" else "python")
        server = runtime / "tts_server.py"
        if model_dir:
            models = Path(model_dir).expanduser()
        elif (runtime / "models").exists():
            models = runtime / "models"
        else:
            models = runtime.parent / "runtime" / "models"
        return python_exe, server, models

    def is_alive(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    @staticmethod
    def _check_torch_environment(python_exe: Path, runtime: Path, backend: str) -> tuple[bool, str]:
        probe = (
            "import torch; "
            "available = bool(torch.cuda.is_available()); "
            "print(f'torch={torch.__version__}; cuda_available={available}; cuda={torch.version.cuda}'); "
            f"\nif {backend!r} in ('cuda', 'rocm') and not available: raise SystemExit(2)"
        )
        try:
            result = subprocess.run(
                [str(python_exe), "-c", probe], cwd=str(runtime), capture_output=True,
                text=True, errors="replace", timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return False, f"Torch 环境预检超时：{python_exe}"
        except OSError as exc:
            return False, f"启动 Torch 环境预检失败：{exc}"
        output = " ".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
        if result.returncode != 0:
            return False, f"Torch 环境预检失败（退出码 {result.returncode}）：{output or '没有返回错误信息'}"
        return True, output or "Torch 环境正常"

    def start(self, runtime_dir: str, model_dir: str, model_name: str, backend: str) -> tuple[bool, str]:
        with self.lock:
            if self.is_alive():
                return True, "runtime already running"
            runtime = Path(runtime_dir).expanduser()
            python_exe, server, models = self.infer_paths(runtime, model_dir)
            model_path = models / model_name
            if not python_exe.exists():
                return False, f"未找到运行时 Python：{python_exe}"
            if not server.exists():
                return False, f"未找到 tts_server.py：{server}"
            if not model_path.exists():
                available = []
                if models.is_dir():
                    available = sorted(path.name for path in models.iterdir() if path.is_dir())
                suffix = f"；可用模型：{', '.join(available[:12])}" if available else "；模型目录中没有可识别的模型"
                return False, f"未找到模型目录：{model_path}{suffix}"
            torch_ok, torch_message = self._check_torch_environment(python_exe, runtime, backend)
            if not torch_ok:
                return False, torch_message
            self.port = self.find_free_port()
            args = [str(python_exe), str(server), "--port", str(self.port), "--model-dir", str(model_path), "--backend", backend]
            try:
                log_file = None
                if self.log_path:
                    self.log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_file = self.log_path.open("ab")
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self.process = subprocess.Popen(args, cwd=str(runtime), stdout=log_file, stderr=subprocess.STDOUT,
                                                creationflags=creationflags)
            except OSError as exc:
                return False, f"启动运行时失败：{exc}"
            self._emit(f"已启动 TTS 运行时，等待模型服务 ({self.port})")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    return False, f"TTS 运行时提前退出，详见 {self.log_path}"
                try:
                    payload = self._request_json("/health", timeout=1)
                    if payload.get("ok"):
                        self._emit("TTS 运行时已就绪，首次生成时会加载模型")
                        return True, "runtime ready"
                except Exception:
                    time.sleep(0.25)
            return False, "等待 TTS 运行时超时"

    def synthesize(self, text: str, speaker: str, speed: float, voice_mode: str = "custom_voice",
                   reference_audio: str = "", reference_text: str = "", instruct: str = "") -> bytes:
        if not self.is_alive():
            raise RuntimeError("TTS 运行时未启动")
        body = json.dumps({"text": text, "speaker": speaker, "speed": speed, "mode": voice_mode,
                           "reference_audio": reference_audio, "reference_text": reference_text,
                           "instruct": instruct}, ensure_ascii=False).encode("utf-8")
        request = Request(f"http://127.0.0.1:{self.port}/synthesize", data=body,
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=600) as response:
                audio = response.read()
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8", errors="replace")).get("error", "")
            except (OSError, json.JSONDecodeError):
                detail = ""
            raise RuntimeError(f"TTS 运行时返回 HTTP {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"无法连接 TTS 运行时：{exc.reason}") from exc
        if not audio.startswith(b"RIFF"):
            raise RuntimeError("TTS 运行时返回的不是 WAV 音频")
        return audio

    def _request_json(self, path: str, timeout: float = 3) -> dict:
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def stop(self) -> None:
        with self.lock:
            process = self.process
            self.process = None
            if not process:
                return
            try:
                with urlopen(Request(f"http://127.0.0.1:{self.port}/shutdown", method="POST"), timeout=2):
                    pass
            except Exception:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            self._emit("TTS 运行时已停止")

    def release_memory(self) -> bool:
        """Unload the external model and release its CUDA/ROCm memory.

        The external runtime can implement ``POST /shutdown`` to unload its
        model (including backend-specific memory cleanup) before stopping the
        child process. Reuse that protocol here so the bridge can release
        accelerator memory without closing the bridge UI or HTTP service.
        """
        with self.lock:
            was_alive = self.is_alive()
        self.stop()
        return was_alive
