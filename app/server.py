import base64
import asyncio
import json
import os
import re
import secrets
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, UploadFile, File, HTTPException, Form, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
import PyPDF2

from .storage import (
    AUDIO_DIR,
    BACKUP_DIR,
    PAPERS_DIR,
    VOICES_DIR,
    clear_audio_assets,
    create_folder,
    create_paper,
    delete_folder,
    delete_paper,
    get_paper,
    get_paper_text,
    get_paper_text_version,
    get_knowledge_text,
    list_text_versions,
    activate_text_version,
    get_audio_manifest,
    get_audio_manifests,
    delete_audio_manifest,
    create_job,
    get_job,
    list_jobs,
    update_job,
    delete_job,
    append_job_log,
    list_job_logs,
    backup_library,
    get_backup_settings,
    restore_library,
    update_backup_settings,
    init_storage,
    list_resource_items,
    list_voice_clones,
    set_resource_protection,
    delete_resource,
    delete_cleanable_resources,
    list_recycle_bin,
    restore_recycle_item,
    permanently_delete_recycle_item,
    list_folders,
    list_papers,
    list_integration_papers,
    list_integration_deletions,
    get_integration_sync_state,
    save_integration_sync_ack,
    clear_integration_deletion,
    update_paper,
    update_folder,
    update_paper_text,
    update_text_knowledge_metadata,
    save_audio_asset,
    save_audio_manifest,
    import_audio_package,
)
from .text_processing import split_text_for_reading as _split_text_for_reading
from .vision_extraction import extract_pdf_body, extract_pdf_metadata

app = FastAPI(title="PDF Listen Book")
init_storage()
INTEGRATION_TOKEN = os.environ.get("PDF_LISTEN_INTEGRATION_TOKEN", "").strip()

# Mount static files
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Storage for processed texts
TEXT_STORE = {}
JOB_CONTROLS: dict[str, dict[str, asyncio.Event]] = {}
BACKUP_SCHEDULER_TASK: asyncio.Task | None = None


async def _backup_scheduler() -> None:
    while True:
        await asyncio.sleep(3600)
        settings = get_backup_settings()
        if not settings["interval_days"]:
            continue
        last = settings.get("last_backup_at")
        due = True
        if last:
            try:
                due = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() >= settings["interval_days"] * 86400
            except ValueError:
                due = True
        if due:
            try:
                backup_library("auto")
            except Exception:
                pass


@app.on_event("startup")
async def start_backup_scheduler() -> None:
    global BACKUP_SCHEDULER_TASK
    if BACKUP_SCHEDULER_TASK is None or BACKUP_SCHEDULER_TASK.done():
        BACKUP_SCHEDULER_TASK = asyncio.create_task(_backup_scheduler())


@app.on_event("shutdown")
async def stop_backup_scheduler() -> None:
    global BACKUP_SCHEDULER_TASK
    if BACKUP_SCHEDULER_TASK:
        BACKUP_SCHEDULER_TASK.cancel()
        BACKUP_SCHEDULER_TASK = None


class TTSRequest(BaseModel):
    text: str
    doc_id: Optional[str] = None
    api_key: str
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "cosyvoice-v2"
    voice: Optional[str] = None
    use_local_tts: bool = False


class TextProcessRequest(BaseModel):
    text: str


class FetchModelsRequest(BaseModel):
    api_key: str
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class LLMRequest(BaseModel):
    text: str
    api_key: str
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-turbo"
    prompt: str = "请将以下文本转换为更适合朗读的中文口语表达，保持原意不变："


class ConfigRequest(BaseModel):
    text: str
    api_key: str
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-turbo"
    prompt: str = "请将以下文本转换为更适合朗读的中文口语表达，保持原意不变："


class FolderCreateRequest(BaseModel):
    name: str
    parent_id: Optional[str] = None


class FolderUpdateRequest(BaseModel):
    name: str


class PaperTextUpdateRequest(BaseModel):
    text: str
    version_type: str = "edited"
    source_version_id: Optional[str] = None
    display_name: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class KnowledgeMetadataRequest(BaseModel):
    enabled: bool = True
    title: str = ""
    authors: str = ""
    abstract: str = ""


class VisionExtractRequest(BaseModel):
    api_key: str
    api_base: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash-vision-exp"


class LocalTTSBridgeTestRequest(BaseModel):
    base_url: str


class PaperUpdateRequest(BaseModel):
    title: Optional[str] = None
    is_favorite: Optional[bool] = None


class BackupSettingsRequest(BaseModel):
    interval_days: int = 30


class ResourceProtectionRequest(BaseModel):
    protected: bool


class AudioGenerateRequest(BaseModel):
    provider: str = "edge_tts"
    model: str = "edge-tts"
    voice: str = "zh-CN-XiaoxiaoNeural"
    speed: float = 1.0
    api_key: str = ""
    api_base: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    voice_id: str = ""
    segment_indices: list[int] = []
    target_package_id: str = ""
    text_version_id: Optional[str] = None
    bridge_url: str = ""
    voice_mode: str = "custom_voice"
    reference_audio: str = ""
    reference_text: str = ""
    instruct: str = ""


class JobRetryRequest(BaseModel):
    api_key: str = ""


class VoiceCloneRequest(BaseModel):
    name: str = "未命名音色"


class IntegrationSyncAckRequest(BaseModel):
    text_version_id: Optional[str] = None
    remote_document_id: Optional[str] = None
    status: str = "synced"
    error_message: Optional[str] = None


def require_integration_token(
    authorization: Optional[str] = Header(default=None),
    x_integration_token: Optional[str] = Header(default=None),
) -> None:
    """Protect the machine-to-machine API without affecting the local WebUI."""
    if not INTEGRATION_TOKEN:
        raise HTTPException(status_code=503, detail="未配置 PDF_LISTEN_INTEGRATION_TOKEN")
    provided = x_integration_token or ""
    if not provided and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            provided = value.strip()
    if not provided or not secrets.compare_digest(provided, INTEGRATION_TOKEN):
        raise HTTPException(status_code=401, detail="集成 API Token 无效")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"), headers={"Cache-Control": "no-store, max-age=0"})


def _folder_tree(folders: list[dict]) -> list[dict]:
    nodes = {folder["id"]: {**folder, "children": []} for folder in folders}
    roots = []
    for node in nodes.values():
        parent_id = node.get("parent_id")
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)
    return roots


