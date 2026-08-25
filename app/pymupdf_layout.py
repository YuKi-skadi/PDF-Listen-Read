"""PDF extraction based on PyMuPDF text blocks and their page geometry.

The module deliberately does not ask an LLM to infer geometry.  PyMuPDF is
used for the physical facts (text, bounding boxes and spans); the application
may later ask an LLM to verify order and classify the text content.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_PUNCTUATION = "，。；：！？、）》」』】》〉’”" 


def _clean(text: str) -> str:
    value = (text or "").replace("\u00a0", " ").replace("\u3000", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    # PyMuPDF can insert a newline inside one logical Chinese word when a
    # glyph is represented by a separate text object.
    value = re.sub(rf"(?<=[{_CJK}])[\s]+(?=[{_CJK}0-9])", "", value)
    value = re.sub(rf"(?<=[{_CJK}])[\s]+(?=[{re.escape(_PUNCTUATION)}])", "", value)
    value = re.sub(rf"[ ]+(?=[{re.escape(_PUNCTUATION)}])", "", value)
    value = re.sub(r"([（【『「《〈]) +", r"\1", value)
    return value.strip()


def _is_page_number_noise(text: str, bbox: list[float], page_width: float, page_height: float) -> bool:
    """Recognize page numbers, including PDFs that split two digits vertically."""
    compact = re.sub(r"[\s\u200b]+", "", str(text or ""))
    if not compact or not re.fullmatch(r"[0-9０-９]{1,4}|(?:19|20)[0-9０-９]{2}[·.．][0-9０-９]{1,3}", compact):
        return False
    if len(bbox) != 4:
        return False
    x0, y0, x1, y1 = bbox
    near_footer = y0 >= page_height * 0.86
    near_margin = x1 <= page_width * 0.08 or x0 >= page_width * 0.92
    # Most journal PDFs put the page number at the bottom; the margin check
    # is intentionally permissive because some use a centered footer.
    return near_footer or (near_margin and y0 >= page_height * 0.70)


def _canonical(text: str) -> str:
    return re.sub(r"\W+", "", text.lower(), flags=re.UNICODE)


def _block_text(block: dict[str, Any], page_width: float) -> tuple[str, list[dict[str, Any]], float]:
    """Return visual line fragments instead of blindly joining every span.

    A PDF text block is a storage detail, not necessarily a visual paragraph.
    Journals often store a DOI, a title, and an author line in one block, and
    can put unrelated text objects on the same PDF line.  Joining all spans
    from that line was the reason some title-page material and column text
    became one huge candidate block.  Split large horizontal gaps while
    retaining adjacent spans that form one visual line.
    """
    lines: list[str] = []
    line_rows: list[dict[str, Any]] = []
    max_size = 0.0
    for line_index, line in enumerate(block.get("lines", [])):
        spans = []
        for span in line.get("spans", []):
            text = _clean(str(span.get("text", "")))
            bbox = list(span.get("bbox", []))
            if not text or len(bbox) != 4:
                continue
            size = float(span.get("size", 0) or 0)
            spans.append({"text": text, "bbox": bbox, "size": size})
            max_size = max(max_size, size)
        spans.sort(key=lambda item: (float(item["bbox"][0]), float(item["bbox"][1])))
        if not spans:
            continue

        fragments: list[list[dict[str, Any]]] = []
        for span in spans:
            if not fragments:
                fragments.append([span])
                continue
            previous = fragments[-1][-1]
            gap = float(span["bbox"][0]) - float(previous["bbox"][2])
            fragment_left = float(fragments[-1][0]["bbox"][0])
            fragment_right = float(previous["bbox"][2])
            font_size = max(float(previous["size"]), float(span["size"]), 1.0)
            # Normal glyph/span gaps are small.  A large gap is usually a
            # separate DOI/header/title object, not whitespace in a sentence.
            # The second condition catches a full-width left/right pair whose
            # gutter is small but whose two fragments occupy separate columns.
            split_for_gap = gap > max(8.0, font_size * 1.65)
            fragment_width = fragment_right - fragment_left
            split_for_columns = (
                # A line that crosses the gutter is represented as one PDF
                # line containing a left-column fragment followed by a
                # right-column fragment.  Do not confuse that with ordinary
                # word spacing inside a full-width title or paragraph.
                gap > 4.0
                and fragment_left < page_width * 0.20
                and fragment_width < page_width * 0.50
                and fragment_right <= page_width * 0.54
                and float(span["bbox"][0]) >= page_width * 0.46
            )
            if split_for_gap or split_for_columns:
                fragments.append([span])
            else:
                fragments[-1].append(span)

        for fragment in fragments:
            text = ""
            bbox = list(fragment[0]["bbox"])
            size = 0.0
            for span in fragment:
                joiner = " " if text and re.search(r"[A-Za-z0-9)]$", text) and re.match(r"^[A-Za-z0-9(]", span["text"]) else ""
                text = _clean(f"{text}{joiner}{span['text']}")
                bbox = [
                    min(bbox[0], span["bbox"][0]),
                    min(bbox[1], span["bbox"][1]),
                    max(bbox[2], span["bbox"][2]),
                    max(bbox[3], span["bbox"][3]),
                ]
                size = max(size, float(span["size"]))
            if text:
                lines.append(text)
                line_rows.append({
                    "line_index": line_index,
                    "text": text,
                    "bbox": bbox,
                    "font_size": size,
                })
    return _clean("\n".join(lines)), line_rows, max_size


def _make_blocks(page_number: int, page: Any) -> tuple[list[dict[str, Any]], bool]:
    page_dict = page.get_text("dict", sort=False)
    candidates: list[dict[str, Any]] = []
    width = float(page.rect.width)
    height = float(page.rect.height)
    table_regions: list[tuple[int, list[float]]] = []
    try:
        detected_tables = page.find_tables().tables
    except Exception:
        detected_tables = []
    for table_index, table in enumerate(detected_tables):
        table_bbox = [round(float(value), 2) for value in getattr(table, "bbox", ())]
        row_count = len(getattr(table, "rows", ()) or ())
        table_width = table_bbox[2] - table_bbox[0] if len(table_bbox) == 4 else 0
        # Ignore publisher frames/header panels. A real content table normally
        # has several rows and occupies only part of the page width.
        if len(table_bbox) == 4 and row_count >= 3 and table_width < width * 0.92:
            table_regions.append((table_index, table_bbox))
    for raw_index, raw_block in enumerate(page_dict.get("blocks", [])):
        if raw_block.get("type") != 0:
            continue
        text, line_rows, font_size = _block_text(raw_block, width)
        bbox = [round(float(value), 2) for value in raw_block.get("bbox", [])]
        if not text or len(bbox) != 4 or _is_page_number_noise(text, bbox, width, height):
            continue
        # A PDF text block may contain multiple lines whose glyphs are
        # interleaved with citation markers or neighbouring columns. Keep
        # physical lines separate until the row/column sort is complete.
        rows = line_rows or [{"line_index": 0, "text": text, "bbox": bbox}]
        for row in rows:
            row_bbox = [round(float(value), 2) for value in row.get("bbox", [])]
            row_text = _clean(str(row.get("text", "")))
            if not row_text or len(row_bbox) != 4 or _is_page_number_noise(row_text, row_bbox, width, height):
                continue
            row_size = max(float(row.get("font_size", 0) or 0), font_size, 0.0)
            candidates.append({
                "raw_index": raw_index * 1000 + int(row.get("line_index", 0)),
                "bbox": row_bbox,
                "text": row_text,
                "lines": [row],
                "font_size": round(row_size, 2),
                "block_width": max(0.0, row_bbox[2] - row_bbox[0]),
                "table_index": next(
                    (table_index for table_index, table_bbox in table_regions
                     if min(row_bbox[2], table_bbox[2]) > max(row_bbox[0], table_bbox[0])
                     and min(row_bbox[3], table_bbox[3]) > max(row_bbox[1], table_bbox[1])),
                    None,
                ),
            })

    narrow = [item for item in candidates if item["block_width"] < width * 0.72]
    left = [item for item in narrow if item["bbox"][0] < width * 0.50 and item["bbox"][2] <= width * 0.58]
    right = [item for item in narrow if item["bbox"][0] >= width * 0.42 and item["bbox"][2] > width * 0.50]
    has_columns = len(left) >= 2 and len(right) >= 2

    # A figure caption can be split into several PDF text objects on the same
    # visual line, e.g. ``图`` and ``１ 高校文科实验室……``.  Mark every
    # fragment from that raw line as figure material so the caption cannot
    # leak into the body when the label itself is removed.
    figure_label_y = [
        float(item["bbox"][1])
        for item in candidates
        if re.match(r"^(?:图|表)$", str(item.get("text", "")).strip())
        or re.match(r"^(?:图|表)[\s　]*[A-Za-zＡ-Ｚａ-ｚ0-9０-９一二三四五六七八九十]+", str(item.get("text", "")).strip(), flags=re.I)
    ]

    column_items = left + right if has_columns else narrow
    # A large section heading on a continuation page is not evidence that the
    # page is a title page.  Restrict title-page handling to a genuinely large
    # centered title near the top of the page.
    title_page = any(
        item["font_size"] >= 25
        and item["bbox"][1] < height * 0.28
        and item["block_width"] >= width * 0.45
        and width * 0.22 <= (item["bbox"][0] + item["bbox"][2]) / 2 <= width * 0.78
        for item in candidates
    )
    if title_page:
        # On a title page, the abstract and author information are full-width
        # even when their text blocks happen to be narrow.  The first real
        # two-column band usually begins below the upper half of the page.
        body_top = min((item["bbox"][1] for item in column_items if item["bbox"][1] >= height * 0.50), default=height * 0.50)
    else:
        body_top = min((item["bbox"][1] for item in column_items), default=height * 0.20)
    body_bottom = max((item["bbox"][3] for item in column_items), default=height * 0.80)
    for item in candidates:
        x0, y0, x1, y1 = item["bbox"]
        label_prefix = str(item["text"]).lstrip()
        figure_caption = any(abs(float(item["bbox"][1]) - label_y) <= 4 for label_y in figure_label_y) or bool(re.match(
            r"^(?:图|表)(?:[\s　]*[A-Za-zＡ-Ｚａ-ｚ0-9０-９一二三四五六七八九十]+)?$|^(?:图|表)[\s　]*[A-Za-zＡ-Ｚａ-ｚ0-9０-９一二三四五六七八九十]+|^(?:Fig(?:ure)?\.?|Table\.?)\s*[A-Za-z0-9]+",
            label_prefix,
            flags=re.I,
        ))
        if not has_columns:
            region = "full"
        elif title_page and y1 <= body_top + 4:
            region = "full-prefix"
        elif figure_caption:
            region = "figure"
        elif title_page and label_prefix.startswith(("【摘要", "【关键词", "【中图分类号", "【文献标识码", "【作者简介")):
            region = "full-prefix"
        elif title_page and y0 < height * 0.50:
            region = "full-prefix"
        elif item["block_width"] >= width * 0.72:
            region = "full-body"
        elif x0 < width * 0.50 and x1 <= width * 0.58:
            region = "left"
        else:
            region = "right"
        # Do not use a large top/bottom percentage here. In multi-column
        # journals the previous column may continue at the very top of the
        # next page, and the last body line may sit close to the footer.
        # Repeated chrome is removed later; these narrow bands only catch
        # unmistakable page furniture.
        if y0 <= height * 0.075:
            region = "header"
        elif y0 >= height * 0.955:
            region = "footer"
        item["region"] = region

    candidates = _merge_adjacent_blocks(candidates, page_number, width, height)
    region_rank = {"header": 0, "full-prefix": 1, "full": 1, "full-body": 2, "left": 3, "right": 4, "figure": 5, "full-suffix": 6, "footer": 7}
    candidates.sort(key=lambda item: (region_rank.get(item["region"], 9), round(float(item["bbox"][1]) / 3), item["bbox"][0]))
    blocks: list[dict[str, Any]] = []
    counters: Counter[str] = Counter()
    for item in candidates:
        region = item["region"]
        counters[region] += 1
        block_id = f"p{page_number}-{region}-{counters[region]}"
        blocks.append({
            "id": block_id,
            "page": page_number,
            "region": region,
            "line_start": int(item["raw_index"]),
            "line_end": int(item["raw_index"]),
            "bbox": item["bbox"],
            "font_size": item["font_size"],
            "text": item["text"],
            "table_index": item.get("table_index"),
        })
    return blocks, has_columns


def _merge_adjacent_blocks(items: list[dict[str, Any]], page_number: int, width: float, height: float) -> list[dict[str, Any]]:
    """Turn PyMuPDF's line-sized blocks into readable paragraph-sized modules."""
    region_order = {"header": 0, "full-prefix": 1, "full": 1, "full-body": 2, "left": 3, "right": 4, "figure": 5, "full-suffix": 6, "footer": 7}

    def is_heading_only(value: str) -> bool:
        compact = re.sub(r"\s+", "", str(value or "").strip())
        return bool(re.fullmatch(r"(?:[一二三四五六七八九十百]+、|（[一二三四五六七八九十百]+）|[0-9０-９]+(?:[.．·][0-9０-９]+)?(?:[、.)）]|\s+))[^。！？；：]{1,40}", compact))

    def is_section_heading(value: str) -> bool:
        compact = re.sub(r"\s+", "", str(value or "").strip())
        if re.match(r"^[0-9０-９]+(?:[.．·][0-9０-９]+)?", compact):
            prefix = re.match(r"^[0-9０-９]+(?:[.．·][0-9０-９]+)?", compact).group(0)
            rest = compact[len(prefix):]
            return bool(rest) and (rest[0] in "、.)）" or "\u3400" <= rest[0] <= "\u9fff")
        return bool(re.match(r"^(?:[一二三四五六七八九十百]+、|（[一二三四五六七八九十百]+）)", compact))

    def join_items(row: list[dict[str, Any]]) -> dict[str, Any]:
        row = sorted(row, key=lambda value: float(value["bbox"][0]))
        first = dict(row[0])
        text = ""
        bbox = list(row[0]["bbox"])
        for item in row:
            joiner = " " if text and re.search(r"[A-Za-z0-9)]$", text) and re.match(r"^[A-Za-z0-9(]", str(item["text"])) else ""
            text = _clean(f"{text}{joiner}{item['text']}")
            bbox = [min(bbox[0], item["bbox"][0]), min(bbox[1], item["bbox"][1]), max(bbox[2], item["bbox"][2]), max(bbox[3], item["bbox"][3])]
        first["text"] = text
        first["bbox"] = bbox
        first["font_size"] = max(float(item["font_size"]) for item in row)
        first["raw_index"] = min(int(item["raw_index"]) for item in row)
        return first

    line_items: list[dict[str, Any]] = []
    for region in region_order:
        region_items = sorted((item for item in items if item["region"] == region), key=lambda value: (float(value["bbox"][1]), float(value["bbox"][0])))
        rows: list[list[dict[str, Any]]] = []
        for item in region_items:
            if item.get("table_index") is not None:
                # Keep table cells/rows as independent classification units.
                # Their geometric order is not the same as paragraph order,
                # and joining them here would create unreadable cell soup.
                rows.append([item])
                continue
            if rows and abs(float(item["bbox"][1]) - float(rows[-1][0]["bbox"][1])) <= 4:
                previous_row_item = rows[-1][-1]
                previous_bbox = previous_row_item["bbox"]
                current_bbox = item["bbox"]
                previous_width = max(0.0, float(previous_bbox[2]) - float(previous_bbox[0]))
                current_width = max(0.0, float(current_bbox[2]) - float(current_bbox[0]))
                previous_height = max(0.0, float(previous_bbox[3]) - float(previous_bbox[1]))
                current_height = max(0.0, float(current_bbox[3]) - float(current_bbox[1]))
                vertical_label = (
                    (previous_width <= 24 and previous_height >= max(12, previous_width * 2.2))
                    or (current_width <= 24 and current_height >= max(12, current_width * 2.2))
                )
                horizontal_gap = max(0.0, float(current_bbox[0]) - float(previous_bbox[2]))
                adjacent = horizontal_gap <= max(18.0, min(float(previous_row_item["font_size"]), float(item["font_size"])) * 2.5)
                overlap = min(float(previous_bbox[2]), float(current_bbox[2])) - max(float(previous_bbox[0]), float(current_bbox[0]))
                if not vertical_label and (adjacent or overlap >= 0):
                    rows[-1].append(item)
                else:
                    rows.append([item])
            else:
                rows.append([item])
        line_items.extend(join_items(row) for row in rows)

    line_items.sort(key=lambda value: (region_order.get(value["region"], 9), float(value["bbox"][1]), float(value["bbox"][0])))
    grouped: list[dict[str, Any]] = []
    for item in line_items:
        if not grouped:
            grouped.append(item)
            continue
        previous = grouped[-1]
        px0, py0, px1, py1 = previous["bbox"]
        x0, y0, x1, y1 = item["bbox"]
        same_region = item["region"] == previous["region"]
        vertical_gap = y0 - py1
        same_column = abs(x0 - px0) <= 28 or min(px1, x1) - max(px0, x0) >= min(px1 - px0, x1 - x0) * 0.45
        starts_label = bool(re.match(r"^【(?:摘要|关键词|中图分类号|文献标识码|作者简介)", str(item["text"])))
        starts_heading = is_section_heading(str(item["text"]))
        can_merge = (
            # PyMuPDF line bboxes from Chinese PDFs often overlap vertically
            # by a few points even when they are consecutive visual lines.
            same_region and same_column and -10 <= vertical_gap <= 22
            and len(str(previous["text"])) + len(str(item["text"])) <= 560
            and previous.get("table_index") is None and item.get("table_index") is None
            and not (
                len(str(previous["text"]).strip()) <= 8
                and not re.search(r"[。！？；：]$", str(previous["text"]).strip())
                and not re.match(r"^(?:[一二三四五六七八九十百]+、|（[一二三四五六七八九十百]+）|[0-9０-９]+(?:\.[0-9０-９]+)*[、.)）])", str(previous["text"]).strip())
            )
            and not starts_label and not starts_heading and not is_heading_only(str(previous["text"]))
            and not is_section_heading(str(previous["text"]))
            and previous.get("region") != "figure" and item.get("region") != "figure"
            # A large font/style transition is normally a title, author line,
            # section heading, or caption boundary.  Do not glue it to the
            # neighbouring small-font line merely because the PDF stored both
            # in one text block.
            and not (
                abs(float(previous.get("font_size", 0)) - float(item.get("font_size", 0))) >= 6
                and max(float(previous.get("font_size", 0)), float(item.get("font_size", 0))) >= 18
            )
        )
        if not can_merge:
            grouped.append(item)
            continue
        joiner = " " if re.search(r"[A-Za-z0-9)]$", str(previous["text"])) and re.match(r"^[A-Za-z0-9(]", str(item["text"])) else ""
        previous["text"] = _clean(f"{previous['text']}{joiner}{item['text']}")
        previous["bbox"] = [min(px0, x0), min(py0, y0), max(px1, x1), max(py1, y1)]
        previous["font_size"] = max(previous["font_size"], item["font_size"])
    return grouped


