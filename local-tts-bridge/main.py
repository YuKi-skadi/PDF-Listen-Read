"""Entry point for PDF Local TTS Bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from bridge_server import BridgeServer
from bridge_ui import BridgeWindow, apply_theme
from job_manager import JobManager


BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"


def resolve_config_path(value: Any) -> str:
    """Resolve relative paths against the bridge directory, not the process cwd."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    bridge_candidate = (BASE_DIR / path).resolve()
    if bridge_candidate.exists():
        return str(bridge_candidate)
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return str(cwd_candidate)
    return str(bridge_candidate)


class AppController:
    def __init__(self):
        self.data_dir = DATA_DIR
        self.settings = self.load_settings()
        self.last_log = ""
        self.window = None
        self.bridge = None
        self.manager = None

    def load_settings(self) -> dict[str, Any]:
        # The bridge package intentionally contains no CUDA/ROCm, Python TTS
        # runtime or model. Those resources must be selected explicitly in the
        # settings page and are stored as paths outside the package.
        settings = {"port": 47840, "resource_root": "", "runtime_dir": "", "model_dir": "",
                    "backend": "cuda", "model_name": "Qwen3-TTS-12Hz-0.6B-CustomVoice", "speaker": "Vivian",
                    "voice_mode": "custom_voice", "reference_audio": "", "reference_text": "", "instruct": ""}
        if SETTINGS_FILE.exists():
            try:
                settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        for key in ("resource_root", "runtime_dir", "model_dir", "reference_audio"):
            settings[key] = resolve_config_path(settings.get(key, ""))
        runtime_value = str(settings.get("runtime_dir", "") or "").strip()
        runtime_path = Path(settings.get("runtime_dir", "")).expanduser()
        if settings.get("backend", "cuda") in {"cuda", "rocm"} and runtime_path.name.lower() == "runtime":
            cuda_runtime = runtime_path.parent / "runtime_cuda"
            if cuda_runtime.is_dir() and (cuda_runtime / "python" / ("python.exe" if sys.platform == "win32" else "python")).exists():
                settings["runtime_dir"] = str(cuda_runtime)
        if settings.get("model_dir") and not Path(settings["model_dir"]).is_dir():
            settings["model_dir"] = ""
        return settings

    def save_settings(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.manager = JobManager(self.data_dir, self.settings)
        self.bridge = BridgeServer(self.manager, "127.0.0.1", int(self.settings["port"]))
        self.manager.event_callback = self._event
        ok, detail = self.bridge.start()
        self.window = BridgeWindow(self)
        self.window.show()
        if ok:
            self._event("service", f"● 服务运行中  127.0.0.1:{self.settings['port']}")
        else:
            self._event("service", "● 服务启动失败")
            QTimer.singleShot(200, lambda: QMessageBox.warning(self.window, "监听服务启动失败", f"端口 {self.settings['port']} 无法监听：\n{detail}\n\n请在服务设置中更换端口。"))

    def _event(self, kind: str, payload: Any) -> None:
        if self.window:
            self.window.bus.event.emit(kind, payload)

    def apply_settings(self, updates: dict[str, Any]) -> None:
        old_port = int(self.settings.get("port", 47840))
        self.settings.update(updates)
        self.save_settings()
        if self.bridge and old_port != int(self.settings["port"]):
            self.bridge.stop()
            self.bridge = BridgeServer(self.manager, "127.0.0.1", int(self.settings["port"]))
            ok, detail = self.bridge.start()
            self._event("service", f"● 服务运行中  127.0.0.1:{self.settings['port']}" if ok else "● 服务启动失败")
            if not ok:
                QMessageBox.warning(self.window, "监听服务启动失败", detail)
        self.manager.settings = self.settings

    def shutdown(self) -> None:
        if self.bridge:
            self.bridge.stop()
        if self.manager:
            self.manager.shutdown()


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    controller = AppController()
    controller.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