def _guess_paper_title(reader: PyPDF2.PdfReader, filename: str, page_texts: list[str]) -> str:
    """Pick a conservative title candidate from PDF metadata or the first page."""
    fallback = Path(filename).stem.strip() or "未命名论文"
    metadata = reader.metadata
    metadata_title = str(getattr(metadata, "title", "") or "").strip() if metadata else ""
    if metadata_title and metadata_title.lower() not in {"anonymous", "(anonymous)", "untitled", "(untitled)"} and 3 <= len(metadata_title) <= 180 and not metadata_title.lower().startswith("microsoft"):
        return re.sub(r"\s+", " ", metadata_title)
    blocked = re.compile(r"(doi|issn|www\.|http|email|收稿|作者简介|摘要|关键词|abstract|keywords|volume|vol\.|university|大学|学院|学报)", re.I)
    for raw_line in (page_texts[0] if page_texts else "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" -—_:：;；")
        if not (8 <= len(line) <= 180) or blocked.search(line):
            continue
        if len(re.findall(r"[A-Za-z\u4e00-\u9fff]", line)) < 6:
            continue
        return line
    return fallback


@app.get("/api/folders/tree")
async def get_folder_tree():
    folders = list_folders()
    return JSONResponse({"folders": folders, "tree": _folder_tree(folders)})


@app.post("/api/folders")
async def add_folder(req: FolderCreateRequest):
    try:
        return JSONResponse(create_folder(req.name, req.parent_id), status_code=201)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/folders/{folder_id}")
async def rename_folder(folder_id: str, req: FolderUpdateRequest):
    try:
        return JSONResponse(update_folder(folder_id, req.name))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/folders/{folder_id}")
async def remove_folder(folder_id: str):
    try:
        delete_folder(folder_id)
        return JSONResponse({"status": "deleted", "folder_id": folder_id})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/papers")
async def get_papers(folder_id: Optional[str] = None, search: Optional[str] = None):
    return JSONResponse({"papers": list_papers(folder_id=folder_id, search=search)})


@app.get("/api/integration/papers")
async def get_integration_papers(
    updated_since: Optional[str] = None,
    folder_id: Optional[str] = None,
    _: None = Depends(require_integration_token),
):
    """List papers for external consumers; optionally use updated_at as a cursor."""
    return JSONResponse({
        "papers": list_integration_papers(updated_since=updated_since, folder_id=folder_id),
        "deleted": list_integration_deletions(updated_since=updated_since),
        "updated_since": updated_since,
    })


@app.get("/api/integration/papers/{paper_id}")
async def get_integration_paper(
    paper_id: str,
    _: None = Depends(require_integration_token),
):
    paper = get_paper(paper_id)
    if not paper or paper.get("status") == "deleted":
        raise HTTPException(status_code=404, detail="Paper not found")
    return JSONResponse({"paper": paper, "sync": get_integration_sync_state(paper_id)})


@app.get("/api/integration/papers/{paper_id}/text")
async def get_integration_paper_text(
    paper_id: str,
    _: None = Depends(require_integration_token),
):
    paper = get_paper(paper_id)
    text = get_paper_text(paper_id)
    if not paper or not text:
        raise HTTPException(status_code=404, detail="Paper text not found")
    return JSONResponse({"paper": paper, **text})


@app.get("/api/integration/papers/{paper_id}/knowledge")
async def get_integration_paper_knowledge(
    paper_id: str,
    _: None = Depends(require_integration_token),
):
    """Return only the text and metadata eligible for external knowledge bases."""
    paper = get_paper(paper_id)
    knowledge = get_knowledge_text(paper_id)
    if not paper or not knowledge:
        raise HTTPException(status_code=404, detail="Paper knowledge text not found")
    return JSONResponse({"paper": paper, **knowledge})


@app.get("/api/integration/papers/{paper_id}/sync-state")
async def get_integration_paper_sync_state(
    paper_id: str,
    _: None = Depends(require_integration_token),
):
    if not get_paper(paper_id):
        raise HTTPException(status_code=404, detail="Paper not found")
    return JSONResponse(get_integration_sync_state(paper_id))


@app.post("/api/integration/papers/{paper_id}/sync-ack")
async def acknowledge_integration_sync(
    paper_id: str,
    req: IntegrationSyncAckRequest,
    _: None = Depends(require_integration_token),
):
    if not get_paper(paper_id):
        raise HTTPException(status_code=404, detail="Paper not found")
    if req.status not in {"pending", "synced", "error"}:
        raise HTTPException(status_code=400, detail="不支持的同步状态")
    return JSONResponse(save_integration_sync_ack(
        paper_id,
        req.text_version_id,
        req.remote_document_id,
        req.status,
        req.error_message,
    ))


@app.post("/api/integration/papers/{paper_id}/delete-ack")
async def acknowledge_integration_delete(
    paper_id: str,
    _: None = Depends(require_integration_token),
):
    """Idempotent acknowledgement for plugins that removed a remote document."""
    clear_integration_deletion(paper_id)
    return JSONResponse({"paper_id": paper_id, "status": "deleted_acknowledged"})


@app.get("/api/backups/settings")
async def get_backup_settings_api():
    return JSONResponse(get_backup_settings())


@app.patch("/api/backups/settings")
async def save_backup_settings(req: BackupSettingsRequest):
    if req.interval_days < 0 or req.interval_days > 3650:
        raise HTTPException(status_code=400, detail="自动备份间隔应为 0 到 3650 天，0 表示关闭")
    return JSONResponse(update_backup_settings(interval_days=req.interval_days))


@app.get("/api/resources")
async def get_resources():
    return JSONResponse({"resources": list_resource_items()})


@app.get("/api/recycle-bin")
async def get_recycle_bin():
    return JSONResponse({"recycle_bin": list_recycle_bin()})


@app.post("/api/resources/cleanup")
async def cleanup_resources():
    return JSONResponse(delete_cleanable_resources())


@app.patch("/api/resources/{resource_type}/{resource_id}/protection")
async def update_resource_protection(resource_type: str, resource_id: str, req: ResourceProtectionRequest):
    try:
        return JSONResponse(set_resource_protection(resource_type, resource_id, req.protected))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/resources/{resource_type}/{resource_id}")
async def remove_resource(resource_type: str, resource_id: str):
    try:
        return JSONResponse(delete_resource(resource_type, resource_id))
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/recycle-bin/{entry_id}/restore")
async def restore_resource(entry_id: str):
    try:
        return JSONResponse(restore_recycle_item(entry_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/recycle-bin/{entry_id}")
async def purge_resource(entry_id: str):
    try:
        return JSONResponse(permanently_delete_recycle_item(entry_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/backups/create")
async def create_backup():
    try:
        archive = backup_library("manual")
        return JSONResponse({"filename": archive.name, "download_url": f"/api/backups/download/{archive.name}"}, status_code=201)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建备份失败: {exc}") from exc


@app.get("/api/backups/download/{filename}")
async def download_backup(filename: str):
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(".zip"):
        raise HTTPException(status_code=400, detail="无效的备份文件名")
    archive = BACKUP_DIR / safe_name
    if not archive.exists():
        raise HTTPException(status_code=404, detail="备份文件不存在")
    return FileResponse(str(archive), media_type="application/zip", filename=safe_name)


@app.post("/api/backups/import")
async def import_backup(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请选择 ZIP 格式的论文库备份")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="paper-backup-", suffix=".zip", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(await file.read())
        result = restore_library(temp_path)
        return JSONResponse(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"导入备份失败: {exc}") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


@app.post("/api/papers/import")
async def import_paper(file: UploadFile = File(...), folder_id: Optional[str] = None):
    filename = Path(file.filename or "paper.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    paper_id = uuid.uuid4().hex
    paper_dir = PAPERS_DIR / paper_id
    paper_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = paper_dir / filename

    try:
        content = await file.read()
        import io

        reader = PyPDF2.PdfReader(io.BytesIO(content))
        page_texts = [(page.extract_text() or "") for page in reader.pages]
        raw_text = "\n\n".join(page_texts).strip()
        detected_title = _guess_paper_title(reader, filename, page_texts)
        pdf_path.write_bytes(content)
        return JSONResponse(
            create_paper(
                filename=filename,
                pdf_path=pdf_path,
                page_count=len(reader.pages),
                raw_text=raw_text,
                folder_id=folder_id,
                paper_id=paper_id,
                title=detected_title,
            ),
            status_code=201,
        )
    except ValueError as exc:
        if paper_dir.exists():
            import shutil

            shutil.rmtree(paper_dir)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if paper_dir.exists():
            import shutil

            shutil.rmtree(paper_dir)
        raise HTTPException(status_code=500, detail=f"Failed to import PDF: {exc}") from exc


@app.patch("/api/papers/{paper_id}")
async def update_paper_detail(paper_id: str, req: PaperUpdateRequest):
    try:
        return JSONResponse(update_paper(paper_id, title=req.title, is_favorite=req.is_favorite))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/papers/{paper_id}")
async def get_paper_detail(paper_id: str):
    paper = get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return JSONResponse(paper)


@app.get("/api/papers/{paper_id}/text")
async def get_paper_text_detail(paper_id: str):
    text = get_paper_text(paper_id)
    if not text:
        raise HTTPException(status_code=404, detail="Paper text not found")
    return JSONResponse(text)


@app.get("/api/papers/{paper_id}/knowledge")
async def get_paper_knowledge_detail(paper_id: str):
    knowledge = get_knowledge_text(paper_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Paper knowledge text not found")
    return JSONResponse(knowledge)


@app.get("/api/papers/{paper_id}/texts")
async def get_paper_text_versions(paper_id: str):
    if not get_paper(paper_id):
        raise HTTPException(status_code=404, detail="Paper not found")
    return JSONResponse({"versions": list_text_versions(paper_id)})


@app.get("/api/papers/{paper_id}/texts/{version_id}")
async def get_paper_text_version_detail(paper_id: str, version_id: str):
    text = get_paper_text_version(paper_id, version_id)
    if not text:
        raise HTTPException(status_code=404, detail="Paper text version not found")
    return JSONResponse(text)


@app.patch("/api/papers/{paper_id}/texts/{version_id}/knowledge")
async def save_text_knowledge_metadata(paper_id: str, version_id: str, req: KnowledgeMetadataRequest):
    try:
        return JSONResponse(update_text_knowledge_metadata(
            paper_id, version_id, req.enabled, req.title, req.authors, req.abstract, "manual"
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/papers/{paper_id}/texts/{version_id}/activate")
async def activate_paper_text_version(paper_id: str, version_id: str):
    try:
        return JSONResponse(activate_text_version(paper_id, version_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/papers/{paper_id}/text")
async def save_paper_text(paper_id: str, req: PaperTextUpdateRequest):
    try:
        if req.version_type not in {
            "raw", "edited", "optimized", "manual", "organized", "deepseek_vision",
            "manual_optimized", "vision_optimized",
        }:
            raise HTTPException(status_code=400, detail="不支持的文本版本类型")
        return JSONResponse(update_paper_text(
            paper_id, req.text, req.version_type,
            source_version_id=req.source_version_id,
            display_name=req.display_name,
            provider=req.provider,
            model=req.model,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/papers/{paper_id}/manual-text", status_code=201)
async def import_manual_text(
    paper_id: str,
    file: UploadFile = File(...),
    kb_enabled: bool = Form(False),
    kb_title: str = Form(""),
    kb_authors: str = Form(""),
    kb_abstract: str = Form(""),
):
    if not get_paper(paper_id):
        raise HTTPException(status_code=404, detail="Paper not found")
    if kb_enabled and (not kb_title.strip() or not kb_abstract.strip()):
        raise HTTPException(status_code=400, detail="知识库文本的论文标题和摘要不能为空")
    filename = Path(file.filename or "正文.txt").name
    if Path(filename).suffix.lower() not in {".txt", ".md", ".markdown"}:
        raise HTTPException(status_code=400, detail="请选择 TXT 或 Markdown 正文文件")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="正文文件不能超过 20 MB")
    text = ""
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        raise HTTPException(status_code=400, detail="无法识别正文文件编码")
    try:
        result = update_paper_text(
            paper_id, text, "manual", display_name="手动导入", provider="manual",
            kb_enabled=kb_enabled, kb_title=kb_title, kb_authors=kb_authors,
            kb_abstract=kb_abstract, kb_source="manual",
        )
        return JSONResponse(result, status_code=201)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/papers/{paper_id}/original")
async def get_original_pdf(paper_id: str):
    paper = get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    pdf_path = Path(paper["original_pdf_path"])
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Original PDF not found")
    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@app.delete("/api/papers/{paper_id}")
async def remove_paper(paper_id: str):
    try:
        return JSONResponse(delete_paper(paper_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/papers/{paper_id}/audio")
async def get_paper_audio(paper_id: str):
    if not get_paper(paper_id):
        raise HTTPException(status_code=404, detail="Paper not found")
    packages = get_audio_manifests(paper_id)
    for package in packages:
        package_id = package["id"]
        for segment in package.get("segments", []):
            source = segment.get("file_path") or segment.get("url", "")
            if source:
                segment["url"] = f"/api/audio/{paper_id}/{package_id}/segments/{Path(source).name}"
        for asset in package.get("assets", []):
            asset["url"] = f"/api/audio/{paper_id}/{package_id}/segments/{Path(asset['file_path']).name}"
        if package.get("full_audio_path"):
            package["full_audio_url"] = f"/api/audio/{paper_id}/{package_id}/{Path(package['full_audio_path']).name}"
    latest = packages[0] if packages else None
    return JSONResponse({
        "ready": bool(packages),
        "packages": packages,
        "segments": latest.get("segments", []) if latest else [],
        "full_audio_url": latest.get("full_audio_url") if latest else None,
    })


@app.get("/api/audio/{paper_id}/{package_id}/{filename:path}")
async def get_paper_audio_package_file(paper_id: str, package_id: str, filename: str):
    relative = Path(filename)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="Invalid audio path")
    package_dir = (AUDIO_DIR / paper_id / package_id).resolve()
    file_path = (package_dir / relative).resolve()
    if package_dir not in file_path.parents:
        raise HTTPException(status_code=400, detail="Invalid audio path")
    # Keep compatibility with URLs generated by older builds that omitted
    # the segments/ directory for segment audio files.
    if not file_path.exists() and len(relative.parts) == 1:
        legacy_path = (package_dir / "segments" / relative.name).resolve()
        if package_dir in legacy_path.parents:
            file_path = legacy_path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(str(file_path), media_type=_audio_media_type(file_path))


@app.get("/api/audio/{paper_id}/{filename}")
async def get_paper_audio_file(paper_id: str, filename: str):
    safe_name = Path(filename).name
    file_path = AUDIO_DIR / paper_id / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(str(file_path), media_type=_audio_media_type(file_path))


def _normalize_bridge_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("请填写本地 TTS 中介服务地址")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value


def _audio_media_type(path: Path) -> str:
    return {
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
    }.get(path.suffix.lower(), "audio/mpeg")


@app.post("/api/local-tts/test")
async def test_local_tts_bridge(req: LocalTTSBridgeTestRequest):
    try:
        base_url = _normalize_bridge_url(req.base_url)
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(f"{base_url}/health")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"中介服务返回 HTTP {response.status_code}")
        try:
            health = response.json()
        except ValueError:
            health = {}
        if health.get("ok") is False:
            raise HTTPException(status_code=502, detail=health.get("error") or "中介服务未就绪")
        return JSONResponse({"ok": True, "base_url": base_url, "health": health})
    except HTTPException:
        raise
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"无法连接本地 TTS 中介：{exc}") from exc


async def _await_job_slot(job_id: str) -> bool:
    control = JOB_CONTROLS[job_id]
    while not control["resume"].is_set():
        update_job(job_id, status="paused", message="任务已暂停")
        await asyncio.sleep(0.25)
        if control["stop"].is_set():
            return False
    return not control["stop"].is_set()


def _build_bridge_submission(paper_id: str, req: AudioGenerateRequest) -> tuple[str, dict, dict]:
    paper = get_paper(paper_id)
    text_data = get_paper_text_version(paper_id, req.text_version_id) if req.text_version_id else get_paper_text(paper_id)
    if not paper or not text_data:
        raise ValueError("论文或指定文本版本不存在")
    base_url = _normalize_bridge_url(req.bridge_url)
    segments = text_data["segments"]
    requested_indices = sorted({index for index in req.segment_indices if 0 <= index < len(segments)})
    selected_segments = [(index, segments[index]) for index in requested_indices] if requested_indices else list(enumerate(segments))
    payload = {
        "schema_version": "pdf-local-tts-1",
        "job_type": "pdf_audio",
        "paper_id": paper_id,
        "text_version_id": text_data["version"]["id"],
        "text_version_label": text_data["version"].get("display_name") or text_data["version"].get("version_type"),
        "model": req.model,
        "voice": req.voice,
        "voice_id": req.voice_id or req.voice,
        "speed": req.speed,
        "voice_mode": req.voice_mode,
        "reference_audio": req.reference_audio,
        "reference_text": req.reference_text,
        "instruct": req.instruct,
        "target_package_id": req.target_package_id,
        "segments": [
            {"index": index, "segment_id": segment["id"], "content_hash": segment["content_hash"], "text": segment["content"]}
            for index, segment in selected_segments
        ],
    }
    return base_url, payload, text_data


async def _run_bridge_audio_job(job_id: str, paper_id: str, req: AudioGenerateRequest) -> None:
    base_url, payload, text_data = _build_bridge_submission(paper_id, req)
    update_job(job_id, status="running", progress=0, message="正在提交本地大模型 TTS 任务", started_at=datetime.now(timezone.utc).isoformat())
    timeout = httpx.Timeout(connect=15.0, read=60.0, write=120.0, pool=30.0)
    bridge_status = "running"
    bridge_status_data: dict = {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{base_url}/v1/jobs", json=payload)
        if response.status_code not in {200, 201, 202}:
            raise ValueError(f"中介任务提交失败：HTTP {response.status_code} {response.text[:300]}")
        response_data = response.json()
        bridge_job = response_data.get("job", response_data)
        bridge_job_id = str(bridge_job.get("id") or bridge_job.get("job_id") or "")
        if not bridge_job_id:
            raise ValueError("中介没有返回任务 ID")

        while True:
            if not await _await_job_slot(job_id):
                try:
                    await client.post(f"{base_url}/v1/jobs/{bridge_job_id}/cancel")
                except httpx.RequestError:
                    pass
                update_job(job_id, status="cancelled", message="任务已停止", finished_at=datetime.now(timezone.utc).isoformat())
                return
            status_response = await client.get(f"{base_url}/v1/jobs/{bridge_job_id}")
            if status_response.status_code != 200:
                raise ValueError(f"读取中介任务状态失败：HTTP {status_response.status_code}")
            response_data = status_response.json()
            status_data = response_data.get("job", response_data)
            bridge_status_data = status_data
            bridge_status = str(status_data.get("status", "")).lower()
            raw_progress = float(status_data.get("progress", 0) or 0)
            progress = raw_progress / 100 if raw_progress > 1 else raw_progress
            update_job(job_id, status="running", progress=max(0, min(progress, 1)), message=f"本地大模型：{status_data.get('message') or bridge_status}")
            if bridge_status in {"completed", "completed_with_errors"}:
                break
            if bridge_status in {"failed", "cancelled", "canceled"}:
                raise ValueError(status_data.get("error_message") or status_data.get("message") or "中介语音任务失败")
            await asyncio.sleep(1.5)

        async with client.stream("GET", f"{base_url}/v1/jobs/{bridge_job_id}/package") as package_response:
            if package_response.status_code != 200:
                raise ValueError(f"下载中介音频 ZIP 失败：HTTP {package_response.status_code}")
            with tempfile.NamedTemporaryFile(prefix="pdf-local-tts-", suffix=".zip", delete=False) as temp_file:
                temp_path = Path(temp_file.name)
                async for chunk in package_response.aiter_bytes():
                    temp_file.write(chunk)
    try:
        imported = import_audio_package(paper_id, temp_path, text_data["version"]["id"])
    finally:
        if temp_path.exists():
            temp_path.unlink()
    final_status = "completed_with_errors" if bridge_status == "completed_with_errors" else "completed"
    bridge_error = None
    if final_status == "completed_with_errors":
        bridge_error = json.dumps({
            "failed_segments": bridge_status_data.get("failed_segments", []),
            "package_id": imported["manifest_id"],
            "bridge_job_id": bridge_job_id,
            "message": bridge_status_data.get("error_message") or bridge_status_data.get("message", ""),
        }, ensure_ascii=False)
    update_job(
        job_id,
        status=final_status,
        progress=1,
        message=f"本地大模型语音已导入（{imported['segments']} 段）",
        error_message=bridge_error,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


async def _run_vision_job(
    job_id: str,
    paper_id: str,
    req: VisionExtractRequest,
    source_version_id: str | None,
) -> None:
    try:
        paper = get_paper(paper_id)
        if not paper:
            raise ValueError("论文不存在")
        pdf_path = Path(paper["original_pdf_path"])
        update_job(
            job_id,
            status="running",
            progress=0,
            message="正在读取论文标题、作者和摘要",
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        try:
            metadata = await extract_pdf_metadata(pdf_path, req.api_key, req.api_base, req.model)
            append_job_log(job_id, "论文标题、作者和摘要已识别并保存到隐藏元信息")
        except Exception as exc:
            # Metadata is useful for AstrBot, but must not prevent a valid body
            # extraction when a model returns an incompatible metadata response.
            metadata = {"title": "", "authors": "", "abstract": ""}
            append_job_log(job_id, f"论文元信息识别失败，将继续提取正文：{exc}", "warning")
        update_job(job_id, message="正在提取论文纯正文")

        async def on_progress(page_number: int, total: int) -> None:
            if not await _await_job_slot(job_id):
                raise RuntimeError("任务已停止")
            update_job(
                job_id,
                status="running",
                progress=page_number / max(total, 1),
                message=f"DeepSeek 读图第 {page_number}/{total} 页",
            )

        body_text = await extract_pdf_body(
            pdf_path, req.api_key, req.api_base, req.model, progress_callback=on_progress
        )
        if not body_text.strip():
            raise ValueError("DeepSeek 没有提取到可用的论文正文")
        update_paper_text(
            paper_id,
            body_text,
            "deepseek_vision",
            source_version_id=source_version_id,
            display_name="读图识别",
            provider="deepseek",
            model=req.model,
            kb_enabled=True,
            kb_title=metadata.get("title", ""),
            kb_authors=metadata.get("authors", ""),
            kb_abstract=metadata.get("abstract", ""),
            kb_source="vision",
        )
        append_job_log(job_id, "DeepSeek 读图正文已保存为新的文本版本")
        update_job(
            job_id,
            status="completed",
            progress=1,
            message="DeepSeek 读图识别完成",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        message = str(exc)
        status = "cancelled" if "任务已停止" in message else "failed"
        append_job_log(job_id, f"DeepSeek 读图任务失败: {message}", "error")
        update_job(
            job_id,
            status=status,
            error_message=message,
            message="DeepSeek 读图任务已停止" if status == "cancelled" else "DeepSeek 读图失败",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        JOB_CONTROLS.pop(job_id, None)


@app.post("/api/papers/{paper_id}/vision-extract", status_code=202)
async def start_vision_extract(paper_id: str, req: VisionExtractRequest):
    paper = get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not Path(paper["original_pdf_path"]).exists():
        raise HTTPException(status_code=404, detail="Original PDF not found")
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="请先填写 DeepSeek API Key")
    source_version_id = paper.get("active_text_version_id")
    stored_request = req.dict(exclude={"api_key"})
    job = create_job(paper_id, "vision_extract", json.dumps(stored_request, ensure_ascii=False))
    JOB_CONTROLS[job["id"]] = {"resume": asyncio.Event(), "stop": asyncio.Event()}
    JOB_CONTROLS[job["id"]]["resume"].set()
    asyncio.create_task(_run_vision_job(job["id"], paper_id, req, source_version_id))
    append_job_log(job["id"], "DeepSeek 读图任务已进入后台队列")
    return JSONResponse({"job": get_job(job["id"])}, status_code=202)


async def _synthesize_qwen_tts(text: str, req: AudioGenerateRequest, output_path: Path) -> None:
    if not req.api_key:
        raise ValueError("在线 TTS 需要先在设置中填写 Qwen API Key")
    native_base = re.sub(r"/compatible-mode/v1/?$", "/api/v1", req.api_base.rstrip("/"))
    endpoint = f"{native_base}/services/aigc/multimodal-generation/generation"
    payload = {"model": req.model, "input": {"text": text, "voice": req.voice_id or req.voice}}
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(endpoint, headers={"Authorization": f"Bearer {req.api_key}"}, json=payload)
        if response.status_code != 200:
            raise ValueError(f"Qwen TTS 请求失败: {response.text[:300]}")
        result = response.json()
        audio = result.get("output", {}).get("audio", {})
        audio_url = audio.get("url") or result.get("output", {}).get("url")
        if audio_url:
            audio_response = await client.get(audio_url)
            audio_response.raise_for_status()
            output_path.write_bytes(audio_response.content)
            return
        audio_base64 = audio.get("data") or audio.get("base64")
        if audio_base64:
            output_path.write_bytes(base64.b64decode(audio_base64))
            return
    raise ValueError("Qwen TTS 未返回可下载的音频")


async def _run_audio_job(job_id: str, paper_id: str, req: AudioGenerateRequest) -> None:
    try:
        if req.provider == "local_bridge":
            await _run_bridge_audio_job(job_id, paper_id, req)
            return
        import edge_tts

        paper = get_paper(paper_id)
        text_data = (
            get_paper_text_version(paper_id, req.text_version_id)
            if req.text_version_id
            else get_paper_text(paper_id)
        )
        if not paper or not text_data:
            raise ValueError("论文或文本不存在")
        package_id = req.target_package_id or uuid.uuid4().hex
        output_dir = AUDIO_DIR / paper_id / package_id
        output_dir.mkdir(parents=True, exist_ok=True)
        update_job(job_id, status="running", progress=0, message="开始生成语音", started_at=datetime.now(timezone.utc).isoformat())
        append_job_log(job_id, f"创建音频包 {package_id}")
        segments = text_data["segments"]
        requested_indices = sorted({index for index in req.segment_indices if 0 <= index < len(segments)})
        work_items = [(index, segments[index]) for index in requested_indices] if requested_indices else list(enumerate(segments))
        existing_package = next((item for item in get_audio_manifests(paper_id) if item["id"] == package_id), None)
        audio_segments = list(existing_package.get("segments", [])) if existing_package else []
        failed_segments = []
        total = max(len(work_items), 1)
        for position, (index, segment) in enumerate(work_items):
            if not await _await_job_slot(job_id):
                update_job(job_id, status="cancelled", message="任务已停止", finished_at=datetime.now(timezone.utc).isoformat())
                append_job_log(job_id, "用户停止任务", "warning")
                return
            update_job(job_id, status="running", message=f"正在生成第 {index + 1}/{len(segments)} 段", progress=position / total)
            file_name = f"{index:05d}_{segment['content_hash'][:16]}.mp3"
            file_path = output_dir / file_name
            try:
                if req.provider == "edge_tts":
                    await edge_tts.Communicate(segment["content"], req.voice).save(str(file_path))
                elif req.provider == "qwen_tts":
                    await _synthesize_qwen_tts(segment["content"], req, file_path)
                else:
                    raise ValueError("不支持的语音提供方")
                duration_ms = None
                try:
                    from imageio_ffmpeg import get_ffmpeg_exe
                    from pydub import AudioSegment
                    AudioSegment.converter = get_ffmpeg_exe()
                    duration_ms = len(AudioSegment.from_file(file_path))
                except Exception:
                    pass
                save_audio_asset(
                    paper_id=paper_id, text_version_id=text_data["version"]["id"], segment_id=segment["id"],
                    provider=req.provider, model=req.model, voice_id=req.voice_id or req.voice, speed=req.speed,
                    text_hash=segment["content_hash"], file_path=str(file_path), duration_ms=duration_ms,
                )
                audio_segments = [item for item in audio_segments if item.get("index") != index]
                audio_segments.append({"segment_id": segment["id"], "index": index, "content_hash": segment["content_hash"], "file_path": str(file_path), "duration_ms": duration_ms})
            except Exception as exc:
                failed_segments.append(index)
                append_job_log(job_id, f"第 {index + 1} 段生成失败: {exc}", "error")
            update_job(job_id, progress=(position + 1) / total)

        full_audio_path = ""
        total_duration_ms = None
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            from pydub import AudioSegment
            AudioSegment.converter = get_ffmpeg_exe()
            combined = AudioSegment.empty()
            offset = 0
            audio_segments.sort(key=lambda item: item.get("index", 0))
            for item in audio_segments:
                audio = AudioSegment.from_file(item["file_path"])
                item["start_ms"] = offset
                offset += len(audio)
                item["end_ms"] = offset
                combined += audio
            if audio_segments:
                full_path = output_dir / "full.mp3"
                combined.export(full_path, format="mp3")
                full_audio_path = str(full_path)
                total_duration_ms = offset
        except Exception as exc:
            append_job_log(job_id, f"完整音频合并不可用: {exc}", "warning")

        if audio_segments:
            save_audio_manifest(
                paper_id=paper_id, text_version_id=text_data["version"]["id"], provider=req.provider,
                model=req.model, voice_id=req.voice_id or req.voice, full_audio_path=full_audio_path,
                total_duration_ms=total_duration_ms, segments=audio_segments, manifest_id=package_id,
            )
        if failed_segments:
            update_job(job_id, status="completed_with_errors", progress=1, message=f"完成，但 {len(failed_segments)} 段失败", error_message=json.dumps({"failed_segments": failed_segments, "package_id": package_id}), finished_at=datetime.now(timezone.utc).isoformat())
        elif audio_segments:
            update_job(job_id, status="completed", progress=1, message="语音生成完成", finished_at=datetime.now(timezone.utc).isoformat())
            append_job_log(job_id, "语音生成完成")
        else:
            update_job(job_id, status="failed", message="没有生成可用音频", finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        append_job_log(job_id, f"任务失败: {exc}", "error")
        update_job(job_id, status="failed", error_message=str(exc), message="语音生成失败", finished_at=datetime.now(timezone.utc).isoformat())
    finally:
        JOB_CONTROLS.pop(job_id, None)


@app.post("/api/papers/{paper_id}/audio/bridge-submit", status_code=202)
async def submit_bridge_audio(paper_id: str, req: AudioGenerateRequest):
    """Submit a local-bridge task without creating a PDF-reader background job."""
    if req.provider != "local_bridge":
        raise HTTPException(status_code=400, detail="该接口只接受本地 TTS 中介任务")
    try:
        base_url, payload, text_data = _build_bridge_submission(paper_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        timeout = httpx.Timeout(connect=15.0, read=60.0, write=120.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base_url}/v1/jobs", json=payload)
        if response.status_code not in {200, 201, 202}:
            raise HTTPException(status_code=502, detail=f"中介任务提交失败：HTTP {response.status_code} {response.text[:300]}")
        response_data = response.json()
        bridge_job = response_data.get("job", response_data)
        bridge_job_id = str(bridge_job.get("id") or bridge_job.get("job_id") or "")
        if not bridge_job_id:
            raise HTTPException(status_code=502, detail="中介没有返回任务 ID")
        return JSONResponse({
            "submitted": True,
            "job": bridge_job,
            "bridge_job_id": bridge_job_id,
            "paper_id": paper_id,
            "text_version_id": text_data["version"]["id"],
            "text_version_label": text_data["version"].get("display_name") or text_data["version"].get("version_type"),
        }, status_code=202)
    except HTTPException:
        raise
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"无法提交本地 TTS 中介任务：{exc}") from exc


@app.post("/api/audio/import-packages")
async def import_audio_packages(files: list[UploadFile] = File(...)):
    """Import one or more bridge ZIPs by the paper/version IDs in each manifest."""
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个语音包 ZIP")
    imported: list[dict] = []
    failed: list[dict] = []
    for upload in files:
        filename = Path(upload.filename or "audio-package.zip").name
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="pdf-audio-import-", suffix=".zip", delete=False) as temp_file:
                temp_path = Path(temp_file.name)
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    temp_file.write(chunk)
            with zipfile.ZipFile(temp_path, "r") as archive:
                if "manifest.json" not in archive.namelist():
                    raise ValueError("缺少 manifest.json")
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            paper_id = str(manifest.get("paper_id") or "")
            text_version_id = str(manifest.get("text_version_id") or "")
            if not paper_id or not text_version_id:
                raise ValueError("manifest 缺少 paper_id 或 text_version_id")
            paper = get_paper(paper_id)
            text_data = get_paper_text_version(paper_id, text_version_id)
            if not paper or not text_data:
                raise ValueError("找不到该语音包对应的论文或文本版本")
            result = import_audio_package(paper_id, temp_path, text_version_id)
            imported.append({
                "filename": filename,
                "paper_id": paper_id,
                "paper_title": paper.get("title") or paper_id,
                "text_version_id": text_version_id,
                "text_version_label": text_data["version"].get("display_name") or text_data["version"].get("version_type"),
                "segments": result["segments"],
                "manifest_id": result["manifest_id"],
            })
        except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            failed.append({"filename": filename, "error": str(exc)})
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()
            await upload.close()
    return JSONResponse({"ok": bool(imported) and not failed, "imported": imported, "failed": failed})


@app.post("/api/papers/{paper_id}/audio", status_code=202)
async def generate_paper_audio(paper_id: str, req: AudioGenerateRequest):
    if not get_paper(paper_id):
        raise HTTPException(status_code=404, detail="Paper or paper text not found")
    if req.text_version_id:
        if not get_paper_text_version(paper_id, req.text_version_id):
            raise HTTPException(status_code=404, detail="指定的文本版本不存在")
    elif not get_paper_text(paper_id):
        raise HTTPException(status_code=404, detail="Paper or paper text not found")
    if req.provider == "local_bridge":
        return await submit_bridge_audio(paper_id, req)
    elif req.provider not in {"edge_tts", "qwen_tts"}:
        raise HTTPException(status_code=400, detail="不支持的语音提供方")
    stored_request = req.dict(exclude={"api_key"})
    job = create_job(paper_id, "audio_generate", json.dumps(stored_request, ensure_ascii=False))
    JOB_CONTROLS[job["id"]] = {"resume": asyncio.Event(), "stop": asyncio.Event()}
    JOB_CONTROLS[job["id"]]["resume"].set()
    asyncio.create_task(_run_audio_job(job["id"], paper_id, req))
    append_job_log(job["id"], "任务已进入后台队列")
    return JSONResponse({"job": get_job(job["id"])}, status_code=202)


@app.post("/api/jobs/{job_id}/retry", status_code=202)
async def retry_failed_audio(job_id: str, req: JobRetryRequest):
    previous = get_job(job_id)
    if not previous:
        raise HTTPException(status_code=404, detail="任务不存在")
    if previous.get("job_type") != "audio_generate":
        raise HTTPException(status_code=400, detail="该任务不是语音生成任务")
    try:
        request_data = json.loads(previous.get("request_json") or "{}")
        error_data = json.loads(previous.get("error_message") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="任务缺少可重试的片段信息") from exc
    failed = error_data.get("failed_segments", []) if isinstance(error_data, dict) else []
    if not failed:
        raise HTTPException(status_code=400, detail="该任务没有失败片段")
    request_data["segment_indices"] = failed
    request_data["target_package_id"] = error_data.get("package_id", "")
    request_data["api_key"] = req.api_key
    request = AudioGenerateRequest(**request_data)
    if request.provider == "qwen_tts" and not request.api_key:
        raise HTTPException(status_code=400, detail="重试在线语音前，请在设置中重新填写 API Key")
    return await generate_paper_audio(previous["paper_id"], request)


@app.delete("/api/papers/{paper_id}/audio/{manifest_id}")
async def remove_audio_package(paper_id: str, manifest_id: str):
    try:
        package = next((item for item in list_resource_items() if item["resource_type"] == "audio" and item["resource_id"] == manifest_id and item.get("paper_id") == paper_id), None)
        if not package:
            raise ValueError("音频包不存在")
        return JSONResponse(delete_resource("audio", manifest_id))
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/jobs")
async def get_jobs(paper_id: Optional[str] = None):
    return JSONResponse({"jobs": list_jobs(paper_id)})


@app.get("/api/jobs/{job_id}")
async def get_job_detail(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONResponse(job)


@app.get("/api/jobs/{job_id}/logs")
async def get_job_logs(job_id: str):
    if not get_job(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONResponse({"logs": list_job_logs(job_id)})


@app.post("/api/jobs/{job_id}/{action}")
async def control_job(job_id: str, action: str):
    control = JOB_CONTROLS.get(job_id)
    if action not in {"pause", "resume", "stop"}:
        raise HTTPException(status_code=400, detail="不支持的任务操作")
    if not control:
        raise HTTPException(status_code=409, detail="任务已结束，无法继续控制")
    if action == "pause":
        control["resume"].clear()
        update_job(job_id, status="paused", message="任务已暂停")
    elif action == "resume":
        control["resume"].set()
        update_job(job_id, status="running", message="继续生成")
    else:
        control["stop"].set()
        control["resume"].set()
    append_job_log(job_id, f"用户执行操作: {action}")
    return JSONResponse(get_job(job_id))


@app.delete("/api/jobs/{job_id}")
async def remove_job(job_id: str):
    control = JOB_CONTROLS.get(job_id)
    if control:
        control["stop"].set()
        control["resume"].set()
        update_job(job_id, status="cancelling", message="正在停止并删除任务")

        async def delete_when_stopped() -> None:
            while job_id in JOB_CONTROLS:
                await asyncio.sleep(0.25)
            delete_job(job_id)

        asyncio.create_task(delete_when_stopped())
        return JSONResponse({"job_id": job_id, "status": "cancelling"}, status_code=202)
    try:
        result = delete_resource("job", job_id)
        return JSONResponse({"job_id": job_id, "status": result["status"]})
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/voice-clones")
async def get_voice_clones(target_model: str = ""):
    return JSONResponse({"voices": list_voice_clones(target_model.strip() or None)})


@app.post("/api/voice-clones")
async def upload_voice_clone(
    file: UploadFile = File(...),
    name: str = Form("未命名音色"),
    api_key: str = Form(""),
    api_base: str = Form("https://dashscope.aliyuncs.com/compatible-mode/v1"),
    target_model: str = Form(""),
):
    """Create a Qwen voice from a local sample and keep a local audit copy."""
    original_name = Path(file.filename or "voice-sample").name
    suffix = Path(original_name).suffix.lower()
    allowed = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail="音源格式暂不支持，请上传 wav、mp3、m4a、flac、ogg 或 aac")
    content = await file.read()
    if not api_key or not target_model:
        raise HTTPException(status_code=400, detail="请先填写 Qwen API Key 并选择支持克隆音色的模型")
    if "-vc" not in target_model.lower():
        raise HTTPException(status_code=400, detail="当前本地音源直传仅支持 Qwen3-TTS-VC 模型；CosyVoice/Qwen-Audio 克隆需要公网音频 URL")

    source_stem = Path(original_name).stem.strip() or "voice"
    display_name = (name.strip() or source_stem)[:80]
    preferred_name = re.sub(r"[^A-Za-z0-9_]", "_", display_name)[:16].strip("_") or f"voice_{uuid.uuid4().hex[:8]}"
    mime_map = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/mp4"}
    mime_type = mime_map.get(suffix, "audio/mpeg")
    native_base = re.sub(r"/compatible-mode/v1/?$", "/api/v1", api_base.rstrip("/"))
    endpoint = f"{native_base}/services/audio/tts/customization"
    payload = {
        "model": "qwen-voice-enrollment",
        "input": {
            "action": "create",
            "target_model": target_model,
            "preferred_name": preferred_name,
            "audio": {"data": f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(endpoint, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
        if response.status_code != 200:
            raise ValueError(response.text[:500])
        result = response.json()
        remote_voice = result.get("output", {}).get("voice")
        if not remote_voice:
            raise ValueError("接口未返回 voice 字段")
        voice_id = uuid.uuid4().hex
        voice_dir = VOICES_DIR / voice_id
        voice_dir.mkdir(parents=True, exist_ok=False)
        path = voice_dir / f"sample{suffix}"
        path.write_bytes(content)
        (voice_dir / "metadata.json").write_text(
            json.dumps({"voice": remote_voice, "target_model": target_model, "name": display_name, "preferred_name": preferred_name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        if "voice_dir" in locals() and voice_dir.exists():
            import shutil
            shutil.rmtree(voice_dir, ignore_errors=True)
        raise HTTPException(status_code=502, detail=f"Qwen 克隆音色创建失败: {exc}") from exc
    return JSONResponse({"id": remote_voice, "local_id": voice_id, "name": display_name, "status": "created"}, status_code=201)


@app.post("/api/fetch-models")
async def fetch_models(req: FetchModelsRequest):
    """Fetch available models from the API"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{req.api_base}/models",
                headers={
                    "Authorization": f"Bearer {req.api_key}",
                    "Content-Type": "application/json"
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"Failed to fetch models: {response.text}")
            
            data = response.json()
            models = []
            tts_models = []
            tts_capabilities = {}
            llm_models = []
            vision_models = []
            known_tts = {
                "qwen-audio-3.0-tts-plus": {"default_voice": True, "clone_voice": False},
                "qwen-audio-3.0-tts-flash": {"default_voice": True, "clone_voice": True},
                "qwen3-tts-flash": {"default_voice": True, "clone_voice": False},
                "qwen3-tts-instruct-flash": {"default_voice": True, "clone_voice": False},
                "cosyvoice-v3.5-plus": {"default_voice": True, "clone_voice": True},
                "cosyvoice-v3.5-flash": {"default_voice": True, "clone_voice": True},
                "cosyvoice-v3-plus": {"default_voice": True, "clone_voice": True},
                "cosyvoice-v3-flash": {"default_voice": True, "clone_voice": True},
                "cosyvoice-v2": {"default_voice": True, "clone_voice": True},
                "cosyvoice-v1": {"default_voice": True, "clone_voice": True},
                "sambert-zhiyan-v1": {"default_voice": True, "clone_voice": False},
                "sambert-zhichu-v1": {"default_voice": True, "clone_voice": False},
            }

            def classify(model_id: str) -> dict | None:
                lower_id = model_id.lower()
                if "realtime" in lower_id or "-vd" in lower_id:
                    return None
                if "-vc" in lower_id or "voice-clone" in lower_id:
                    return {"default_voice": False, "clone_voice": True}
                if model_id in known_tts:
                    return known_tts[model_id]
                if any(token in lower_id for token in ("cosyvoice", "sambert")):
                    return {"default_voice": True, "clone_voice": True}
                if "tts" in lower_id and not any(token in lower_id for token in ("asr", "stt")):
                    return {"default_voice": True, "clone_voice": "vc" in lower_id or "clone" in lower_id}
                return None

            for model in data.get("data", []):
                model_id = model.get("id", "")
                if not model_id:
                    continue
                models.append(model_id)
                if any(token in model_id.lower() for token in ("vision", "-vl", "vl-")):
                    vision_models.append(model_id)
                capability = classify(model_id)
                if capability:
                    tts_models.append(model_id)
                    tts_capabilities[model_id] = capability
                elif any(kw in model_id.lower() for kw in ["qwen", "gpt", "glm", "chat", "completion", "turbo", "plus", "max", "deepseek"]):
                    llm_models.append(model_id)

            if not llm_models:
                llm_models = ["qwen-turbo", "qwen-plus", "qwen-max"]

            return JSONResponse({
                "tts_models": tts_models,
                "tts_capabilities": tts_capabilities,
                "llm_models": llm_models,
                "vision_models": vision_models,
                "all_models": models[:100]
            })
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Request failed: {str(e)}")


@app.get("/api/tts-voices")
async def get_tts_voices(model: str = "cosyvoice-v2"):
    """Get available voices for a TTS model"""
    voice_map = {
        "cosyvoice-v3": ["longanyang", "longxiaochun", "yuer", "toonyun"],
        "cosyvoice-v3-flash": ["longanyang", "longxiaochun", "yuer", "toonyun"],
        "cosyvoice-v3-plus": ["longanyang", "longxiaochun", "yuer", "toonyun"],
        "cosyvoice-v2": ["longxiaochun_v2", "longyang_v2", "longwan_v2", "longcheng_v2"],
        "cosyvoice-v1": ["longxiaochun", "longyang", "longwan", "longcheng"],
        "sambert": ["zhiyan", "zhichu", "zhibei", "zhimiao", "xiaoyun", "xiaogang"],
        "tts-1": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
        "tts-1-hd": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    }
    
    # Try to find matching voices
    for key in voice_map:
        if key in model.lower():
            return JSONResponse({"voices": voice_map[key]})
    
    # Default voices for unknown models (cosyvoice v3 style)
    return JSONResponse({"voices": ["longanyang", "longxiaochun", "yuer", "toonyun"]})


@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload PDF and extract text"""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    try:
        content = await file.read()
        import io
        reader = PyPDF2.PdfReader(io.BytesIO(content))
        
        pages = []
        full_text = ""
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            pages.append({"page": i + 1, "text": text})
            full_text += text + "\n\n"

        # Process text into chunks for reading
        chunks = split_text_for_reading(full_text)
        
        # Store with unique ID
        doc_id = str(uuid.uuid4())[:8]
        TEXT_STORE[doc_id] = {
            "filename": file.filename,
            "full_text": full_text,
            "pages": pages,
            "chunks": chunks
        }

        return JSONResponse({
            "doc_id": doc_id,
            "filename": file.filename,
            "page_count": len(pages),
            "chunk_count": len(chunks)
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")


@app.post("/api/process-text")
async def process_text(req: TextProcessRequest):
    """Process raw text input into chunks"""
    try:
        chunks = split_text_for_reading(req.text)
        doc_id = str(uuid.uuid4())[:8]
        TEXT_STORE[doc_id] = {
            "filename": "text_input.txt",
            "full_text": req.text,
            "pages": [],
            "chunks": chunks
        }
        return JSONResponse({
            "doc_id": doc_id,
            "filename": "text_input.txt",
            "chunk_count": len(chunks)
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process text: {str(e)}")


@app.get("/api/document/{doc_id}")
async def get_document(doc_id: str):
    """Get document chunks"""
    if doc_id not in TEXT_STORE:
        raise HTTPException(status_code=404, detail="Document not found")
    
    doc = TEXT_STORE[doc_id]
    return JSONResponse({
        "doc_id": doc_id,
        "filename": doc["filename"],
        "chunks": doc["chunks"]
    })


@app.post("/api/llm-process")
async def llm_process(req: LLMRequest):
    """Process text with LLM for optimization"""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{req.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {req.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": req.model,
                    "messages": [
                        {"role": "system", "content": "你是一个专业的文本优化助手，请将文本转换为更适合朗读的形式。"},
                        {"role": "user", "content": f"{req.prompt}\n\n{req.text}"}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4096
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"LLM API error: {response.text}")
            
            result = response.json()
            processed_text = result["choices"][0]["message"]["content"]
            
            # Re-split into chunks
            chunks = split_text_for_reading(processed_text)
            
            return JSONResponse({
                "processed_text": processed_text,
                "chunks": chunks
            })
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Request failed: {str(e)}")


@app.post("/api/tts")
async def text_to_speech(req: TTSRequest):
    """Convert text to speech using Cloud API or Local TTS"""
    try:
        audio_id = str(uuid.uuid4())[:8]
        audio_path = STATIC_DIR / "audio" / f"{audio_id}.mp3"
        audio_path.parent.mkdir(exist_ok=True)
        
        if req.use_local_tts:
            # Use edge-tts (Local, Free, High Quality)
            import edge_tts
            
            voice = req.voice or "zh-CN-XiaoxiaoNeural"
            communicate = edge_tts.Communicate(req.text, voice)
            await communicate.save(str(audio_path))
        else:
            # Use Cloud API (Dashscope or OpenAI compatible)
            api_base_lower = req.api_base.lower()
            
            if "dashscope" in api_base_lower or "aliyun" in api_base_lower:
                # Use Dashscope SDK
                import dashscope
                from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
                
                dashscope.api_key = req.api_key
                dashscope.base_websocket_api_url = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
                
                voice = req.voice or "longanyang"
                
                # Model-voice compatibility check
                if "v3" in req.model.lower():
                    if voice in ["longxiaochun_v2", "Chelsie", "Ethan", "Cherry", "Danny"]:
                        voice = "longanyang"
                elif "v2" in req.model.lower():
                    if voice in ["longanyang", "zhichu", "zhiyan"]:
                        voice = "longxiaochun_v2"
                
                class TTSCallback:
                    def __init__(self):
                        self.audio_data = b""
                        self.error_message = None
                    
                    def on_open(self): pass
                    def on_complete(self): pass
                    def on_close(self): pass
                    def on_event(self, message): pass
                    def on_error(self, message: str):
                        self.error_message = message
                    def on_data(self, data: bytes) -> None:
                        self.audio_data += data
                
                callback = TTSCallback()
                synthesizer = SpeechSynthesizer(
                    model=req.model,
                    voice=voice,
                    format=AudioFormat.PCM_22050HZ_MONO_16BIT,
                    callback=callback
                )
                synthesizer.call(req.text)
                
                if callback.error_message:
                    raise HTTPException(status_code=500, detail=f"TTS failed: {callback.error_message}")
                
                # Convert PCM to MP3 using pydub if available, else WAV
                try:
                    from pydub import AudioSegment
                    audio_segment = AudioSegment(
                        callback.audio_data, 
                        frame_rate=22050, 
                        sample_width=2, 
                        channels=1
                    )
                    audio_segment.export(str(audio_path), format="mp3")
                except ImportError:
                    # Fallback to WAV if pydub not installed
                    import wave
                    wav_path = STATIC_DIR / "audio" / f"{audio_id}.wav"
                    with wave.open(str(wav_path), 'wb') as wav_file:
                        wav_file.setnchannels(1)
                        wav_file.setsampwidth(2)
                        wav_file.setframerate(22050)
                        wav_file.writeframes(callback.audio_data)
                    return JSONResponse({
                        "audio_url": f"/static/audio/{audio_id}.wav",
                        "audio_id": audio_id
                    })
            else:
                # Standard OpenAI format
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        f"{req.api_base}/audio/speech",
                        headers={
                            "Authorization": f"Bearer {req.api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": req.model,
                            "input": req.text,
                            "voice": req.voice or "alloy",
                            "response_format": "mp3"
                        }
                    )
                    
                    if response.status_code != 200:
                        raise HTTPException(status_code=response.status_code, detail=f"TTS API error: {response.text}")
                    
                    audio_path.write_bytes(response.content)
        
        # Track audio file for this document
        if req.doc_id and req.doc_id in TEXT_STORE:
            if "audio_ids" not in TEXT_STORE[req.doc_id]:
                TEXT_STORE[req.doc_id]["audio_ids"] = []
            TEXT_STORE[req.doc_id]["audio_ids"].append(audio_id)
            
        return JSONResponse({
            "audio_url": f"/static/audio/{audio_id}.mp3",
            "audio_id": audio_id
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")


@app.delete("/api/audio/{audio_id}")
async def delete_audio(audio_id: str):
    """Delete temporary audio file"""
    audio_path = STATIC_DIR / "audio" / f"{audio_id}.mp3"
    if audio_path.exists():
        audio_path.unlink()
    return JSONResponse({"status": "ok"})


@app.post("/api/clear-cache")
async def clear_cache():
    """Clear all cached audio files and document data"""
    audio_dir = STATIC_DIR / "audio"
    deleted_count = 0
    if audio_dir.exists():
        for f in audio_dir.iterdir():
            if f.is_file():
                f.unlink()
                deleted_count += 1
    
    doc_count = len(TEXT_STORE)
    TEXT_STORE.clear()
    
    return JSONResponse({
        "status": "ok",
        "deleted_audio_files": deleted_count,
        "cleared_documents": doc_count
    })


@app.get("/api/download-full-audio/{doc_id}")
async def download_full_audio(doc_id: str):
    """Combine all audio chunks into a single file and return it"""
    try:
        from pydub import AudioSegment
        import io
        
        if doc_id not in TEXT_STORE:
            raise HTTPException(status_code=404, detail="Document not found")
        
        doc = TEXT_STORE[doc_id]
        audio_ids = doc.get("audio_ids", [])
        
        if not audio_ids:
            raise HTTPException(status_code=404, detail="No audio generated for this document")
        
        # Load and combine all audio segments
        combined = AudioSegment.empty()
        for audio_id in audio_ids:
            audio_path = STATIC_DIR / "audio" / f"{audio_id}.mp3"
            if audio_path.exists():
                segment = AudioSegment.from_mp3(audio_path)
                combined += segment
        
        # Export combined audio
        output = io.BytesIO()
        combined.export(output, format="mp3")
        output.seek(0)
        
        return Response(content=output.read(), media_type="audio/mpeg", headers={
            "Content-Disposition": f"attachment; filename={doc_id}_full_audio.mp3"
        })
    except ImportError:
        raise HTTPException(status_code=500, detail="pydub is required for audio combining")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to combine audio: {str(e)}")


def split_text_for_reading(text: str, max_chunk_length: int = 200) -> list[dict]:
    """Backward-compatible wrapper for the shared text processing module."""
    return _split_text_for_reading(text, max_chunk_length)


# Serve the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