def extract_pdf_layout(pdf_bytes: bytes) -> dict[str, Any] | None:
    """Extract a layout representation from PDF bytes using PyMuPDF."""

    try:
        import pymupdf

        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None

    try:
        pages: list[dict[str, Any]] = []
        for page_number, page in enumerate(document, 1):
            blocks, has_columns = _make_blocks(page_number, page)
            pages.append({
                "page": page_number,
                "width": round(float(page.rect.width), 2),
                "height": round(float(page.rect.height), 2),
                "has_columns": has_columns,
                "blocks": blocks,
                "text": "\n\n".join(block["text"] for block in blocks),
            })
    finally:
        document.close()

    if not any(page["blocks"] for page in pages):
        return None

    repeated = Counter(
        _canonical(block["text"])
        for page in pages
        for block in page["blocks"]
        if block["region"] in {"header", "footer"} and len(_canonical(block["text"])) >= 3
    )
    repeated_keys = {key for key, count in repeated.items() if count >= 2}
    for page in pages:
        page["blocks"] = [
            block for block in page["blocks"]
            if not (block["region"] in {"header", "footer"} and _canonical(block["text"]) in repeated_keys)
            and not re.fullmatch(r"[0-9０-９]+", re.sub(r"\s+", "", block["text"]).strip())
        ]
        page["text"] = "\n\n".join(block["text"] for block in page["blocks"]).strip()

    blocks = [block for page in pages for block in page["blocks"] if block["text"].strip()]
    return {
        "engine": "pymupdf-layout-v1",
        "pages": pages,
        "blocks": blocks,
        "text": "\n\n".join(block["text"] for block in blocks).strip(),
    }
