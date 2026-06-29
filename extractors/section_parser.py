from extractors.hierarchy_detector import (
    detect_body_size,
    extract_figure_number,
    extract_title,
    get_text_elements,
    is_bold_or_medium,
    is_figure_caption,
    is_heading,
    is_noise_text,
    is_page_header,
    is_table_caption,
    normalize_text,
)


def _new_block(header):
    return {
        "header": normalize_text(header),
        "description": "",
        "extended_descriptions": [],
    }


def _normalize_multiline(text):
    lines = [normalize_text(line) for line in (text or "").splitlines()]

    cleaned = []
    previous_blank = False
    for line in lines:
        if not line:
            if cleaned and not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue

        cleaned.append(line)
        previous_blank = False

    while cleaned and not cleaned[-1]:
        cleaned.pop()

    return "\n".join(cleaned)


def _is_list_item(text):
    return normalize_text(text).startswith(("-", "−", "•", "*"))


def _line_separator(state, element, text):
    if state.get("current_chunk_is_heading"):
        return "\n\n"
    if state.get("current_chunk_ends_with_marker"):
        return "\n\n"

    previous = state.get("last_text_element")
    if not previous:
        return "\n"

    vertical_gap = element.get("y0", 0) - previous.get("y1", 0)
    same_page = element.get("page") == previous.get("page")
    indent_delta = abs(element.get("x0", 0) - previous.get("x0", 0))
    body_size = state.get("body_size", 10)
    previous_text = previous.get("text", "")

    if _is_list_item(text):
        return "\n"
    if _is_list_item(previous_text):
        return "\n"
    if not same_page:
        return " "
    if vertical_gap >= body_size * 0.65:
        return "\n\n"
    if indent_delta >= body_size * 1.5:
        return "\n"

    return " "


def _append_text(state, text, element=None):
    text = normalize_text(text)
    if not text:
        return

    if not state["current_chunk"]:
        state["current_chunk"] = text
    elif element:
        state["current_chunk"] += _line_separator(state, element, text) + text
    else:
        state["current_chunk"] += "\n\n" + text

    state["current_chunk_is_heading"] = False
    state["current_chunk_ends_with_marker"] = False
    if element:
        state["last_text_element"] = element


def _append_to_current_chunk(state, text, element):
    if state["current_chunk"] is None:
        state["current_chunk"] = normalize_text(text)
        state["last_text_element"] = element
        state["current_chunk_is_heading"] = False
        state["current_chunk_ends_with_marker"] = False
    else:
        _append_text(state, text, element)


def _flush_chunk(state):
    block = state["current_block"]
    chunk = _normalize_multiline(state["current_chunk"])
    if not block or not chunk:
        state["current_chunk"] = None
        state["last_text_element"] = None
        state["current_chunk_is_heading"] = False
        state["current_chunk_ends_with_marker"] = False
        return

    if not block["description"]:
        block["description"] = chunk
    else:
        block["extended_descriptions"].append(chunk)

    state["current_chunk"] = None
    state["last_text_element"] = None
    state["current_chunk_is_heading"] = False
    state["current_chunk_ends_with_marker"] = False


def _start_block(state, header):
    _flush_chunk(state)
    block = _new_block(header)
    state["blocks"].append(block)
    state["current_block"] = block
    state["current_chunk"] = None
    state["last_text_element"] = None
    state["current_chunk_is_heading"] = False
    state["current_chunk_ends_with_marker"] = False


def _start_subsection(state, heading):
    if not state["current_block"]:
        return
    _flush_chunk(state)
    state["current_chunk"] = normalize_text(heading)
    state["last_text_element"] = None
    state["current_chunk_is_heading"] = True
    state["current_chunk_ends_with_marker"] = False


def _append_standalone_item(state, placeholder):
    if not state["current_block"]:
        return

    if state["current_chunk"] and state["current_chunk_is_heading"]:
        state["current_chunk"] = _normalize_multiline(f"{state['current_chunk']}\n\n{placeholder}")
        state["current_chunk_is_heading"] = False
        state["current_chunk_ends_with_marker"] = True
        return

    if not state["current_chunk"] and not state["current_block"]["description"]:
        state["current_block"]["description"] = placeholder
        state["current_chunk_ends_with_marker"] = True
        return

    _flush_chunk(state)
    state["current_block"]["extended_descriptions"].append(placeholder)


def _row_density(element, text_elements):
    y0 = element.get("y0", 0)
    return sum(1 for item in text_elements if abs(item.get("y0", 0) - y0) <= 2.5)


def _looks_like_table_text(element, density, body_size, table_bbox=None):
    text = normalize_text(element.get("text", ""))
    if not text:
        return False

    word_count = len(text.split())
    left_edge = element.get("width", 0) * 0.14

    if table_bbox:
        table_bottom = table_bbox[3]
        clearly_heading = (
            is_heading(element, body_size)
            and element.get("y0", 0) >= table_bottom - 3
        )

        if clearly_heading:
            return False

        if element.get("y0", 0) <= table_bottom + 3:
            return True

        clearly_paragraph = (
            element.get("x0", 0) <= left_edge
            and element.get("size", 0) >= body_size - 1
            and word_count >= 6
        )

        if clearly_paragraph:
            return False

    if element.get("size", 0) >= body_size + 3 and element.get("x0", 0) <= left_edge:
        return False
    if density >= 2:
        return True
    if element.get("x0", 0) > left_edge:
        return True
    if is_bold_or_medium(element) and word_count <= 8:
        return True
    if word_count <= 4 and not text.endswith((".", ":", ";")):
        return True

    return False


