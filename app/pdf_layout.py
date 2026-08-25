"""Layout-aware PDF text extraction.

PDF text objects are not necessarily stored in reading order.  This module
uses pypdf's layout extraction as the first deterministic pass.  The spaces
inserted by that mode preserve the horizontal relationship between columns,
which is considerably safer than concatenating visitor callbacks.  We then
split stable two-column regions into small, identifiable blocks.  An optional
LLM pass in ``server.py`` can reorder those blocks by ID, while the original
block text remains available for integrity checks and manual review.
"""

from __future__ import annotations

import re
from collections import Counter
from statistics import median
from typing import Any


_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"


def _clean(text: str) -> str:
    """Normalize extraction noise without removing meaningful word spaces."""

    value = (text or "").replace("\u00a0", " ").replace("\u3000", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    # Layout extraction often puts a space between adjacent Chinese glyphs
    # or before Chinese punctuation.  English word spacing is preserved.
    value = re.sub(rf"(?<=[{_CJK}])[ ]+(?=[{_CJK}0-9])", "", value)
    value = re.sub(rf"(?<=[{_CJK}])[ ]+(?=[，。；：！？、）》】』」])", "", value)
    value = re.sub(r"[ ]+([，。；：！？、）》】』」])", r"\1", value)
    value = re.sub(r"([（《【『「])[ ]+", r"\1", value)
    return value.strip()


def _extract_layout_text(page: Any) -> str:
    """Use pypdf's layout mode, with a compatible fallback for older pypdf."""

    try:
        return page.extract_text(extraction_mode="layout") or ""
    except (TypeError, ValueError):
        return page.extract_text() or ""
    except Exception:
        return ""


def _extract_page_lines(page: Any) -> list[dict[str, Any]]:
    """Return non-empty visual rows while retaining their raw column spaces."""

    raw_text = _extract_layout_text(page)
    lines: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_text.replace("\t", "    ").splitlines()):
        raw = raw.rstrip()
        if not raw.strip():
            continue
        text = _clean(raw)
        if not text:
            continue
        lines.append(
            {
                "id": index,
                "line_index": index,
                "raw": raw,
                "text": text,
                "leading": len(raw) - len(raw.lstrip(" ")),
            }
        )
    return lines


def _split_candidate(raw: str) -> tuple[int, int, str, str] | None:
    """Find a likely left/right column gap in one layout row.

    A large interior gap is required and both sides must contain enough text.
    This prevents centered titles and letter-spaced headers from being treated
    as columns.
    """

    matches: list[tuple[int, int, str, str]] = []
    for match in re.finditer(r" {10,}", raw):
        start, end = match.span()
        left = _clean(raw[:start])
        right = _clean(raw[end:])
        if len(left) < 10 or len(right) < 10:
            continue
        matches.append((start, end, left, right))
    if not matches:
        return None
    # The largest interior gap is normally the gutter between columns.  This
    # avoids mistaking spaces inside a right-column sentence (for example
    # between a Chinese phrase and a year) for the column boundary.
    return max(matches, key=lambda item: item[1] - item[0])


