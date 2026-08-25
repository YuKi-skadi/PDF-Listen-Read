"""Build protocol-compatible ZIP packages from generated WAV segments."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def concat_wavs(paths: list[Path], output: Path) -> int:
    if not paths:
        raise ValueError("没有可合并的音频分段")
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()
        frames = [first.readframes(first.getnframes())]
    for path in paths[1:]:
        with wave.open(str(path), "rb") as current:
            current_params = current.getparams()
            if (current_params.nchannels, current_params.sampwidth, current_params.framerate) != (params.nchannels, params.sampwidth, params.framerate):
                raise ValueError("音频分段的采样参数不一致，无法合并")
            frames.append(current.readframes(current.getnframes()))
    with wave.open(str(output), "wb") as merged:
        merged.setparams(params)
        merged.writeframes(b"".join(frames))
    return int(sum(len(chunk) for chunk in frames) / params.nchannels / params.sampwidth / params.framerate * 1000)


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as audio:
        return int(audio.getnframes() / audio.getframerate() * 1000)


def build_package(job: dict, work_dir: Path, package_dir: Path) -> tuple[Path, dict]:
    segment_paths = []
    manifest_segments = []
    for index, segment in enumerate(job["segments"]):
        path = work_dir / "segments" / f"{index:05d}.wav"
        if not path.exists():
            continue
        segment_paths.append(path)
        manifest_segments.append({
            "segment_id": segment["segment_id"],
            "index": segment["index"],
            "content_hash": segment["content_hash"],
            "file": f"segments/{index:05d}.wav",
            "duration_ms": wav_duration_ms(path),
        })
    if not segment_paths:
        raise ValueError("没有生成成功的音频分段")
    full_path = work_dir / "full.wav"
    full_duration = concat_wavs(segment_paths, full_path)
    package_id = f"pkg_{uuid.uuid4().hex[:16]}"
    package_dir.mkdir(parents=True, exist_ok=True)
    zip_path = package_dir / f"{package_id}.zip"
    manifest = {
        "schema_version": "pdf-local-tts-1",
        "job_id": job["id"],
        "package_id": package_id,
        "paper_id": job["paper_id"],
        "text_version_id": job["text_version_id"],
        "provider": "local_bridge",
        "model": job["model"],
        "voice_id": job["voice_id"],
        "voice_mode": job.get("voice_mode", "custom_voice"),
        "speed": job["speed"],
        "created_at": utc_now(),
        "segments": manifest_segments,
        "full_audio": {"file": "full.wav", "duration_ms": full_duration},
    }
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path in segment_paths:
            archive.write(path, f"segments/{path.name}")
        archive.write(full_path, "full.wav")
    return zip_path, manifest


def copy_export(package_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / package_path.name
    shutil.copy2(package_path, target)
    return target