def parse_sections(pages_data):
    title = extract_title(pages_data)
    normalized_title = normalize_text(title).lower()
    text_elements = get_text_elements(pages_data)
    body_size = detect_body_size(text_elements)

    state = {
        "blocks": [],
        "current_block": None,
        "current_chunk": None,
        "current_chunk_is_heading": False,
        "current_chunk_ends_with_marker": False,
        "last_text_element": None,
        "body_size": body_size,
        "table_no": 0,
        "seen_figure_numbers": set(),
    }
    intro_lines = []
    active_header = None
    seen_headers = set()
    tables = []

    for page in pages_data:
        continuation_bboxes = page.get("table_continuation_bboxes") or []
        table_text_filter_active = bool(continuation_bboxes)
        active_table_bbox = continuation_bboxes[0] if continuation_bboxes else None
        page_text_elements = [
            element for element in page.get("elements", []) if element.get("kind") == "text"
        ]
        page_header = None
        for element in page.get("elements", []):
            if element.get("kind") == "text" and is_page_header(element):
                page_header = normalize_text(element["text"])
                break

        if page_header and page_header != active_header:
            active_header = page_header
            if active_header not in seen_headers:
                _start_block(state, active_header)
                seen_headers.add(active_header)

        for element in page.get("elements", []):
            kind = element.get("kind")

            if kind == "table":
                if not state["current_block"]:
                    continue
                state["table_no"] += 1
                placeholder = f"[TABLE{state['table_no']}]"
                tables.append({
                    "name": placeholder.strip("[]"),
                    "rows": element.get("rows") or [],
                })
                _append_standalone_item(state, placeholder)
                table_text_filter_active = True
                active_table_bbox = element.get("bbox")
                continue

            if kind == "image":
                continue

            if kind != "text":
                continue

            text = normalize_text(element.get("text", ""))
            
            if is_noise_text(element):
                continue

            if is_page_header(element) and text == active_header:
                continue

            if is_table_caption(text):
                continue

            if table_text_filter_active:
                density = _row_density(element, page_text_elements)
                if _looks_like_table_text(
                    element,
                    density,
                    body_size,
                    active_table_bbox,
                ):
                    continue
                table_text_filter_active = False
                active_table_bbox = None

            if is_figure_caption(text):
                figure_number = extract_figure_number(text)

                if (
                    state["current_block"]
                    and figure_number is not None
                    and figure_number not in state["seen_figure_numbers"]
                ):
                    state["seen_figure_numbers"].add(figure_number)
                    _append_standalone_item(state, f"[IMAGE{figure_number}]")
                continue

            if (
                element.get("page") == 1
                and normalized_title
                and text.lower() in normalized_title
                and element.get("size", 0) >= body_size + 3
            ):
                continue

            if is_heading(element, body_size):

                current_header = ""

                if state["current_block"]:
                    current_header = (
                        state["current_block"]["header"]
                        .strip()
                        .lower()
                    )

                # ------------------------------------------------
                # ABOUT HPE SPECIAL CASE
                # ------------------------------------------------
                if current_header == "about hpe":

                    _append_to_current_chunk(
                        state,
                        text,
                        element
                    )

                    continue

                if state["current_block"]:
                    _start_subsection(state, text)

                elif element.get("page") == 1:
                    intro_lines.append(text)
                    continue

                else:
                    _start_block(state, text)

                continue

            if state["current_block"]:
                _append_to_current_chunk(state, text, element)
            else:
                intro_lines.append(text)

    _flush_chunk(state)

    meaningful_blocks = []

    for block in state["blocks"]:

        # ==================================================
        # SPECIAL HANDLING FOR ABOUT HPE
        # ==================================================
        if block["header"].strip().lower() == "about hpe":

            merged_description = block["description"]

            footer_parts = []

            for item in block["extended_descriptions"]:

                lower_item = item.lower()

            # Footer content
                if (
                    "copyright" in lower_item
                    or "hewlett packard" in lower_item
                    or "hewlett packard enterprise" in lower_item
                    or "hpe.com" in lower_item
                    or "rev." in lower_item
                    or "microsoft is either" in lower_item
                    or "chat now" in lower_item
                ):

                    footer_parts.append(item)

            # Ignore images on About HPE page
                elif item.strip().startswith("[IMAGE"):

                    continue

                # Merge everything else back into description
                else:

                    merged_description += "\n\n" + item

            block["description"] = merged_description

            block["extended_descriptions"] = []

            if footer_parts:

                block["extended_descriptions"].append(
                    "\n\n".join(footer_parts)
                )
        # ==================================================
        # KEEP ONLY MEANINGFUL BLOCKS
        # ==================================================
        has_content = bool(
            block["description"]
            or block["extended_descriptions"]
        )

        if has_content:

            meaningful_blocks.append(
                block
            )

    return {

        "headline": title,

        "description": _normalize_multiline(
            " ".join(
                intro_lines
            )
        ),

        "blocks": meaningful_blocks,

        "tables": tables,
    }