def _column_boundary(lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for line in lines:
        split = _split_candidate(line["raw"])
        if split is None:
            continue
        start, end, left, right = split
        candidates.append(
            {
                "line_index": line["line_index"],
                "start": start,
                "end": end,
                "left": left,
                "right": right,
            }
        )
    if len(candidates) < 3:
        return None

    # The left fragment's length varies with the words on each row, but the
    # right column normally starts at one stable character position.  Cluster
    # the *right* edge of the gap rather than the left edge; this is important
    # for PDFs whose text objects are emitted as a merged left/right callback.
    buckets = Counter(round(item["end"] / 8) for item in candidates)
    bucket, count = buckets.most_common(1)[0]
    stable_end = bucket * 8
    stable = [item for item in candidates if abs(item["end"] - stable_end) <= 8]
    if count < 3 or len(stable) < 3:
        return None
    stable.sort(key=lambda item: item["line_index"])
    return {
        "start": int(round(median(item["start"] for item in stable))),
        "end": int(round(median(item["end"] for item in stable))),
        # Include a couple of rows before the first obvious gutter only when
        # they look like body text.  A top page header must remain full-width.
        "first_line": stable[0]["line_index"] if stable[0]["line_index"] <= 8 else max(0, stable[0]["line_index"] - 2),
        "candidates": {item["line_index"]: item for item in stable},
    }


def _join_lines(items: list[dict[str, Any]]) -> str:
    values = [item["text"].strip() for item in sorted(items, key=lambda item: item["line_index"]) if item["text"].strip()]
    if not values:
        return ""
    result = values[0]
    for value in values[1:]:
        if re.search(r"[A-Za-z0-9)]$", result) and re.match(r"^[A-Za-z0-9(]", value):
            result += " " + value
        else:
            result += value
    return _clean(result)


def _looks_like_metadata(text: str) -> bool:
    value = text.lower()
    return any(
        marker in value
        for marker in (
            "收稿日期",
            "修改日期",
            "基金项目",
            "作者简介",
            "通信作者",
            "引文格式",
            "cite this article",
            "issn ",
            "doi:",
            "http://",
            "https://",
        )
    )


def _region_blocks(page_number: int, region: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group visual rows into manageable LLM units without losing their IDs."""

    ordered = sorted(items, key=lambda item: item["line_index"])
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    char_count = 0
    previous_index: int | None = None
    for item in ordered:
        gap = item["line_index"] - previous_index if previous_index is not None else 0
        if current and (gap > 1 or char_count >= 520):
            groups.append(current)
            current = []
            char_count = 0
        current.append(item)
        char_count += len(item["text"])
        previous_index = item["line_index"]
    if current:
        groups.append(current)

    blocks: list[dict[str, Any]] = []
    for number, group in enumerate(groups, 1):
        text = _join_lines(group)
        if not text:
            continue
        blocks.append(
            {
                "id": f"p{page_number}-{region}-{number}",
                "page": page_number,
                "region": region,
                "line_start": group[0]["line_index"],
                "line_end": group[-1]["line_index"],
                "text": text,
            }
        )
    return blocks


def _rebuild_page(page: dict[str, Any]) -> None:
    lines = page["lines"]
    boundary = _column_boundary(lines)
    page["has_columns"] = boundary is not None
    if boundary is None:
        page["blocks"] = _region_blocks(page["page"], "full", lines)
        return

    start = boundary["start"]
    end = boundary["end"]
    first_line = boundary["first_line"]
    candidates = boundary["candidates"]
    prefix: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    right: list[dict[str, Any]] = []
    body_full: list[dict[str, Any]] = []

    for line in lines:
        if line["line_index"] < first_line:
            prefix.append(line)
            continue
        raw = line["raw"]
        leading = line["leading"]
        # Some producers omit the gutter on rows where a text object crosses
        # the nominal column boundary.  The layout renderer still places the
        # right column at a stable character position, so use that position
        # as a second-pass split.  It prevents text such as ``...社会`` +
        # ``实验室`` from being glued into one column block.
        fixed_left = raw[:end].rstrip()
        fixed_right = raw[end:].strip()
        # Rows beginning near the right column are continuations of that
        # column.  Short centered/full-width rows are retained separately so
        # the LLM can place headings or captions using their block IDs.
        if _looks_like_metadata(line["text"]):
            body_full.append(line)
        elif leading >= end - 4:
            right.append({**line, "text": _clean(raw), "raw": _clean(raw)})
        elif len(fixed_right) >= 3:
            # Use the stable right-column start for rows where pypdf did not
            # expose a sufficiently large whitespace gutter.  This is the
            # key safeguard against cross-column callbacks being glued into
            # one paragraph.
            left.append({**line, "text": _clean(fixed_left), "raw": fixed_left})
            right.append({**line, "text": _clean(fixed_right), "raw": fixed_right})
        elif leading <= max(2, start // 3):
            left.append(line)
        else:
            body_full.append(line)

    blocks: list[dict[str, Any]] = []
    for region, items in (
        ("full-prefix", prefix),
        ("left", left),
        ("right", right),
        ("full-body", body_full),
    ):
        blocks.extend(_region_blocks(page["page"], region, items))
    page["blocks"] = blocks


def _canonical(text: str) -> str:
    return re.sub(r"\W+", "", text.lower(), flags=re.UNICODE)


def extract_pdf_layout(reader: Any) -> dict[str, Any] | None:
    """Extract a safe layout representation and deterministic initial order."""

    try:
        pages: list[dict[str, Any]] = []
        for number, page in enumerate(reader.pages, 1):
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            pages.append(
                {
                    "page": number,
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "lines": _extract_page_lines(page),
                    "blocks": [],
                    "has_columns": False,
                }
            )
    except Exception:
        return None

    if not any(page["lines"] for page in pages):
        return None

    # Remove repeated headers, footers, and isolated page numbers, but keep
    # one-off metadata in the block stream for the LLM to classify explicitly.
    occurrences: Counter[str] = Counter()
    for page in pages:
        for line in page["lines"]:
            if line["line_index"] <= 8 or line["line_index"] >= 90:
                key = _canonical(line["text"])
                if len(key) >= 3:
                    occurrences[key] += 1
    repeated = {key for key, count in occurrences.items() if count >= 2}
    for page in pages:
        page["lines"] = [
            line
            for line in page["lines"]
            if not (
                _canonical(line["text"]) in repeated
                and (line["line_index"] <= 8 or line["line_index"] >= 90)
            )
            # PDF 页脚页码可能是全角数字，或被提取器拆成带空格的数字。
            # 这些不是正文内容，不能让它们进入后续整理/朗读文本。
            and not re.fullmatch(
                r"[0-9０-９]+",
                re.sub(r"\s+", "", line["text"]).strip(),
            )
        ]
        _rebuild_page(page)
        page["text"] = "\n\n".join(block["text"] for block in page["blocks"] if block["text"].strip()).strip()

    blocks = [block for page in pages for block in page["blocks"] if block["text"].strip()]
    return {
        "engine": "pypdf-layout-v2",
        "pages": pages,
        "blocks": blocks,
        "text": "\n\n".join(block["text"] for block in blocks).strip(),
    }
