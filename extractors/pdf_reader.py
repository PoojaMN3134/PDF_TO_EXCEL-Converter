from pathlib import Path
import re

import fitz


def _clean_text(text):
    return " ".join(text.replace("\xa0", " ").split())


def _inside_any_bbox(bbox, bboxes, tolerance=2):
    if not bbox:
        return False

    x0, y0, x1, y1 = bbox
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2

    for tx0, ty0, tx1, ty1 in bboxes:
        if tx0 - tolerance <= cx <= tx1 + tolerance and ty0 - tolerance <= cy <= ty1 + tolerance:
            return True

    return False

def _table_to_dict(table):
    try:
        rows = table.extract()
    except Exception:
        rows = []

    return {
        "bbox": tuple(table.bbox),
        "rows": rows or [],
    }


def _combine_table_segments(segments):
    ordered = sorted(segments, key=lambda table: (table["bbox"][1], table["bbox"][0]))
    combined = {
        "bbox": ordered[0]["bbox"],
        "rows": [],
    }

    for segment in ordered:
        x0, y0, x1, y1 = combined["bbox"]
        sx0, sy0, sx1, sy1 = segment["bbox"]
        combined["bbox"] = (
            min(x0, sx0),
            min(y0, sy0),
            max(x1, sx1),
            max(y1, sy1),
        )
        _merge_table_rows(combined, segment)

    return combined


def _find_tables(page, **kwargs):
    try:
        finder = page.find_tables(**kwargs)
    except Exception:
        return []

    return [
        _table_to_dict(table)
        for table in (getattr(finder, "tables", []) or [])
    ]


def _compact_row(row):
    return tuple(
        _clean_text(str(value or "")).lower()
        for value in (row or [])
        if _clean_text(str(value or ""))
    )


def _first_meaningful_row(tables):
    for table in tables:
        for row in table.get("rows") or []:
            if _compact_row(row):
                return {
                    "bbox": table["bbox"],
                    "rows": [row],
                }

    return None


def _extract_tables(page):
    page_dict = page.get_text("dict")
    captions = _extract_table_captions(page_dict)
    default_tables = _find_tables(page)

    if not captions:
        return default_tables

    tables = []
    used_default_tables = set()

    for caption_index, caption in enumerate(captions):
        region_top = caption["bbox"][3] + 1
        region_bottom = (
            captions[caption_index + 1]["bbox"][1] - 1
            if caption_index + 1 < len(captions)
            else page.rect.height * 0.94
        )
        clip = fitz.Rect(
            max(0, caption["bbox"][0] - 5),
            region_top,
            page.rect.width - 20,
            region_bottom,
        )

        region_segments = []
        for table_index, table in enumerate(default_tables):
            table_center_y = (table["bbox"][1] + table["bbox"][3]) / 2
            if region_top <= table_center_y <= region_bottom:
                region_segments.append(table)
                used_default_tables.add(table_index)

        enhanced_tables = _find_tables(
            page,
            clip=clip,
            vertical_strategy="text",
            horizontal_strategy="lines",
        )
        region_segments.extend(enhanced_tables)

        if region_segments:
            existing_rows = {
                _compact_row(row)
                for segment in region_segments
                for row in (segment.get("rows") or [])
                if _compact_row(row)
            }
            text_first_row = _first_meaningful_row(
                _find_tables(
                    page,
                    clip=clip,
                    vertical_strategy="text",
                    horizontal_strategy="text",
                )
            )

            if (
                text_first_row
                and _compact_row(text_first_row["rows"][0]) not in existing_rows
            ):
                region_segments.append(text_first_row)

            combined = _combine_table_segments(region_segments)
            x0, _, x1, y1 = combined["bbox"]
            combined["bbox"] = (x0, region_top, x1, y1)
            tables.append(combined)

    tables.extend(
        table
        for table_index, table in enumerate(default_tables)
        if table_index not in used_default_tables
    )

    return sorted(tables, key=lambda table: (table["bbox"][1], table["bbox"][0]))

def _extract_image_blocks(page):
    images = []
    page_dict = page.get_text("dict")

    for block in page_dict.get("blocks", []):
        if block.get("type") == 1 and "bbox" in block:
            images.append({"bbox": tuple(block["bbox"])})

    return images


def _extract_table_captions(page_dict):
    captions = []

    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
            text = _clean_text(" ".join(span["text"] for span in spans))

            if re.match(r"^table\s*\d+\s*[\.:]", text, re.I):
                captions.append({
                    "text": text,
                    "bbox": tuple(line["bbox"]),
                })

    return captions


def _has_caption_before_table(captions, table_bbox):
    table_top = table_bbox[1]

    return any(
        caption["bbox"][1] <= table_top
        and table_top - caption["bbox"][3] <= 60
        for caption in captions
    )


def _table_column_count(table):
    rows = table.get("rows") or []
    return max((len(row or []) for row in rows), default=0)


