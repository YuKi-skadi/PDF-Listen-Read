"""PDF-Listen-Read 论文库的 AstrBot 知识库插件。

设计边界：PDF-Listen-Read 保存原 PDF、优化文本和文件夹结构；本插件只保存
AstrBot 原生知识库中的索引，并通过 paper_id/content_hash 做同步映射。用户应该
在朗读器里修改正文，插件会在下一次同步时重新索引，不在插件预览页里编辑文本。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
from astrbot.api import AstrBotConfig, llm_tool, logger, star
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.web import error_response, json_response
from astrbot.core.knowledge_base.kb_helper import KBHelper
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path


PLUGIN_NAME = "astrbot_plugin_pdf_library_kb"
STATE_FILE = "state.json"
MAX_CHUNKS_PER_INDEX_DOCUMENT = 96
EMBEDDING_BATCH_SIZE = 25


class ReaderClient:
    """Small async client for the stable PDF-Listen-Read HTTP API."""

    def __init__(self, base_url: str, integration_token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.integration_token = integration_token.strip()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.integration_token}"} if self.integration_token else {}

    async def _get(self, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(self._headers())
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{self.base_url}{path}", headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()

    async def list_papers(self) -> list[dict[str, Any]]:
        data = await self._get("/api/integration/papers")
        return list(data.get("papers", []))

    async def get_text(self, paper_id: str) -> dict[str, Any]:
        return await self._get(f"/api/papers/{paper_id}/text")

    async def get_knowledge(self, paper_id: str) -> dict[str, Any]:
        return await self._get(f"/api/integration/papers/{paper_id}/knowledge")

    async def get_tree(self) -> dict[str, Any]:
        return await self._get("/api/folders/tree")


class PaperLibraryKnowledge(star.Star):
    """Synchronize the reader library into AstrBot's native KB."""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / STATE_FILE
        self.state = self._load_state()
        self.sync_lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.sync_task = asyncio.create_task(self._sync_loop())

        context.register_web_api(
            f"/{PLUGIN_NAME}/stats", self.api_stats, ["GET"], "论文库知识库状态"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/providers",
            self.api_providers,
            ["GET"],
            "列出可用的 Embedding/Rerank Provider",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/documents",
            self.api_documents,
            ["GET"],
            "预览论文知识库文档",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/documents/<paper_id>",
            self.api_document,
            ["GET"],
            "预览单篇论文知识库内容",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/sync", self.api_sync, ["POST"], "同步论文知识库"
        )

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"kb_id": "", "papers": {}, "last_sync_at": "", "last_error": ""}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("kb_id", "")
                data.setdefault("papers", {})
                data.setdefault("last_sync_at", "")
                data.setdefault("last_error", "")
                return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("论文知识库状态文件读取失败，将创建新映射: %s", exc)
        return {"kb_id": "", "papers": {}, "last_sync_at": "", "last_error": ""}

    def _save_state(self) -> None:
        temp_path = self.state_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_path.replace(self.state_path)

    @staticmethod
    def _error_text(exc: Exception) -> str:
        """Keep AstrBot's structured KB cause visible to the plugin UI/log."""
        details = getattr(exc, "details", None)
        if details:
            return f"{exc} 详情: {details}"
        cause = getattr(exc, "__cause__", None)
        if cause and str(cause) and str(cause) != str(exc):
            return f"{exc} 底层原因: {cause}"
        return str(exc)

    def _cfg(self, key: str, default: Any = None) -> Any:
        value = self.config.get(key, default)
        return default if value is None else value

    def _reader(self) -> ReaderClient:
        return ReaderClient(
            str(self._cfg("reader_base_url", "http://127.0.0.1:8000")),
            str(self._cfg("reader_integration_token", "")),
        )

    def _kb_name(self) -> str:
        return str(self._cfg("knowledge_base_name", "PDF-Listen-Read 论文库")).strip()

    async def _get_embedding_id(self) -> str:
        configured = str(self._cfg("embedding_provider_id", "")).strip()
        if configured:
            return configured
        providers = self.context.provider_manager.embedding_provider_insts
        if providers:
            return str(providers[0].meta().id)
        raise RuntimeError("没有可用的 Embedding Provider，请先在 AstrBot 中配置模型")

    async def _ensure_kb(self) -> KBHelper:
        kb_manager = self.context.kb_manager
        embedding_id = await self._get_embedding_id()
        rerank_id = str(self._cfg("rerank_provider_id", "")).strip() or None
        kb_name = self._kb_name()
        if not kb_name:
            raise RuntimeError("知识库名称不能为空")

        kb_helper: KBHelper | None = None
        state_kb_id = str(self.state.get("kb_id", "")).strip()
        if state_kb_id:
            kb_helper = await kb_manager.get_kb(state_kb_id)
        if kb_helper is None:
            kb_helper = await kb_manager.get_kb_by_name(kb_name)
        if kb_helper is None:
            # A manually removed AstrBot KB invalidates every saved doc_id.
            # Clearing the mapping makes the following sync rebuild all papers
            # instead of incorrectly treating them as already indexed.
            if state_kb_id:
                self.state["papers"] = {}
                self.state["kb_id"] = ""
            kb_helper = await kb_manager.create_kb(
                kb_name=kb_name,
                description="由 PDF-Listen-Read 论文库插件同步，源数据以朗读器为准。",
                emoji="📚",
                embedding_provider_id=embedding_id,
                rerank_provider_id=rerank_id,
            )
        elif (
            kb_helper.kb.embedding_provider_id != embedding_id
            or kb_helper.kb.rerank_provider_id != rerank_id
        ):
            kb_helper = await kb_manager.update_kb(
                kb_id=kb_helper.kb.kb_id,
                kb_name=kb_helper.kb.kb_name,
                embedding_provider_id=embedding_id,
                rerank_provider_id=rerank_id,
            )
            if kb_helper is None:
                raise RuntimeError("AstrBot 知识库模型配置更新失败")

        self.state["kb_id"] = kb_helper.kb.kb_id
        self._save_state()
        return kb_helper

    @staticmethod
    def _content_hash(version: dict[str, Any], chunks: list[str], source: str) -> str:
        payload = "\n".join([source, str(version.get("id", "")), *chunks])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _chunks(text_data: dict[str, Any]) -> list[str]:
        chunks = [
            str(item.get("content", "")).strip()
            for item in text_data.get("segments", [])
            if str(item.get("content", "")).strip()
        ]
        if chunks:
            return chunks
        content = str(text_data.get("version", {}).get("content", "")).strip()
        return [content] if content else []

    @staticmethod
    def _knowledge_chunks(text_data: dict[str, Any]) -> list[str]:
        body_chunks = PaperLibraryKnowledge._chunks(text_data)
        info = text_data.get("knowledge") or {}
        metadata = []
        if str(info.get("title") or "").strip():
            metadata.append(f"论文标题：{str(info['title']).strip()}")
        if str(info.get("authors") or "").strip():
            metadata.append(f"作者：{str(info['authors']).strip()}")
        if str(info.get("abstract") or "").strip():
            metadata.append(f"摘要：{str(info['abstract']).strip()}")
        return (["\n".join(metadata)] if metadata else []) + body_chunks

    @staticmethod
    def _mapping_doc_ids(mapping: dict[str, Any]) -> list[str]:
        doc_ids = mapping.get("doc_ids")
        if isinstance(doc_ids, list):
            return [str(doc_id) for doc_id in doc_ids if doc_id]
        # Compatibility with the first plugin build, which stored one doc_id.
        if mapping.get("doc_id"):
            return [str(mapping["doc_id"])]
        return []

    async def _delete_mapping(self, helper: KBHelper, mapping: dict[str, Any]) -> None:
        for doc_id in self._mapping_doc_ids(mapping):
            try:
                await helper.delete_document(doc_id)
            except Exception as exc:
                logger.warning("删除旧论文索引失败 %s: %s", doc_id, self._error_text(exc))

    async def sync_now(self) -> dict[str, Any]:
        async with self.sync_lock:
            helper = await self._ensure_kb()
            papers = await self._reader().list_papers()
            current_ids = {str(paper.get("id", "")) for paper in papers if paper.get("id")}
            mappings: dict[str, dict[str, Any]] = self.state.setdefault("papers", {})
            added = updated = removed = skipped = 0

            for paper in papers:
                paper_id = str(paper.get("id", "")).strip()
                if not paper_id:
                    continue
                title = str(paper.get("title") or paper.get("original_filename") or paper_id)
                source_updated_at = str(paper.get("updated_at", ""))
                old = mappings.get(paper_id, {})
                # The reader updates this timestamp whenever a paper's active
                # text or title changes. Avoid downloading every full text on
                # each polling round when nothing has changed.
                if (
                    old.get("source_type") in {"manual", "deepseek_vision"}
                    and old.get("source_updated_at") == source_updated_at
                    and old.get("paper_title", old.get("title")) == title
                ):
                    skipped += 1
                    continue
                try:
                    text_data = await self._reader().get_knowledge(paper_id)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise
                    if old:
                        await self._delete_mapping(helper, old)
                        mappings.pop(paper_id, None)
                    logger.info("论文 %s 没有手动或读图知识库文本，跳过索引", paper_id)
                    continue
                knowledge = text_data.get("knowledge") or {}
                knowledge_title = str(knowledge.get("title") or title)
                source_type = str(text_data.get("source") or "")
                chunks = self._knowledge_chunks(text_data)
                if not chunks:
                    logger.warning("论文 %s 没有可索引文本，跳过", paper_id)
                    continue
                version = text_data.get("version", {})
                digest = self._content_hash(version, chunks, source_type)
                if old.get("content_hash") == digest and old.get("title") == knowledge_title:
                    old["source_updated_at"] = source_updated_at
                    mappings[paper_id] = old
                    skipped += 1
                    continue

                await self._delete_mapping(helper, old)
                doc_ids: list[str] = []
                try:
                    for batch_index, start in enumerate(
                        range(0, len(chunks), MAX_CHUNKS_PER_INDEX_DOCUMENT),
                        start=1,
                    ):
                        batch = chunks[start : start + MAX_CHUNKS_PER_INDEX_DOCUMENT]
                        doc = await helper.upload_document(
                            file_name=(
                                f"{paper_id} - {knowledge_title} "
                                f"[{batch_index}].txt"
                            ),
                            file_content=None,
                            file_type="txt",
                            pre_chunked_text=batch,
                            # DashScope/Qwen Embedding rejects batches > 25.
                            # AstrBot's KB default is 32, so override it here.
                            batch_size=EMBEDDING_BATCH_SIZE,
                        )
                        doc_ids.append(doc.doc_id)
                except Exception:
                    for doc_id in doc_ids:
                        try:
                            await helper.delete_document(doc_id)
                        except Exception as cleanup_exc:
                            logger.warning(
                                "清理失败的分块索引失败 %s: %s",
                                doc_id,
                                self._error_text(cleanup_exc),
                            )
                    raise
                mappings[paper_id] = {
                    "doc_ids": doc_ids,
                    "title": knowledge_title,
                    "paper_title": title,
                    "source_type": source_type,
                    "source_version_id": version.get("id", ""),
                    "knowledge_title": knowledge.get("title", ""),
                    "knowledge_authors": knowledge.get("authors", ""),
                    "knowledge_abstract": knowledge.get("abstract", ""),
                    "original_filename": paper.get("original_filename", ""),
                    "folder_name": paper.get("folder_name", "未分类"),
                    "page_count": paper.get("page_count", 0),
                    "content_hash": digest,
                    "source_updated_at": source_updated_at,
                    "segment_count": len(chunks),
                    "synced_at": int(time.time()),
                }
                if old:
                    updated += 1
                else:
                    added += 1
                self._save_state()

            for paper_id in list(mappings):
                if paper_id in current_ids:
                    continue
                old = mappings[paper_id]
                await self._delete_mapping(helper, old)
                mappings.pop(paper_id, None)
                removed += 1

            self.state["last_sync_at"] = int(time.time())
            self.state["last_error"] = ""
            self._save_state()
            return {
                "added": added,
                "updated": updated,
                "removed": removed,
                "skipped": skipped,
                "total": len(mappings),
                "kb_id": helper.kb.kb_id,
            }

    async def _sync_loop(self) -> None:
        await asyncio.sleep(2)
        while not self.stop_event.is_set():
            if bool(self._cfg("auto_sync", True)):
                try:
                    await self.sync_now()
                except Exception as exc:
                    message = self._error_text(exc)
                    self.state["last_error"] = message
                    self._save_state()
                    logger.warning("论文知识库自动同步失败: %s", message)
            interval = max(1, int(self._cfg("sync_interval_minutes", 10))) * 60
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue

    @staticmethod
    def _provider_info(provider: Any) -> dict[str, Any]:
        try:
            meta = provider.meta()
            return {"id": meta.id, "model": meta.model, "type": meta.type}
        except Exception:
            return {
                "id": str(provider.provider_config.get("id", "")),
                "model": str(provider.get_model()),
                "type": provider.__class__.__name__,
            }

    async def _preview(self, paper_id: str | None = None) -> dict[str, Any]:
        helper = await self._ensure_kb()
        if paper_id:
            mapping = self.state.get("papers", {}).get(paper_id)
            if not mapping:
                raise ValueError("论文尚未同步到知识库")
            chunks = []
            for doc_id in self._mapping_doc_ids(mapping):
                chunks.extend(
                    await helper.get_chunks_by_doc_id(doc_id, offset=0, limit=100)
                )
            return {"paper": mapping, "chunks": chunks}
        docs = []
        for pid, mapping in self.state.get("papers", {}).items():
            docs.append({"paper_id": pid, **mapping})
        docs.sort(key=lambda item: str(item.get("title", "")).lower())
        return {"documents": docs, "total": len(docs)}

    async def api_stats(self):
        try:
            kb = await self._ensure_kb()
            return json_response(
                {
                    "kb_id": kb.kb.kb_id,
                    "kb_name": kb.kb.kb_name,
                    "document_count": await kb.count_documents(),
                    "mapped_papers": len(self.state.get("papers", {})),
                    "last_sync_at": self.state.get("last_sync_at", ""),
                    "last_error": self.state.get("last_error", ""),
                    "reader_base_url": self._cfg("reader_base_url", ""),
                }
            )
        except Exception as exc:
            return json_response(
                {
                    "kb_name": self._kb_name(),
                    "mapped_papers": len(self.state.get("papers", {})),
                    "last_sync_at": self.state.get("last_sync_at", ""),
                    "last_error": self._error_text(exc),
                }
            )

    async def api_providers(self):
        manager = self.context.provider_manager
        return json_response(
            {
                "embedding": [
                    self._provider_info(provider)
                    for provider in manager.embedding_provider_insts
                ],
                "rerank": [
                    self._provider_info(provider)
                    for provider in manager.rerank_provider_insts
                ],
            }
        )

    async def api_documents(self):
        try:
            return json_response(await self._preview())
        except Exception as exc:
            return error_response(str(exc), status_code=503)

    async def api_document(self, paper_id: str):
        try:
            return json_response(await self._preview(paper_id))
        except ValueError as exc:
            return error_response(str(exc), status_code=404)
        except Exception as exc:
            return error_response(str(exc), status_code=503)

    async def api_sync(self):
        try:
            return json_response(await self.sync_now())
        except Exception as exc:
            message = self._error_text(exc)
            self.state["last_error"] = message
            self._save_state()
            return error_response(message, status_code=503)

    @filter.command("paper_kb_sync")
    async def command_sync(self, event: AstrMessageEvent) -> None:
        """手动同步 PDF-Listen-Read 论文库。"""
        try:
            result = await self.sync_now()
            event.set_result(MessageEventResult().message(f"论文库同步完成：{result}"))
        except Exception as exc:
            event.set_result(
                MessageEventResult().message(
                    f"论文库同步失败：{self._error_text(exc)}"
                )
            )

    @filter.command("paper_kb_preview")
    async def command_preview(self, event: AstrMessageEvent) -> None:
        """预览当前已同步的论文列表。"""
        try:
            data = await self._preview()
            lines = [f"论文知识库：{data['total']} 篇"]
            for item in data["documents"][:30]:
                lines.append(f"- {item['title']}（{item.get('folder_name') or '未分类'}）")
            event.set_result(MessageEventResult().message("\n".join(lines)))
        except Exception as exc:
            event.set_result(MessageEventResult().message(f"预览失败：{exc}"))

    @llm_tool("search_paper_knowledge")
    async def search_paper_knowledge(self, query: str) -> str:
        """检索 PDF-Listen-Read 论文知识库并返回带来源的相关片段。

        Args:
            query(string): 用户想从论文中查找或核对的问题。
        """
        if not query.strip():
            return "查询不能为空。"
        try:
            result = await self.context.kb_manager.retrieve(
                query=query,
                kb_names=[self._kb_name()],
                top_k_fusion=20,
                top_m_final=6,
            )
            if not result:
                return "论文知识库中没有找到相关内容。"
            return str(result.get("context_text", "没有找到相关内容。"))
        except Exception as exc:
            return f"论文知识库检索失败：{exc}"

    async def terminate(self) -> None:
        self.stop_event.set()
        self.sync_task.cancel()
        try:
            await self.sync_task
        except asyncio.CancelledError:
            pass


@star.register(
    PLUGIN_NAME,
    "PDF-Listen-Read",
    "将 PDF-Listen-Read 论文同步到 AstrBot 原生知识库并提供检索工具",
    "0.1.2",
)
class RegisteredPaperLibraryKnowledge(PaperLibraryKnowledge):
    """AstrBot plugin entrypoint kept separate for clearer test imports."""
