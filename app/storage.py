import hashlib
import json
import os
import re
import sqlite3
import sys
import shutil
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .text_processing import split_text_for_reading


PROJECT_DIR = Path(__file__).resolve().parent.parent
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else PROJECT_DIR
DATA_DIR = Path(os.environ.get("PDF_LISTEN_DATA_DIR", RUNTIME_DIR / "data"))
PAPERS_DIR = DATA_DIR / "papers"
TEXT_DIR = DATA_DIR / "text"
AUDIO_DIR = DATA_DIR / "audio"
VOICES_DIR = DATA_DIR / "voices"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_SETTINGS_PATH = DATA_DIR / "backup_settings.json"
DB_PATH = DATA_DIR / "library.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _title_from_filename(filename: str) -> str:
    title = re.sub(r"\.[^.]+$", "", filename).strip()
    return title or "未命名论文"


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_storage() -> None:
    for directory in (PAPERS_DIR, TEXT_DIR, AUDIO_DIR, VOICES_DIR, BACKUP_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS folders (
                id TEXT PRIMARY KEY,
                parent_id TEXT REFERENCES folders(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(parent_id, name)
            );

            CREATE TABLE IF NOT EXISTS papers (
                id TEXT PRIMARY KEY,
                folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                original_pdf_path TEXT NOT NULL,
                page_count INTEGER NOT NULL DEFAULT 0,
                is_favorite INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'imported',
                raw_text_version_id TEXT,
                active_text_version_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS text_versions (
                id TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                version_type TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                llm_model TEXT,
                prompt_version TEXT,
                source_version_id TEXT,
                display_name TEXT,
                provider TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS text_segments (
                id TEXT PRIMARY KEY,
                text_version_id TEXT NOT NULL REFERENCES text_versions(id) ON DELETE CASCADE,
                stable_key TEXT NOT NULL,
                segment_index INTEGER NOT NULL,
                section_path TEXT,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(text_version_id, stable_key)
            );

            CREATE TABLE IF NOT EXISTS audio_assets (
                id TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                text_version_id TEXT NOT NULL REFERENCES text_versions(id) ON DELETE CASCADE,
                segment_id TEXT REFERENCES text_segments(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                voice_id TEXT NOT NULL,
                speed REAL NOT NULL DEFAULT 1.0,
                text_hash TEXT NOT NULL,
                file_path TEXT NOT NULL,
                duration_ms INTEGER,
                status TEXT NOT NULL DEFAULT 'ready',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audio_manifests (
                id TEXT PRIMARY KEY,
                paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                text_version_id TEXT NOT NULL REFERENCES text_versions(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                voice_id TEXT NOT NULL,
                full_audio_path TEXT NOT NULL,
                total_duration_ms INTEGER,
                segments_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0,
                message TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS integration_sync (
                paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
                text_version_id TEXT,
                remote_document_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS integration_deletions (
                paper_id TEXT PRIMARY KEY,
                remote_document_id TEXT,
                deleted_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_papers_folder ON papers(folder_id);
            CREATE INDEX IF NOT EXISTS idx_papers_updated ON papers(updated_at);
            CREATE INDEX IF NOT EXISTS idx_text_versions_paper ON text_versions(paper_id);
            CREATE INDEX IF NOT EXISTS idx_text_segments_version ON text_segments(text_version_id);
            CREATE INDEX IF NOT EXISTS idx_audio_paper ON audio_assets(paper_id);
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "request_json" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN request_json TEXT")
        paper_columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
        if "is_favorite" not in paper_columns:
            conn.execute("ALTER TABLE papers ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
        text_version_columns = {row[1] for row in conn.execute("PRAGMA table_info(text_versions)").fetchall()}
        for column, definition in (
            ("source_version_id", "TEXT"),
            ("display_name", "TEXT"),
            ("provider", "TEXT"),
            ("kb_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("kb_title", "TEXT"),
            ("kb_authors", "TEXT"),
            ("kb_abstract", "TEXT"),
            ("kb_source", "TEXT"),
        ):
            if column not in text_version_columns:
                conn.execute(f"ALTER TABLE text_versions ADD COLUMN {column} {definition}")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS job_logs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id, created_at);
            CREATE TABLE IF NOT EXISTS resource_protection (
                resource_key TEXT PRIMARY KEY,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                protected INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recycle_bin (
                id TEXT PRIMARY KEY,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                paper_id TEXT,
                label TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                trash_path TEXT,
                deleted_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_recycle_bin_expires ON recycle_bin(expires_at);
            """
        )


def _resource_key(resource_type: str, resource_id: str) -> str:
    return f"{resource_type}:{resource_id}"


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _resource_default_protected(resource_type: str, item: dict, latest_backup: str = "") -> bool:
    if resource_type == "text":
        return item.get("version_type") not in {"raw", "organized"}
    if resource_type == "job":
        return item.get("status") in {"queued", "running", "paused", "cancelling"}
    if resource_type == "backup":
        return item.get("id") == latest_backup
    return True


def purge_expired_recycle_bin() -> int:
    now = _utc_now()
    removed = 0
    with connection() as conn:
        rows = conn.execute("SELECT id, trash_path FROM recycle_bin WHERE expires_at <= ?", (now,)).fetchall()
        for row in rows:
            trash_path = Path(row["trash_path"]) if row["trash_path"] else None
            if trash_path and trash_path.exists():
                shutil.rmtree(trash_path, ignore_errors=True)
            conn.execute("DELETE FROM recycle_bin WHERE id=?", (row["id"],))
            removed += 1
    return removed


def list_recycle_bin() -> list[dict]:
    purge_expired_recycle_bin()
    with connection() as conn:
        rows = conn.execute("SELECT * FROM recycle_bin ORDER BY deleted_at DESC").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        remaining_seconds = (datetime.fromisoformat(item["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
        item["days_left"] = max(0, int((remaining_seconds + 86399) // 86400))
        result.append(item)
    return result


def list_resource_items() -> list[dict]:
    purge_expired_recycle_bin()
    items: list[dict] = []
    with connection() as conn:
        papers = {row["id"]: dict(row) for row in conn.execute("SELECT id, title FROM papers").fetchall()}
        text_rows = conn.execute(
            "SELECT tv.*, p.title AS paper_title FROM text_versions tv JOIN papers p ON p.id=tv.paper_id "
            "ORDER BY tv.created_at DESC"
        ).fetchall()
        audio_rows = conn.execute(
            "SELECT am.*, tv.version_type, tv.display_name AS text_version_name, p.title AS paper_title "
            "FROM audio_manifests am JOIN papers p ON p.id=am.paper_id "
            "LEFT JOIN text_versions tv ON tv.id=am.text_version_id ORDER BY am.created_at DESC"
        ).fetchall()
        job_rows = conn.execute(
            "SELECT j.*, p.title AS paper_title, COUNT(jl.id) AS log_count "
            "FROM jobs j LEFT JOIN papers p ON p.id=j.paper_id LEFT JOIN job_logs jl ON jl.job_id=j.id "
            "GROUP BY j.id ORDER BY j.created_at DESC"
        ).fetchall()
        protection = {
            row["resource_key"]: bool(row["protected"])
            for row in conn.execute("SELECT resource_key, protected FROM resource_protection").fetchall()
        }

    for row in text_rows:
        item = dict(row)
        item.update({
            "id": item["id"], "resource_type": "text", "resource_id": item["id"],
            "label": text_version_label(item), "paper_title": item["paper_title"],
            "size": len((item.get("content") or "").encode("utf-8")), "created_at": item["created_at"],
        })
        default = _resource_default_protected("text", item)
        item["default_protected"] = default
        item["protected"] = protection.get(_resource_key("text", item["id"]), default)
        item["can_delete"] = not item["protected"]
        item.pop("content", None)
        items.append(item)

    for row in audio_rows:
        item = dict(row)
        package_dir = AUDIO_DIR / item["paper_id"] / item["id"]
        item.update({
            "resource_type": "audio", "resource_id": item["id"],
            "label": f"{item.get('text_version_name') or text_version_label(item)} · {item.get('model') or '语音包'}",
            "size": _path_size(package_dir), "created_at": item["created_at"],
        })
        default = _resource_default_protected("audio", item)
        item["default_protected"] = default
        item["protected"] = protection.get(_resource_key("audio", item["id"]), default)
        item["can_delete"] = not item["protected"]
        item.pop("segments_json", None)
        items.append(item)

    for row in job_rows:
        item = dict(row)
        item.update({
            "resource_type": "job", "resource_id": item["id"],
            "label": f"{item.get('paper_title') or '未关联论文'} · {item.get('job_type') or '后台任务'}",
            "size": 0, "created_at": item["created_at"],
        })
        default = _resource_default_protected("job", item)
        item["default_protected"] = default
        item["protected"] = protection.get(_resource_key("job", item["id"]), default)
        item["can_delete"] = not item["protected"] and not default
        item.pop("request_json", None)
        items.append(item)

    backup_paths = sorted(BACKUP_DIR.glob("*.zip"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    latest_backup = backup_paths[0].name if backup_paths else ""
    for path in backup_paths:
        item = {"id": path.name, "resource_type": "backup", "resource_id": path.name, "label": path.name,
                "size": _path_size(path), "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
        default = _resource_default_protected("backup", item, latest_backup)
        item["default_protected"] = default
        item["protected"] = protection.get(_resource_key("backup", path.name), default)
        item["can_delete"] = not item["protected"]
        items.append(item)

    for voice_dir in sorted((path for path in VOICES_DIR.iterdir() if path.is_dir()), key=lambda path: path.name):
        metadata_path = voice_dir / "metadata.json"
        metadata = {}
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        item = {"id": voice_dir.name, "resource_type": "voice", "resource_id": voice_dir.name,
                "label": metadata.get("name") or voice_dir.name, "size": _path_size(voice_dir),
                "created_at": datetime.fromtimestamp(voice_dir.stat().st_mtime, timezone.utc).isoformat()}
        default = _resource_default_protected("voice", item)
        item["default_protected"] = default
        item["protected"] = protection.get(_resource_key("voice", voice_dir.name), default)
        item["can_delete"] = not item["protected"]
        items.append(item)
    return items


def list_voice_clones(target_model: str | None = None) -> list[dict]:
    """List successfully enrolled local clone sources, optionally for one target model."""
    result: list[dict] = []
    if not VOICES_DIR.exists():
        return result
    for voice_dir in VOICES_DIR.iterdir():
        if not voice_dir.is_dir():
            continue
        metadata_path = voice_dir / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        remote_voice = str(metadata.get("voice") or "").strip()
        model = str(metadata.get("target_model") or "").strip()
        if not remote_voice or (target_model and model != target_model):
            continue
        result.append({
            "id": remote_voice,
            "local_id": voice_dir.name,
            "name": str(metadata.get("name") or remote_voice),
            "target_model": model,
            "created_at": datetime.fromtimestamp(voice_dir.stat().st_mtime, timezone.utc).isoformat(),
        })
    return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)


def set_resource_protection(resource_type: str, resource_id: str, protected: bool) -> dict:
    allowed = {"text", "audio", "job", "backup", "voice"}
    if resource_type not in allowed:
        raise ValueError("不支持的资源类型")
    match = next((item for item in list_resource_items() if item["resource_type"] == resource_type and item["resource_id"] == resource_id), None)
    if not match:
        raise ValueError("资源不存在")
    with connection() as conn:
        conn.execute(
            "INSERT INTO resource_protection(resource_key, resource_type, resource_id, protected, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(resource_key) DO UPDATE SET protected=excluded.protected, updated_at=excluded.updated_at",
            (_resource_key(resource_type, resource_id), resource_type, resource_id, int(protected), _utc_now()),
        )
    match["protected"] = bool(protected)
    match["can_delete"] = not bool(protected)
    return match


def _snapshot_row(row: sqlite3.Row | dict | None) -> dict | None:
    return dict(row) if row else None


def delete_resource(resource_type: str, resource_id: str) -> dict:
    item = next((entry for entry in list_resource_items() if entry["resource_type"] == resource_type and entry["resource_id"] == resource_id), None)
    if not item:
        raise ValueError("资源不存在")
    if item["protected"]:
        raise PermissionError("该资源受保护，请先点击保护按钮解锁")
    if resource_type == "job" and item.get("status") in {"queued", "running", "paused", "cancelling"}:
        raise ValueError("运行中的后台任务不能清理")
    entry_id = _new_id()
    trash_dir = DATA_DIR / "recycle_bin" / entry_id
    trash_dir.mkdir(parents=True, exist_ok=False)
    payload: dict = {"item": item}
    moved: list[tuple[Path, Path]] = []
    try:
        with connection() as conn:
            if resource_type == "text":
                version = conn.execute("SELECT * FROM text_versions WHERE id=?", (resource_id,)).fetchone()
                segments = conn.execute("SELECT * FROM text_segments WHERE text_version_id=? ORDER BY segment_index", (resource_id,)).fetchall()
                linked_audio = conn.execute("SELECT COUNT(*) FROM audio_manifests WHERE text_version_id=?", (resource_id,)).fetchone()[0]
                if linked_audio:
                    raise ValueError("该文本版本仍有语音包，请先清理关联语音包")
                paper = conn.execute("SELECT * FROM papers WHERE id=?", (version["paper_id"],)).fetchone() if version else None
                payload.update({"version": _snapshot_row(version), "segments": [_snapshot_row(row) for row in segments], "paper_pointers": _snapshot_row(paper)})
                conn.execute("DELETE FROM text_versions WHERE id=?", (resource_id,))
                if paper and (paper["active_text_version_id"] == resource_id or paper["raw_text_version_id"] == resource_id):
                    replacement = conn.execute("SELECT id FROM text_versions WHERE paper_id=? ORDER BY created_at DESC LIMIT 1", (paper["id"],)).fetchone()
                    conn.execute("UPDATE papers SET active_text_version_id=?, raw_text_version_id=? WHERE id=?",
                                 (replacement["id"] if replacement and paper["active_text_version_id"] == resource_id else paper["active_text_version_id"],
                                  replacement["id"] if replacement and paper["raw_text_version_id"] == resource_id else paper["raw_text_version_id"], paper["id"]))
            elif resource_type == "audio":
                manifest = conn.execute("SELECT * FROM audio_manifests WHERE id=?", (resource_id,)).fetchone()
                assets = conn.execute("SELECT * FROM audio_assets WHERE file_path LIKE ?", (f"%{resource_id}%",)).fetchall()
                payload.update({"manifest": _snapshot_row(manifest), "assets": [_snapshot_row(row) for row in assets]})
                conn.execute("DELETE FROM audio_manifests WHERE id=?", (resource_id,))
                conn.execute("DELETE FROM audio_assets WHERE file_path LIKE ?", (f"%{resource_id}%",))
                source = AUDIO_DIR / item.get("paper_id", "") / resource_id
                if source.exists():
                    target = trash_dir / "package"
                    shutil.move(str(source), str(target))
                    moved.append((source, target))
            elif resource_type == "job":
                job = conn.execute("SELECT * FROM jobs WHERE id=?", (resource_id,)).fetchone()
                logs = conn.execute("SELECT * FROM job_logs WHERE job_id=? ORDER BY created_at", (resource_id,)).fetchall()
                payload.update({"job": _snapshot_row(job), "logs": [_snapshot_row(row) for row in logs]})
                conn.execute("DELETE FROM jobs WHERE id=?", (resource_id,))
            elif resource_type == "backup":
                source = BACKUP_DIR / resource_id
                if not source.exists():
                    raise ValueError("备份文件不存在")
                target = trash_dir / source.name
                shutil.move(str(source), str(target))
                moved.append((source, target))
            elif resource_type == "voice":
                source = VOICES_DIR / resource_id
                if not source.exists():
                    raise ValueError("音源不存在")
                target = trash_dir / source.name
                shutil.move(str(source), str(target))
                moved.append((source, target))
            expires = datetime.now(timezone.utc).timestamp() + 30 * 86400
            conn.execute(
                "INSERT INTO recycle_bin(id, resource_type, resource_id, paper_id, label, payload_json, trash_path, deleted_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, resource_type, resource_id, item.get("paper_id"), item.get("label") or resource_id,
                 json.dumps(payload, ensure_ascii=False), str(trash_dir), _utc_now(), datetime.fromtimestamp(expires, timezone.utc).isoformat()),
            )
        return {"id": entry_id, "resource_type": resource_type, "resource_id": resource_id, "status": "recycled", "expires_in_days": 30}
    except Exception:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                shutil.move(str(target), str(source))
        if trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
        raise


def delete_cleanable_resources() -> dict:
    """Move every currently unlocked, safe-to-delete resource into the recycle bin."""
    candidates = [item for item in list_resource_items() if item.get("can_delete")]
    recycled = []
    skipped = []
    for item in candidates:
        try:
            recycled.append(delete_resource(item["resource_type"], item["resource_id"]))
        except Exception as exc:
            skipped.append({
                "resource_type": item["resource_type"],
                "resource_id": item["resource_id"],
                "label": item.get("label") or item["resource_id"],
                "reason": str(exc),
            })
    return {"recycled": recycled, "skipped": skipped, "count": len(recycled)}


def restore_recycle_item(entry_id: str) -> dict:
    purge_expired_recycle_bin()
    with connection() as conn:
        row = conn.execute("SELECT * FROM recycle_bin WHERE id=?", (entry_id,)).fetchone()
    if not row:
        raise ValueError("回收站项目不存在或已过期")
    entry = dict(row)
    payload = json.loads(entry["payload_json"])
    resource_type = entry["resource_type"]
    resource_id = entry["resource_id"]
    trash_dir = Path(entry["trash_path"]) if entry["trash_path"] else None
    try:
        with connection() as conn:
            if resource_type == "text":
                version = payload.get("version")
                if not version or conn.execute("SELECT 1 FROM text_versions WHERE id=?", (version["id"],)).fetchone():
                    raise ValueError("文本版本已存在，无法恢复")
                version_keys = ("id", "paper_id", "version_type", "content", "content_hash", "llm_model", "prompt_version", "source_version_id", "display_name", "provider", "kb_enabled", "kb_title", "kb_authors", "kb_abstract", "kb_source", "created_at")
                conn.execute(
                    "INSERT INTO text_versions(id, paper_id, version_type, content, content_hash, llm_model, prompt_version, source_version_id, display_name, provider, kb_enabled, kb_title, kb_authors, kb_abstract, kb_source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    tuple(version.get(key) for key in version_keys),
                )
                for segment in payload.get("segments", []):
                    conn.execute("INSERT INTO text_segments VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tuple(segment.get(key) for key in ("id", "text_version_id", "stable_key", "segment_index", "section_path", "content", "content_hash", "created_at")))
                original_paper = payload.get("paper_pointers") or {}
                if original_paper.get("id") and conn.execute("SELECT 1 FROM papers WHERE id=?", (original_paper["id"],)).fetchone():
                    active_id = original_paper.get("active_text_version_id")
                    raw_id = original_paper.get("raw_text_version_id")
                    if active_id == version["id"] or (active_id and conn.execute("SELECT 1 FROM text_versions WHERE id=?", (active_id,)).fetchone()):
                        active_value = active_id
                    else:
                        active_value = None
                    if raw_id == version["id"] or (raw_id and conn.execute("SELECT 1 FROM text_versions WHERE id=?", (raw_id,)).fetchone()):
                        raw_value = raw_id
                    else:
                        raw_value = None
                    conn.execute("UPDATE papers SET active_text_version_id=?, raw_text_version_id=? WHERE id=?", (active_value, raw_value, original_paper["id"]))
            elif resource_type == "audio":
                manifest = payload.get("manifest")
                if not manifest or conn.execute("SELECT 1 FROM audio_manifests WHERE id=?", (manifest["id"],)).fetchone():
                    raise ValueError("语音包已存在，无法恢复")
                conn.execute("INSERT INTO audio_manifests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(manifest.get(key) for key in ("id", "paper_id", "text_version_id", "provider", "model", "voice_id", "full_audio_path", "total_duration_ms", "segments_json", "created_at")))
                for asset in payload.get("assets", []):
                    conn.execute("INSERT INTO audio_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(asset.get(key) for key in ("id", "paper_id", "text_version_id", "segment_id", "provider", "model", "voice_id", "speed", "text_hash", "file_path", "duration_ms", "status", "created_at")))
            elif resource_type == "job":
                job = payload.get("job")
                if not job or conn.execute("SELECT 1 FROM jobs WHERE id=?", (job["id"],)).fetchone():
                    raise ValueError("任务记录已存在，无法恢复")
                job_keys = ("id", "paper_id", "job_type", "status", "progress", "message", "error_message", "created_at", "started_at", "finished_at", "request_json")
                conn.execute("INSERT INTO jobs(id, paper_id, job_type, status, progress, message, error_message, created_at, started_at, finished_at, request_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(job.get(key) for key in job_keys))
                for log in payload.get("logs", []):
                    conn.execute("INSERT INTO job_logs VALUES (?, ?, ?, ?, ?)", tuple(log.get(key) for key in ("id", "job_id", "level", "message", "created_at")))
        if resource_type == "backup":
            source = trash_dir / resource_id if trash_dir else None
            target = BACKUP_DIR / resource_id
            if target.exists():
                raise ValueError("备份文件已存在，无法恢复")
            if source and source.exists():
                shutil.move(str(source), str(target))
        elif resource_type == "voice":
            source = trash_dir / resource_id if trash_dir else None
            target = VOICES_DIR / resource_id
            if target.exists():
                raise ValueError("音源已存在，无法恢复")
            if source and source.exists():
                shutil.move(str(source), str(target))
        elif resource_type == "audio":
            source = trash_dir / "package" if trash_dir else None
            target = AUDIO_DIR / payload["manifest"]["paper_id"] / resource_id
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("语音包文件已存在，无法恢复")
            if source and source.exists():
                shutil.move(str(source), str(target))
        if trash_dir and trash_dir.exists():
            shutil.rmtree(trash_dir, ignore_errors=True)
        with connection() as conn:
            conn.execute("DELETE FROM recycle_bin WHERE id=?", (entry_id,))
        return {"id": entry_id, "status": "restored"}
    except Exception:
        raise


def permanently_delete_recycle_item(entry_id: str) -> dict:
    with connection() as conn:
        row = conn.execute("SELECT trash_path FROM recycle_bin WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise ValueError("回收站项目不存在")
        conn.execute("DELETE FROM recycle_bin WHERE id=?", (entry_id,))
    if row["trash_path"]:
        shutil.rmtree(Path(row["trash_path"]), ignore_errors=True)
    return {"id": entry_id, "status": "purged"}


def get_backup_settings() -> dict:
    default = {"interval_days": 30, "last_backup_at": ""}
    if not BACKUP_SETTINGS_PATH.exists():
        return default
    try:
        data = json.loads(BACKUP_SETTINGS_PATH.read_text(encoding="utf-8"))
        interval = int(data.get("interval_days", 30))
        return {"interval_days": max(interval, 0), "last_backup_at": str(data.get("last_backup_at", "") or "")}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default


def update_backup_settings(interval_days: int | None = None, last_backup_at: str | None = None) -> dict:
    current = get_backup_settings()
    if interval_days is not None:
        current["interval_days"] = max(int(interval_days), 0)
    if last_backup_at is not None:
        current["last_backup_at"] = last_backup_at
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def backup_library(label: str = "manual") -> Path:
    """Archive the complete library, excluding older backups to avoid recursion."""
    init_storage()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = BACKUP_DIR / f"paper-library-{label}-{stamp}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in DATA_DIR.rglob("*"):
            if not path.is_file() or BACKUP_DIR in path.parents:
                continue
            if path.name.endswith(("-wal", "-shm")) or path.name.startswith("."):
                continue
            archive.write(path, path.relative_to(DATA_DIR).as_posix())
    update_backup_settings(last_backup_at=datetime.now(timezone.utc).isoformat())
    return archive_path


def restore_library(archive_path: Path) -> dict:
    """Safely replace the current library with a validated backup archive."""
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = archive.infolist()
        for member in members:
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("备份文件包含不安全的路径")
        with tempfile.TemporaryDirectory(prefix="pdf-library-restore-") as temp_dir:
            extract_root = Path(temp_dir).resolve()
            for member in members:
                target = (extract_root / member.filename).resolve()
                if extract_root not in target.parents and target != extract_root:
                    raise ValueError("备份文件包含不安全的路径")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
            source_root = extract_root
            if not (source_root / "library.db").exists():
                candidates = [child for child in source_root.iterdir() if child.is_dir() and (child / "library.db").exists()]
                if len(candidates) == 1:
                    source_root = candidates[0]
            if not (source_root / "library.db").exists():
                raise ValueError("不是有效的论文库备份文件")

            safety_backup = backup_library("pre-restore")
            for child in DATA_DIR.iterdir():
                if child == BACKUP_DIR or child == extract_root:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            for child in source_root.iterdir():
                destination = DATA_DIR / child.name
                if child.is_dir():
                    shutil.copytree(child, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(child, destination)
    init_storage()
    return {"status": "restored", "safety_backup": safety_backup.name}


def list_folders() -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, parent_id, name, sort_order, created_at, updated_at "
            "FROM folders ORDER BY parent_id, sort_order, name"
        ).fetchall()
    return [dict(row) for row in rows]


def create_folder(name: str, parent_id: str | None = None) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("文件夹名称不能为空")
    folder_id = _new_id()
    now = _utc_now()
    with connection() as conn:
        if parent_id and not conn.execute("SELECT 1 FROM folders WHERE id=?", (parent_id,)).fetchone():
            raise ValueError("父文件夹不存在")
        conn.execute(
            "INSERT INTO folders(id, parent_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (folder_id, parent_id, name, now, now),
        )
    return {"id": folder_id, "parent_id": parent_id, "name": name, "created_at": now, "updated_at": now}


def update_folder(folder_id: str, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("文件夹名称不能为空")
    now = _utc_now()
    with connection() as conn:
        result = conn.execute("UPDATE folders SET name=?, updated_at=? WHERE id=?", (name, now, folder_id))
        if result.rowcount == 0:
            raise ValueError("文件夹不存在")
        row = conn.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
    return dict(row)


def delete_folder(folder_id: str) -> None:
    paper_ids: list[str] = []
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone():
            raise ValueError("文件夹不存在")
        folder_ids = [folder_id]
        cursor = 0
        while cursor < len(folder_ids):
            children = conn.execute(
                "SELECT id FROM folders WHERE parent_id=?", (folder_ids[cursor],)
            ).fetchall()
            folder_ids.extend(row[0] for row in children)
            cursor += 1

        placeholders = ",".join("?" for _ in folder_ids)
        paper_ids = [
            row[0]
            for row in conn.execute(
                f"SELECT id FROM papers WHERE folder_id IN ({placeholders})", folder_ids
            ).fetchall()
        ]
        if paper_ids:
            paper_placeholders = ",".join("?" for _ in paper_ids)
            conn.execute(
                "INSERT OR REPLACE INTO integration_deletions(paper_id, remote_document_id, deleted_at) "
                "SELECT p.id, s.remote_document_id, ? FROM papers p "
                "LEFT JOIN integration_sync s ON s.paper_id=p.id WHERE p.id IN (" + paper_placeholders + ")",
                [_utc_now(), *paper_ids],
            )
            conn.execute(f"DELETE FROM papers WHERE id IN ({paper_placeholders})", paper_ids)
        conn.execute(f"DELETE FROM folders WHERE id IN ({placeholders})", folder_ids)

    import shutil

    for paper_id in paper_ids:
        for resource_dir in (PAPERS_DIR / paper_id, AUDIO_DIR / paper_id):
            if resource_dir.exists():
                shutil.rmtree(resource_dir)


def _insert_text_segments(conn: sqlite3.Connection, version_id: str, content: str, now: str) -> None:
    for index, chunk in enumerate(split_text_for_reading(content)):
        chunk_text = chunk["text"]
        stable_key = _hash_text(chunk_text)[:24]
        conn.execute(
            "INSERT INTO text_segments(id, text_version_id, stable_key, segment_index, content, content_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_new_id(), version_id, f"{index}-{stable_key}", index, chunk_text, _hash_text(chunk_text), now),
        )


def _insert_text_version(
    conn: sqlite3.Connection,
    paper_id: str,
    content: str,
    version_type: str,
    source_version_id: str | None = None,
    display_name: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    kb_enabled: bool = False,
    kb_title: str | None = None,
    kb_authors: str | None = None,
    kb_abstract: str | None = None,
    kb_source: str | None = None,
) -> str:
    version_id = _new_id()
    now = _utc_now()
    conn.execute(
        "INSERT INTO text_versions(id, paper_id, version_type, content, content_hash, llm_model, "
        "source_version_id, display_name, provider, kb_enabled, kb_title, kb_authors, kb_abstract, kb_source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (version_id, paper_id, version_type, content, _hash_text(content), model, source_version_id, display_name, provider,
         int(kb_enabled), kb_title, kb_authors, kb_abstract, kb_source, now),
    )
    _insert_text_segments(conn, version_id, content, now)
    return version_id


def create_paper(
    filename: str,
    pdf_path: Path,
    page_count: int,
    raw_text: str,
    folder_id: str | None = None,
    paper_id: str | None = None,
    title: str | None = None,
) -> dict:
    paper_id = paper_id or _new_id()
    now = _utc_now()
    with connection() as conn:
        if folder_id and not conn.execute("SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone():
            raise ValueError("文件夹不存在")
        conn.execute(
            "INSERT INTO papers(id, folder_id, title, original_filename, original_pdf_path, page_count, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'needs_review', ?, ?)",
            (paper_id, folder_id, (title or _title_from_filename(filename)).strip() or _title_from_filename(filename), filename, str(pdf_path), page_count, now, now),
        )
        raw_version_id = _insert_text_version(conn, paper_id, raw_text, "raw")
        conn.execute(
            "UPDATE papers SET raw_text_version_id=?, active_text_version_id=?, updated_at=? WHERE id=?",
            (raw_version_id, raw_version_id, now, paper_id),
        )
    return get_paper(paper_id)  # type: ignore[return-value]


def update_paper(paper_id: str, title: str | None = None, is_favorite: bool | None = None) -> dict:
    fields = []
    values: list[object] = []
    if title is not None:
        normalized = title.strip()
        if not normalized:
            raise ValueError("论文标题不能为空")
        fields.append("title=?")
        values.append(normalized)
    if is_favorite is not None:
        fields.append("is_favorite=?")
        values.append(1 if is_favorite else 0)
    if not fields:
        paper = get_paper(paper_id)
        if not paper:
            raise ValueError("论文不存在")
        return paper
    values.extend([_utc_now(), paper_id])
    with connection() as conn:
        result = conn.execute(f"UPDATE papers SET {', '.join(fields)}, updated_at=? WHERE id=?", values)
        if result.rowcount == 0:
            raise ValueError("论文不存在")
    return get_paper(paper_id)  # type: ignore[return-value]


def list_papers(folder_id: str | None = None, search: str | None = None) -> list[dict]:
    query = """
        SELECT p.*, f.name AS folder_name,
               EXISTS(SELECT 1 FROM audio_manifests am WHERE am.paper_id=p.id) AS audio_ready
        FROM papers p LEFT JOIN folders f ON f.id=p.folder_id
        WHERE p.status != 'deleted'
    """
    params: list[str] = []
    if folder_id:
        query += " AND p.folder_id=?"
        params.append(folder_id)
    if search:
        query += " AND (p.title LIKE ? OR p.original_filename LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    query += " ORDER BY p.updated_at DESC"
    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_paper(paper_id: str) -> dict | None:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT p.*, f.name AS folder_name,
                   EXISTS(SELECT 1 FROM audio_manifests am WHERE am.paper_id=p.id) AS audio_ready
            FROM papers p LEFT JOIN folders f ON f.id=p.folder_id
            WHERE p.id=?
            """,
            (paper_id,),
        ).fetchone()
    return dict(row) if row else None


def get_paper_text(paper_id: str) -> dict | None:
    with connection() as conn:
        active_id = conn.execute("SELECT active_text_version_id FROM papers WHERE id=?", (paper_id,)).fetchone()
    if not active_id or not active_id[0]:
        return None
    return get_paper_text_version(paper_id, active_id[0])


def get_paper_text_version(paper_id: str, version_id: str) -> dict | None:
    with connection() as conn:
        version = conn.execute(
            "SELECT * FROM text_versions WHERE id=? AND paper_id=?",
            (version_id, paper_id),
        ).fetchone()
        if not version:
            return None
        segments = conn.execute(
            "SELECT * FROM text_segments WHERE text_version_id=? ORDER BY segment_index",
            (version["id"],),
        ).fetchall()
    return {"version": dict(version), "segments": [dict(row) for row in segments]}


def get_knowledge_text(paper_id: str) -> dict | None:
    """Return the text version selected for AstrBot knowledge indexing.

    An explicitly marked manual version wins over the latest DeepSeek vision
    version. No other text version is eligible for AstrBot indexing.
    """
    with connection() as conn:
        paper = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not paper:
            return None
        version = conn.execute(
            "SELECT * FROM text_versions WHERE paper_id=? AND version_type IN ('manual', 'edited') AND kb_enabled=1 "
            "ORDER BY created_at DESC LIMIT 1", (paper_id,)
        ).fetchone()
        source = "manual"
        if not version:
            version = conn.execute(
                "SELECT * FROM text_versions WHERE paper_id=? AND version_type='deepseek_vision' "
                "ORDER BY created_at DESC LIMIT 1", (paper_id,)
            ).fetchone()
            source = "deepseek_vision"
        if not version:
            return None
        segments = conn.execute(
            "SELECT * FROM text_segments WHERE text_version_id=? ORDER BY segment_index", (version["id"],)
        ).fetchall()
    version_data = dict(version)
    version_data["label"] = text_version_label(version_data)
    return {
        "source": source,
        "version": version_data,
        "segments": [dict(row) for row in segments],
        "knowledge": {
            "title": version_data.get("kb_title") or str(paper["title"] or ""),
            "authors": version_data.get("kb_authors") or "",
            "abstract": version_data.get("kb_abstract") or "",
            "enabled": bool(version_data.get("kb_enabled")) or source == "deepseek_vision",
        },
    }


_VERSION_LABELS = {
    "raw": "自动识别",
    "organized": "自动识别",
    "edited": "手动导入",
    "manual": "手动导入",
    "deepseek_vision": "读图识别",
    "optimized": "朗读优化",
    "manual_optimized": "手动正文朗读优化",
    "vision_optimized": "读图正文朗读优化",
}

_VERSION_TYPE_ALIASES = {
    "raw": "raw",
    "organized": "raw",
    "edited": "manual",
    "manual": "manual",
    "deepseek_vision": "deepseek_vision",
    "optimized": "optimized",
    "manual_optimized": "optimized",
    "vision_optimized": "optimized",
}


def _canonical_version_type(version_type: str) -> str:
    return _VERSION_TYPE_ALIASES.get(version_type, version_type)


def text_version_label(version: dict) -> str:
    version_type = _canonical_version_type(str(version.get("version_type", "")))
    return str(version.get("display_name") or _VERSION_LABELS.get(version_type, version_type or "文本"))


def list_text_versions(paper_id: str) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT tv.*, p.active_text_version_id, "
            "(SELECT COUNT(*) FROM text_segments ts WHERE ts.text_version_id=tv.id) AS segment_count "
            "FROM text_versions tv JOIN papers p ON p.id=tv.paper_id "
            "WHERE tv.paper_id=? ORDER BY tv.created_at DESC",
            (paper_id,),
        ).fetchall()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        item = dict(row)
        item["is_active"] = item["id"] == item["active_text_version_id"]
        canonical_type = _canonical_version_type(str(item.get("version_type", "")))
        item["canonical_version_type"] = canonical_type
        grouped.setdefault(canonical_type, []).append(item)
    result = []
    for items in grouped.values():
        active = next((item for item in items if item["is_active"]), None)
        item = active or items[0]
        item["version_type"] = item["canonical_version_type"]
        item["label"] = text_version_label(item)
        result.append(item)
    result.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return result


def activate_text_version(paper_id: str, version_id: str) -> dict:
    with connection() as conn:
        row = conn.execute(
            "SELECT id FROM text_versions WHERE id=? AND paper_id=?", (version_id, paper_id)
        ).fetchone()
        if not row:
            raise ValueError("文本版本不存在")
        conn.execute(
            "UPDATE papers SET active_text_version_id=?, status='ready', updated_at=? WHERE id=?",
            (version_id, _utc_now(), paper_id),
        )
    return get_paper_text_version(paper_id, version_id)  # type: ignore[return-value]


def list_integration_papers(
    updated_since: str | None = None,
    folder_id: str | None = None,
) -> list[dict]:
    """Return the small, stable paper payload used by external integrations."""
    query = """
        SELECT p.id, p.folder_id, p.title, p.original_filename, p.page_count,
               p.status, p.active_text_version_id, p.created_at, p.updated_at,
               f.name AS folder_name
        FROM papers p LEFT JOIN folders f ON f.id=p.folder_id
        WHERE p.status != 'deleted'
    """
    params: list[str] = []
    if updated_since:
        query += " AND p.updated_at > ?"
        params.append(updated_since)
    if folder_id:
        query += " AND p.folder_id=?"
        params.append(folder_id)
    query += " ORDER BY p.updated_at DESC"
    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_integration_sync_state(paper_id: str) -> dict:
    with connection() as conn:
        row = conn.execute(
            "SELECT paper_id, text_version_id, remote_document_id, status, error_message, updated_at "
            "FROM integration_sync WHERE paper_id=?",
            (paper_id,),
        ).fetchone()
    return dict(row) if row else {
        "paper_id": paper_id,
        "text_version_id": None,
        "remote_document_id": None,
        "status": "pending",
        "error_message": None,
        "updated_at": None,
    }


def list_integration_deletions(updated_since: str | None = None) -> list[dict]:
    query = "SELECT paper_id, remote_document_id, deleted_at FROM integration_deletions"
    params: list[str] = []
    if updated_since:
        query += " WHERE deleted_at > ?"
        params.append(updated_since)
    query += " ORDER BY deleted_at"
    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def clear_integration_deletion(paper_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM integration_deletions WHERE paper_id=?", (paper_id,))


def save_integration_sync_ack(
    paper_id: str,
    text_version_id: str | None,
    remote_document_id: str | None,
    status: str,
    error_message: str | None = None,
) -> dict:
    now = _utc_now()
    with connection() as conn:
        conn.execute(
            "INSERT INTO integration_sync(paper_id, text_version_id, remote_document_id, status, error_message, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(paper_id) DO UPDATE SET text_version_id=excluded.text_version_id, "
            "remote_document_id=excluded.remote_document_id, status=excluded.status, "
            "error_message=excluded.error_message, updated_at=excluded.updated_at",
            (paper_id, text_version_id, remote_document_id, status, error_message, now),
        )
    return get_integration_sync_state(paper_id)


def update_paper_text(
    paper_id: str,
    content: str,
    version_type: str = "edited",
    source_version_id: str | None = None,
    display_name: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    kb_enabled: bool | None = None,
    kb_title: str | None = None,
    kb_authors: str | None = None,
    kb_abstract: str | None = None,
    kb_source: str | None = None,
) -> dict:
    if not content.strip():
        raise ValueError("文本不能为空")
    canonical_type = _canonical_version_type(version_type)
    if canonical_type not in {"raw", "manual", "deepseek_vision", "optimized"}:
        raise ValueError("不支持的文本版本类型")
    now = _utc_now()
    audio_paths: list[str] = []
    with connection() as conn:
        if not conn.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone():
            raise ValueError("论文不存在")
        type_candidates = {
            "raw": ("raw", "organized"),
            "manual": ("manual", "edited"),
            "deepseek_vision": ("deepseek_vision",),
            "optimized": ("optimized", "manual_optimized", "vision_optimized"),
        }[canonical_type]
        placeholders = ",".join("?" for _ in type_candidates)
        existing = conn.execute(
            f"SELECT * FROM text_versions WHERE paper_id=? AND version_type IN ({placeholders}) "
            "ORDER BY CASE WHEN version_type=? THEN 0 ELSE 1 END, created_at DESC LIMIT 1",
            (paper_id, *type_candidates, canonical_type),
        ).fetchone()
        # A paper has one persistent slot for each of the four user-facing text kinds.
        if existing:
            version_id = existing["id"]
            old_assets = conn.execute(
                "SELECT file_path FROM audio_assets WHERE paper_id=? AND text_version_id=?",
                (paper_id, version_id),
            ).fetchall()
            audio_paths.extend(str(row[0]) for row in old_assets if row[0])
            old_manifests = conn.execute(
                "SELECT id FROM audio_manifests WHERE paper_id=? AND text_version_id=?",
                (paper_id, version_id),
            ).fetchall()
            audio_paths.extend(str(AUDIO_DIR / paper_id / row[0] / "full.mp3") for row in old_manifests)
            conn.execute("DELETE FROM audio_assets WHERE paper_id=? AND text_version_id=?", (paper_id, version_id))
            conn.execute("DELETE FROM audio_manifests WHERE paper_id=? AND text_version_id=?", (paper_id, version_id))
            conn.execute("DELETE FROM text_segments WHERE text_version_id=?", (version_id,))
            conn.execute(
                "UPDATE text_versions SET version_type=?, content=?, content_hash=?, llm_model=?, "
                "source_version_id=?, display_name=?, provider=?, kb_enabled=?, kb_title=?, kb_authors=?, kb_abstract=?, kb_source=?, created_at=? WHERE id=?",
                (canonical_type, content, _hash_text(content), model, source_version_id, display_name, provider,
                 int(existing["kb_enabled"] if kb_enabled is None else kb_enabled),
                 existing["kb_title"] if kb_enabled is None else kb_title,
                 existing["kb_authors"] if kb_enabled is None else kb_authors,
                 existing["kb_abstract"] if kb_enabled is None else kb_abstract,
                 existing["kb_source"] if kb_enabled is None else kb_source, now, version_id),
            )
            _insert_text_segments(conn, version_id, content, now)
        else:
            version_id = _insert_text_version(
                conn, paper_id, content, canonical_type,
                source_version_id=source_version_id,
                display_name=display_name,
                provider=provider,
                model=model,
                kb_enabled=bool(kb_enabled),
                kb_title=kb_title,
                kb_authors=kb_authors,
                kb_abstract=kb_abstract,
                kb_source=kb_source,
            )
        conn.execute(
            "UPDATE papers SET active_text_version_id=?, status='ready', updated_at=? WHERE id=?",
            (version_id, now, paper_id),
        )
    package_dirs = {Path(path).parent for path in audio_paths if path}
    for package_dir in package_dirs:
        if AUDIO_DIR / paper_id in package_dir.parents and package_dir.exists():
            shutil.rmtree(package_dir)
    return get_paper_text(paper_id)  # type: ignore[return-value]


def update_text_knowledge_metadata(
    paper_id: str,
    version_id: str,
    enabled: bool,
    title: str = "",
    authors: str = "",
    abstract: str = "",
    source: str = "manual",
) -> dict:
    if enabled and not title.strip():
        raise ValueError("知识库文本的论文标题不能为空")
    if enabled and not abstract.strip():
        raise ValueError("知识库文本的摘要不能为空")
    with connection() as conn:
        version = conn.execute(
            "SELECT * FROM text_versions WHERE id=? AND paper_id=?", (version_id, paper_id)
        ).fetchone()
        if not version:
            raise ValueError("文本版本不存在")
        if _canonical_version_type(str(version["version_type"])) != "manual":
            raise ValueError("知识库信息补录目前只支持手动导入文本")
        conn.execute(
            "UPDATE text_versions SET kb_enabled=?, kb_title=?, kb_authors=?, kb_abstract=?, kb_source=? WHERE id=?",
            (int(enabled), title.strip() or None, authors.strip() or None, abstract.strip() or None, source or "manual", version_id),
        )
        conn.execute("UPDATE papers SET updated_at=? WHERE id=?", (_utc_now(), paper_id))
    return get_paper_text_version(paper_id, version_id)  # type: ignore[return-value]


def delete_paper(paper_id: str) -> dict:
    paper = get_paper(paper_id)
    if not paper:
        raise ValueError("论文不存在")
    with connection() as conn:
        resource_ids = [row[0] for row in conn.execute(
            "SELECT id FROM text_versions WHERE paper_id=? UNION SELECT id FROM audio_manifests WHERE paper_id=? UNION SELECT id FROM jobs WHERE paper_id=?",
            (paper_id, paper_id, paper_id),
        ).fetchall()]
        conn.execute(
            "INSERT OR REPLACE INTO integration_deletions(paper_id, remote_document_id, deleted_at) "
            "SELECT p.id, s.remote_document_id, ? FROM papers p "
            "LEFT JOIN integration_sync s ON s.paper_id=p.id WHERE p.id=?",
            (_utc_now(), paper_id),
        )
        conn.execute("DELETE FROM papers WHERE id=?", (paper_id,))
        for resource_id in resource_ids:
            conn.execute("DELETE FROM resource_protection WHERE resource_id=?", (resource_id,))

    paper_dir = PAPERS_DIR / paper_id
    if paper_dir.exists():
        import shutil

        shutil.rmtree(paper_dir)
    audio_dir = AUDIO_DIR / paper_id
    if audio_dir.exists():
        import shutil

        shutil.rmtree(audio_dir)
    text_dir = TEXT_DIR / paper_id
    if text_dir.exists():
        shutil.rmtree(text_dir)
    pdf_path = Path(paper["original_pdf_path"])
    if pdf_path.exists():
        pdf_path.unlink()
    return {"paper_id": paper_id, "status": "deleted"}


def clear_audio_assets(paper_id: str, manifest_id: str | None = None) -> None:
    with connection() as conn:
        if manifest_id:
            conn.execute("DELETE FROM audio_assets WHERE paper_id=? AND file_path LIKE ?", (paper_id, f"%{manifest_id}%"))
            conn.execute("DELETE FROM audio_manifests WHERE paper_id=? AND id=?", (paper_id, manifest_id))
        else:
            conn.execute("DELETE FROM audio_assets WHERE paper_id=?", (paper_id,))
            conn.execute("DELETE FROM audio_manifests WHERE paper_id=?", (paper_id,))


def save_audio_asset(
    paper_id: str,
    text_version_id: str,
    segment_id: str,
    provider: str,
    model: str,
    voice_id: str,
    speed: float,
    text_hash: str,
    file_path: str,
    duration_ms: int | None,
) -> str:
    asset_id = _new_id()
    with connection() as conn:
        conn.execute(
            "INSERT INTO audio_assets(id, paper_id, text_version_id, segment_id, provider, model, voice_id, speed, text_hash, file_path, duration_ms, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)",
            (asset_id, paper_id, text_version_id, segment_id, provider, model, voice_id, speed, text_hash, file_path, duration_ms, _utc_now()),
        )
    return asset_id


def save_audio_manifest(
    paper_id: str,
    text_version_id: str,
    provider: str,
    model: str,
    voice_id: str,
    full_audio_path: str,
    total_duration_ms: int | None,
    segments: list[dict],
    manifest_id: str | None = None,
) -> str:
    manifest_id = manifest_id or _new_id()
    with connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO audio_manifests(id, paper_id, text_version_id, provider, model, voice_id, full_audio_path, total_duration_ms, segments_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (manifest_id, paper_id, text_version_id, provider, model, voice_id, full_audio_path, total_duration_ms, json.dumps(segments, ensure_ascii=False), _utc_now()),
        )
        conn.execute("UPDATE papers SET status='audio_ready', updated_at=? WHERE id=?", (_utc_now(), paper_id))
    return manifest_id


def import_audio_package(paper_id: str, zip_path: Path, expected_text_version_id: str) -> dict:
    """Validate and import a bridge-produced audio ZIP for one exact text version."""
    paper = get_paper(paper_id)
    text_data = get_paper_text_version(paper_id, expected_text_version_id)
    if not paper or not text_data:
        raise ValueError("论文或指定文本版本不存在")

    max_uncompressed = 2 * 1024 * 1024 * 1024
    with zipfile.ZipFile(zip_path, "r") as archive:
        infos = archive.infolist()
        if sum(info.file_size for info in infos) > max_uncompressed:
            raise ValueError("音频 ZIP 解压后超过 2 GB")
        names: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            relative = Path(info.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("音频 ZIP 包含不安全路径")
            normalized = relative.as_posix()
            if not normalized or info.is_dir():
                continue
            names[normalized] = info
        if "manifest.json" not in names:
            raise ValueError("音频 ZIP 缺少 manifest.json")
        try:
            manifest = json.loads(archive.read(names["manifest.json"]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("音频 manifest.json 无法读取") from exc
        if manifest.get("schema_version") not in {"pdf-local-tts-1", 1}:
            raise ValueError("不支持的音频 ZIP 协议版本")
        if manifest.get("paper_id") != paper_id:
            raise ValueError("音频 ZIP 与当前论文不匹配")
        if manifest.get("text_version_id") != expected_text_version_id:
            raise ValueError("音频 ZIP 与当前文本版本不匹配")

        local_segments = {segment["id"]: segment for segment in text_data["segments"]}
        manifest_segments = manifest.get("segments") or []
        if not manifest_segments:
            raise ValueError("音频 ZIP 没有可导入的分段")
        seen_indices: set[int] = set()
        prepared_segments: list[dict] = []
        for item in manifest_segments:
            try:
                segment_id = str(item["segment_id"])
                index = int(item["index"])
                content_hash = str(item["content_hash"])
                source_file = Path(str(item.get("file") or item.get("file_path") or ""))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("音频 ZIP 的分段清单格式错误") from exc
            if segment_id not in local_segments or index in seen_indices:
                raise ValueError("音频 ZIP 包含无效或重复的文本分段")
            local_segment = local_segments[segment_id]
            if int(local_segment["segment_index"]) != index or local_segment["content_hash"] != content_hash:
                raise ValueError("音频 ZIP 的文本哈希与当前版本不匹配")
            normalized_file = source_file.as_posix()
            if not normalized_file or source_file.is_absolute() or ".." in source_file.parts or normalized_file not in names:
                raise ValueError("音频 ZIP 的分段文件路径无效")
            seen_indices.add(index)
            prepared_segments.append({
                **item,
                "segment_id": segment_id,
                "index": index,
                "content_hash": content_hash,
                "file": normalized_file,
                "duration_ms": item.get("duration_ms"),
            })

        package_id = str(manifest.get("package_id") or _new_id())
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", package_id):
            package_id = _new_id()
        output_dir = AUDIO_DIR / paper_id / package_id
        if output_dir.exists():
            package_id = _new_id()
            output_dir = AUDIO_DIR / paper_id / package_id
        output_dir.mkdir(parents=True, exist_ok=False)
        try:
            copied_segments: list[dict] = []
            for item in prepared_segments:
                relative = Path(item["file"])
                target = (output_dir / relative).resolve()
                if output_dir.resolve() not in target.parents:
                    raise ValueError("音频 ZIP 文件路径越界")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(names[item["file"]], "r") as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                copied = {key: value for key, value in item.items() if key != "file"}
                copied["file_path"] = str(target)
                copied_segments.append(copied)

            full_audio = manifest.get("full_audio") or {}
            if isinstance(full_audio, str):
                full_file = full_audio
                full_duration_ms = manifest.get("total_duration_ms")
            else:
                full_file = str(full_audio.get("file") or full_audio.get("file_path") or "")
                full_duration_ms = full_audio.get("duration_ms")
            full_audio_path = ""
            if full_file:
                full_relative = Path(full_file)
                full_normalized = full_relative.as_posix()
                if full_relative.is_absolute() or ".." in full_relative.parts or full_normalized not in names:
                    raise ValueError("音频 ZIP 的完整音频路径无效")
                target = (output_dir / full_relative).resolve()
                if output_dir.resolve() not in target.parents:
                    raise ValueError("完整音频文件路径越界")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(names[full_normalized], "r") as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                full_audio_path = str(target)

            provider = str(manifest.get("provider") or "local_bridge")
            model = str(manifest.get("model") or "local-tts")
            voice_id = str(manifest.get("voice_id") or manifest.get("voice") or "local")
            now = _utc_now()
            with connection() as conn:
                for item in copied_segments:
                    conn.execute(
                        "INSERT INTO audio_assets(id, paper_id, text_version_id, segment_id, provider, model, voice_id, speed, text_hash, file_path, duration_ms, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)",
                        (_new_id(), paper_id, expected_text_version_id, item["segment_id"], provider, model, voice_id, float(manifest.get("speed", 1.0)), item["content_hash"], item["file_path"], item.get("duration_ms"), now),
                    )
                conn.execute(
                    "INSERT INTO audio_manifests(id, paper_id, text_version_id, provider, model, voice_id, full_audio_path, total_duration_ms, segments_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (package_id, paper_id, expected_text_version_id, provider, model, voice_id, full_audio_path, full_duration_ms, json.dumps(copied_segments, ensure_ascii=False), now),
                )
                conn.execute("UPDATE papers SET status='audio_ready', updated_at=? WHERE id=?", (now, paper_id))
            return {"paper_id": paper_id, "text_version_id": expected_text_version_id, "manifest_id": package_id, "segments": len(copied_segments)}
        except Exception:
            if output_dir.exists():
                shutil.rmtree(output_dir)
            raise


def get_audio_manifests(paper_id: str) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT am.*, tv.version_type, tv.display_name AS text_version_name "
            "FROM audio_manifests am LEFT JOIN text_versions tv ON tv.id=am.text_version_id "
            "WHERE am.paper_id=? ORDER BY am.created_at DESC", (paper_id,)
        ).fetchall()
        assets = conn.execute(
            "SELECT * FROM audio_assets WHERE paper_id=? ORDER BY created_at", (paper_id,)
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["text_version_name"] = item.get("text_version_name") or text_version_label(item)
        item["segments"] = json.loads(item.pop("segments_json", "[]"))
        manifest_id = item["id"]
        item["assets"] = [
            dict(asset) for asset in assets
            if manifest_id in str(asset["file_path"])
        ]
        if not item["assets"] and len(rows) == 1:
            item["assets"] = [dict(asset) for asset in assets]
        result.append(item)
    return result


def delete_audio_manifest(paper_id: str, manifest_id: str) -> dict:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM audio_manifests WHERE id=? AND paper_id=?", (manifest_id, paper_id)
        ).fetchone()
        if not row:
            raise ValueError("音频包不存在")
        conn.execute("DELETE FROM audio_manifests WHERE id=?", (manifest_id,))
        conn.execute("DELETE FROM audio_assets WHERE paper_id=? AND file_path LIKE ?", (paper_id, f"%{manifest_id}%"))
    package_dir = AUDIO_DIR / paper_id / manifest_id
    if package_dir.exists():
        import shutil
        shutil.rmtree(package_dir)
    return {"paper_id": paper_id, "manifest_id": manifest_id, "status": "deleted"}


def create_job(paper_id: str, job_type: str, request_json: str = "") -> dict:
    job_id = _new_id()
    now = _utc_now()
    with connection() as conn:
        conn.execute(
            "INSERT INTO jobs(id, paper_id, job_type, status, progress, request_json, created_at) VALUES (?, ?, ?, 'queued', 0, ?, ?)",
            (job_id, paper_id, job_type, request_json, now),
        )
    return get_job(job_id)  # type: ignore[return-value]


def get_job(job_id: str) -> dict | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(paper_id: str | None = None) -> list[dict]:
    query = "SELECT * FROM jobs"
    params: list[str] = []
    if paper_id:
        query += " WHERE paper_id=?"
        params.append(paper_id)
    query += " ORDER BY created_at DESC"
    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def update_job(job_id: str, **fields) -> dict | None:
    allowed = {"status", "progress", "message", "error_message", "started_at", "finished_at"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return get_job(job_id)
    assignments = ", ".join(f"{key}=?" for key in values)
    with connection() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", [*values.values(), job_id])
    return get_job(job_id)


def delete_job(job_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def append_job_log(job_id: str, message: str, level: str = "info") -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO job_logs(id, job_id, level, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (_new_id(), job_id, level, message, _utc_now()),
        )


def list_job_logs(job_id: str) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM job_logs WHERE job_id=? ORDER BY created_at", (job_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_audio_manifest(paper_id: str) -> dict | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM audio_manifests WHERE paper_id=? ORDER BY created_at DESC LIMIT 1",
            (paper_id,),
        ).fetchone()
        assets = conn.execute(
            "SELECT * FROM audio_assets WHERE paper_id=? ORDER BY created_at",
            (paper_id,),
        ).fetchall()
    if not row and not assets:
        return None
    result = dict(row) if row else {"paper_id": paper_id, "full_audio_path": "", "segments_json": "[]"}
    result["segments"] = json.loads(result.pop("segments_json", "[]"))
    result["assets"] = [dict(asset) for asset in assets]
    return result