def _similar_table_structure(previous_table, current_table, page_width):
    previous_bbox = previous_table["bbox"]
    current_bbox = current_table["bbox"]

    previous_width = previous_bbox[2] - previous_bbox[0]
    current_width = current_bbox[2] - current_bbox[0]
    x_tolerance = max(18, page_width * 0.04)
    width_tolerance = max(30, previous_width * 0.15)

    previous_columns = _table_column_count(previous_table)
    current_columns = _table_column_count(current_table)

    if previous_columns and current_columns and previous_columns != current_columns:
        return False

    return (
        abs(previous_bbox[0] - current_bbox[0]) <= x_tolerance
        and abs(previous_width - current_width) <= width_tolerance
    )


def _normalized_row(row):
    return tuple(_clean_text(str(value or "")).lower() for value in (row or []))


def _merge_table_rows(target_table, continuation_table):
    target_rows = target_table.setdefault("rows", [])
    continuation_rows = list(continuation_table.get("rows") or [])

    if (
        target_rows
        and continuation_rows
        and _normalized_row(target_rows[0]) == _normalized_row(continuation_rows[0])
    ):
        continuation_rows = continuation_rows[1:]

    target_rows.extend(continuation_rows)


def extract_pdf_content(pdf_path):
    pdf_path = Path(pdf_path)
    doc = fitz.open(pdf_path)

    pages_data = []
    table_no = 0
    image_no = 0
    last_table_chain = None

    for page_num, page in enumerate(doc, start=1):
        page_dict = page.get_text("dict")
        tables = sorted(_extract_tables(page), key=lambda table: (table["bbox"][1], table["bbox"][0]))
        table_captions = _extract_table_captions(page_dict)
        table_bboxes = [table["bbox"] for table in tables]
        table_continuation_bboxes = []
        elements = []

        for table_index, table in enumerate(tables):
            is_continuation = (
                table_index == 0
                and last_table_chain is not None
                and last_table_chain["page"] == page_num - 1
                and last_table_chain["captioned"]
                and not table_captions
                and table["bbox"][1] <= page.rect.height * 0.2
                and _similar_table_structure(
                    last_table_chain["segment"],
                    table,
                    page.rect.width,
                )
            )

            if is_continuation:
                _merge_table_rows(last_table_chain["element"], table)
                table_continuation_bboxes.append(table["bbox"])
                last_table_chain["page"] = page_num
                last_table_chain["segment"] = table
                continue

            table_no += 1
            table_element = {
                "kind": "table",
                "page": page_num,
                "number": table_no,
                "placeholder": f"[TABLE{table_no}]",
                "bbox": table["bbox"],
                "rows": table["rows"],
            }
            elements.append(table_element)

            last_table_chain = {
                "element": table_element,
                "segment": table,
                "page": page_num,
                "captioned": _has_caption_before_table(table_captions, table["bbox"]),
            }

        if not tables:
            last_table_chain = None

        for image in _extract_image_blocks(page):
            if _inside_any_bbox(image["bbox"], table_bboxes):
                continue

            image_no += 1
            elements.append({
                "kind": "image",
                "page": page_num,
                "number": image_no,
                "placeholder": f"[IMAGE{image_no}]",
                "bbox": image["bbox"],
            })

        for block in page_dict.get("blocks", []):
            if "lines" not in block:
                continue

            for line in block["lines"]:
                spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
                if not spans:
                    continue

                bbox = tuple(line["bbox"])
                if _inside_any_bbox(bbox, table_bboxes, tolerance=0):
                    continue

                text = _clean_text(" ".join(span["text"] for span in spans))
                if not text:
                    continue

                sizes = [span["size"] for span in spans]
                fonts = [span.get("font", "") for span in spans]
                flags = [span.get("flags", 0) for span in spans]

                elements.append({
                    "kind": "text",
                    "page": page_num,
                    "text": text,
                    "size": round(max(sizes), 1),
                    "avg_size": round(sum(sizes) / len(sizes), 1),
                    "font": fonts[0],
                    "fonts": fonts,
                    "flags": flags,
                    "bbox": bbox,
                    "x0": bbox[0],
                    "y0": bbox[1],
                    "x1": bbox[2],
                    "y1": bbox[3],
                    "width": page.rect.width,
                    "height": page.rect.height,
                })

        elements.sort(key=lambda item: (item["bbox"][1], item["bbox"][0], item["kind"]))

        previous_text = None
        for element in elements:
            if element["kind"] != "text":
                continue

            element["space_before"] = (
                element["y0"] - previous_text["y1"] if previous_text is not None else element["y0"]
            )
            previous_text = element

        pages_data.append({
            "page": page_num,
            "width": page.rect.width,
            "height": page.rect.height,
            "elements": elements,
            "blocks": [element for element in elements if element["kind"] == "text"],
            "table_continuation_bboxes": table_continuation_bboxes,
        })

    return pages_data
    
