"""DeepSeek vision extraction for PDF body text."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Awaitable, Callable

import httpx

try:
    import pymupdf
except ImportError as exc:  # pragma: no cover - exercised only in incomplete installs
    try:
        import fitz as pymupdf
    except ImportError:
        raise RuntimeError("DeepSeek 读图需要安装 PyMuPDF") from exc


VISION_BODY_PROMPT = """请阅读这张论文页面图片，只提取论文的纯正文。

严格要求：
1. 不要输出论文标题、摘要、Abstract、目录、作者信息、单位、基金项目、通讯作者信息。
2. 不要输出参考文献、References、致谢、附录、页眉、页脚、页码、期刊信息、DOI 或网址。
3. 不要输出图、表、公式图片、图题、表题或图表说明；如果页面只有这些内容，直接输出空字符串。
4. 保持正文原文和段落顺序，不要总结、改写、翻译、补写或添加解释。
5. 如果这一页是正文开始前的标题/摘要/目录页，或者已经进入参考文献页，只输出空字符串。
6. 直接输出正文文本，不要加“正文：”、Markdown 代码块、前言或后记。"""


VISION_METADATA_PROMPT = """请阅读这些论文首页图片，提取用于知识库检索的论文元信息，只返回一个 JSON 对象，不要输出 Markdown 或解释。

JSON 字段必须是：
{
  "title": "论文标题",
  "authors": "作者，多个作者用逗号分隔",
  "abstract": "中文摘要；如果论文没有中文摘要，则保留原文摘要；如果确实没有摘要则为空字符串"
}

要求：
1. 标题、作者和摘要必须来自图片原文，不要猜测或补写。
2. 摘要优先选择中文摘要；没有中文摘要时保留英文或论文原文摘要。
3. 不要把单位、关键词、基金、通讯作者说明写入作者字段或摘要字段。
4. 字段内容使用纯文本，保持公式和英文术语可读。"""


ProgressCallback = Callable[[int, int], Awaitable[None]]


def _chat_endpoint(api_base: str) -> str:
    return f"{api_base.rstrip('/')}/chat/completions"


def _text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(content or "")


def clean_page_body(text: str) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    value = re.sub(r"^```(?:text|markdown)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value).strip()
    value = re.sub(r"^(?:正文|正文内容|提取结果)\s*[:：]\s*", "", value).strip()
    if value in {"空", "空字符串", "无", "无正文", "没有正文", "None", "null"}:
        return ""

    kept: list[str] = []
    for line in value.splitlines():
        compact = re.sub(r"\s+", "", line)
        if not compact:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        # Models occasionally return a standalone caption despite the prompt.
        if re.match(r"^(?:图|表|Figure|Fig\.?|Table)\s*[0-9一二三四五六七八九十]*(?:[.．:：、]|\s|$)", compact, re.I):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _parse_metadata(content: object) -> dict[str, str]:
    raw = _text_from_content(content).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"title": "", "authors": "", "abstract": ""}
    if not isinstance(data, dict):
        return {"title": "", "authors": "", "abstract": ""}
    return {
        "title": str(data.get("title") or "").strip(),
        "authors": str(data.get("authors") or "").strip(),
        "abstract": str(data.get("abstract") or "").strip(),
    }


async def extract_pdf_metadata(
    pdf_path: Path,
    api_key: str,
    api_base: str,
    model: str,
) -> dict[str, str]:
    """Read title, authors and abstract from the first few PDF pages."""
    if not api_key.strip() or not model.strip() or not pdf_path.exists():
        raise ValueError("无法读取论文元信息")
    document = pymupdf.open(str(pdf_path))
    try:
        if not len(document):
            return {"title": "", "authors": "", "abstract": ""}
        content: list[dict[str, object]] = [{"type": "text", "text": VISION_METADATA_PROMPT}]
        for page in list(document)[:3]:
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
            image_data = base64.b64encode(pixmap.tobytes("jpeg", jpg_quality=84)).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}})
        timeout = httpx.Timeout(connect=30.0, read=180.0, write=60.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                _chat_endpoint(api_base),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": 4096},
            )
        if response.status_code != 200:
            raise ValueError(f"DeepSeek 论文元信息识别失败：{response.text[:500]}")
        choices = response.json().get("choices") or []
        return _parse_metadata(choices[0].get("message", {}).get("content", "")) if choices else {"title": "", "authors": "", "abstract": ""}
    finally:
        document.close()


async def extract_pdf_body(
    pdf_path: Path,
    api_key: str,
    api_base: str,
    model: str,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Render each page and ask DeepSeek to return only body paragraphs."""
    if not api_key.strip():
        raise ValueError("DeepSeek 读图需要先填写 API Key")
    if not model.strip():
        raise ValueError("请先选择 DeepSeek 读图模型")
    if not pdf_path.exists():
        raise ValueError("原始 PDF 不存在")

    document = pymupdf.open(str(pdf_path))
    try:
        total = len(document)
        if not total:
            raise ValueError("PDF 没有可读取的页面")
        timeout = httpx.Timeout(connect=30.0, read=180.0, write=60.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            page_outputs: list[str] = []
            for page_number, page in enumerate(document, start=1):
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
                image_bytes = pixmap.tobytes("jpeg", jpg_quality=84)
                image_data = base64.b64encode(image_bytes).decode("ascii")
                response = await client.post(
                    _chat_endpoint(api_base),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": VISION_BODY_PROMPT + f"\n\n这是第 {page_number}/{total} 页。"},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                            ],
                        }],
                        "temperature": 0,
                        "max_tokens": 8192,
                    },
                )
                if response.status_code != 200:
                    detail = response.text[:500]
                    raise ValueError(f"DeepSeek 读图请求失败（第 {page_number} 页）：{detail}")
                result = response.json()
                choices = result.get("choices") or []
                if not choices:
                    raise ValueError(f"DeepSeek 读图没有返回结果（第 {page_number} 页）")
                page_text = clean_page_body(_text_from_content(choices[0].get("message", {}).get("content", "")))
                if page_text:
                    page_outputs.append(page_text)
                if progress_callback:
                    await progress_callback(page_number, total)
            return "\n\n".join(page_outputs).strip()
    finally:
        document.close()
