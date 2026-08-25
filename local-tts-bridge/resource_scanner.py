"""Discover external TTS runtimes, models and backend capabilities."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RuntimeCandidate:
    path: Path
    python_exe: Path
    server_script: Path
    model_dir: Path | None
    models: list[str] = field(default_factory=list)
    backends: list[str] = field(default_factory=list)
    backend_detail: str = ""
    ready: bool = False

    @property
    def label(self) -> str:
        suffix = " · " + ", ".join(self.backends) if self.backends else " · 未检测到后端"
        return f"{self.path.name}{suffix}"


@dataclass
class ResourceScan:
    root: Path
    runtimes: list[RuntimeCandidate] = field(default_factory=list)
    model_dirs: list[Path] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    @property
    def all_models(self) -> list[str]:
        names: set[str] = set()
        for runtime in self.runtimes:
            names.update(runtime.models)
        for model_dir in self.model_dirs:
            names.update(path.name for path in model_dir.iterdir() if path.is_dir() and (path / "config.json").exists())
        return sorted(names)


def _python_path(runtime: Path) -> Path:
    return runtime / "python" / ("python.exe" if os.name == "nt" else "python")


def _model_dirs(root: Path, runtimes: list[Path]) -> list[Path]:
    candidates = [root / "models"]
    for runtime in runtimes:
        candidates.extend([runtime / "models", runtime.parent / "models"])
    # A common layout is <bundle>/runtime and <bundle>/runtime_cuda sharing
    # <bundle>/runtime/models; include it even when the selected root is one
    # of the runtime directories.
    candidates.extend([root.parent / "runtime" / "models", root.parent / "runtime_cuda" / "models"])
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        key = os.path.normcase(str(candidate))
        if key in seen or not candidate.is_dir():
            continue
        if not any(path.is_dir() and (path / "config.json").exists() for path in candidate.iterdir()):
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _find_runtime_dirs(root: Path) -> list[Path]:
    candidates = [root, root / "runtime", root / "runtime_cuda", root / "runtime_rocm", root / "runtime_cpu"]
    try:
        candidates.extend(path.parent for path in root.rglob("tts_server.py"))
    except OSError:
        pass
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        key = os.path.normcase(str(candidate))
        if key in seen or not (candidate / "tts_server.py").is_file() or not _python_path(candidate).is_file():
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _script_backends(server_script: Path) -> list[str]:
    try:
        source = server_script.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    match = re.search(r"choices\s*=\s*\(([^)]*)\)", source)
    if not match:
        return []
    return [item for item in re.findall(r"['\"]([a-z0-9_]+)['\"]", match.group(1)) if item in {"cuda", "rocm", "cpu"}]


def _probe_torch(python_exe: Path) -> tuple[list[str], str]:
    code = (
        "import json\n"
        "try:\n import torch\n "
        "p={'torch':True,'cuda':bool(torch.cuda.is_available()),'cuda_version':getattr(torch.version,'cuda',None),'hip_version':getattr(torch.version,'hip',None)}\n"
        "except Exception as e:\n p={'torch':False,'error':str(e)}\n"
        "print(json.dumps(p))"
    )
    try:
        completed = subprocess.run([str(python_exe), "-c", code], capture_output=True, text=True, timeout=6, check=False)
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        payload = json.loads(lines[-1]) if lines else {}
    except Exception as exc:
        return [], f"Torch 检测失败：{exc}"
    if not payload.get("torch"):
        return [], f"Torch 不可用：{payload.get('error', '未知错误')}"
    backends = ["cpu"]
    if payload.get("cuda"):
        if payload.get("hip_version"):
            backends.append("rocm")
        elif payload.get("cuda_version"):
            backends.append("cuda")
    detail = f"Torch 可用；CUDA={payload.get('cuda_version') or '无'}；ROCm={payload.get('hip_version') or '无'}"
    return backends, detail


def scan_resource_root(root_value: str | Path) -> ResourceScan:
    root = Path(root_value).expanduser().resolve()
    scan = ResourceScan(root=root)
    if not root.is_dir():
        scan.messages.append("选择的资源根目录不存在")
        return scan
    runtime_dirs = _find_runtime_dirs(root)
    scan.model_dirs = _model_dirs(root, runtime_dirs)
    common_models = scan.model_dirs[0] if scan.model_dirs else None
    for runtime_dir in runtime_dirs:
        model_dir = runtime_dir / "models"
        if not model_dir.is_dir() or not any(model_dir.glob("*/config.json")):
            model_dir = common_models
        models = []
        if model_dir and model_dir.is_dir():
            models = sorted(path.name for path in model_dir.iterdir() if path.is_dir() and (path / "config.json").exists())
        backends, detail = _probe_torch(_python_path(runtime_dir))
        script_backends = _script_backends(runtime_dir / "tts_server.py")
        if not backends:
            backends = script_backends
            detail = detail + ("；脚本声明支持：" + ", ".join(script_backends) if script_backends else "")
        scan.runtimes.append(RuntimeCandidate(runtime_dir, _python_path(runtime_dir), runtime_dir / "tts_server.py", model_dir, models, backends, detail, bool(models and backends)))
    if not scan.runtimes:
        scan.messages.append("未找到包含 python 和 tts_server.py 的可用运行时")
    if not scan.model_dirs:
        scan.messages.append("未找到包含模型 config.json 的 models 目录")
    return scan
