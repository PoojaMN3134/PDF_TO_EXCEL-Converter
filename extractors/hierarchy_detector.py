from collections import Counter
import re


IGNORED_EXACT = {
    "data sheet",
    "chat now",
    "learn more",
    "visit website",
}

IGNORED_CONTAINS = (
    "copyright",
    "all rights reserved",
    "product configuration and appearance may vary",
)


def normalize_text(text):
    return " ".join((text or "").replace("\xa0", " ").split())


def normalize_key(text):
    return re.sub(r"\W+", " ", normalize_text(text).lower()).strip()


def is_bold_or_medium(element):
    fonts = " ".join(element.get("fonts") or [element.get("font", "")]).lower()
    return any(token in fonts for token in ("bold", "medium", "semibold", "demi"))


def is_noise_text(element):
    text = normalize_text(element.get("text", ""))

    lower_text = text.lower()

    # --------------------------------------------------
    # PRESERVE LEGAL / COPYRIGHT CONTENT
    # --------------------------------------------------
    legal_keywords = [

        "copyright",

        "hewlett packard enterprise",

        "development lp",

        "all third-party marks",

        "microsoft is either",

        "rev.",

        "hpe.com"
    ]

    if any(
        keyword in lower_text
        for keyword in legal_keywords
    ):
        return False
    key = normalize_key(text)
    if not text:
        return True
    if key in IGNORED_EXACT:
        return True
    if any(token in key for token in IGNORED_CONTAINS):
        return True
    if re.fullmatch(r"\d+", text) and element.get("y0", 0) > element.get("height", 0) * 0.85:
        return True
    if (
        element.get("y0", 0) > element.get("height", 0) * 0.92
        and not any(
            keyword in lower_text
            for keyword in legal_keywords
        )
    ):
        return True
    return False


def get_text_elements(pages_data, include_noise=False):
    elements = []
    for page in pages_data:
        for element in page.get("elements", page.get("blocks", [])):
            if element.get("kind", "text") != "text":
                continue
            if include_noise or not is_noise_text(element):
                elements.append(element)
    return elements


def detect_body_size(text_elements):
    sizes = [
        round(element.get("size", 0), 1)
        for element in text_elements
        if 6 <= element.get("size", 0) <= 14 and len(element.get("text", "")) > 20
    ]
    if not sizes:
        return 10.0
    return Counter(sizes).most_common(1)[0][0]


def extract_title(pages_data):
    first_page = next((page for page in pages_data if page.get("page") == 1), None)
    if not first_page:
        return ""

    candidates = [
        element
        for element in first_page.get("elements", first_page.get("blocks", []))
        if element.get("kind", "text") == "text" and not is_noise_text(element)
    ]
    if not candidates:
        return ""

    max_size = max(element.get("size", 0) for element in candidates)
    title_lines = [
        element
        for element in candidates
        if element.get("size", 0) >= max_size * 0.85 and element.get("y0", 0) < first_page["height"] * 0.85
    ]
    title_lines.sort(key=lambda element: (element.get("y0", 0), element.get("x0", 0)))

    return normalize_text(" ".join(element["text"] for element in title_lines))


def is_page_header(element):
    text = normalize_text(element.get("text", ""))
    if is_noise_text(element):
        return False
    if len(text.split()) > 10:
        return False
    if element.get("y0", 9999) > 40:
        return False
    if element.get("x0", 9999) > element.get("width", 0) * 0.35:
        return False
    return True


def is_table_caption(text):
    return bool(re.match(r"^table\s+\d+[\.:]?", normalize_text(text), re.I))


def is_figure_caption(text):
    return bool(re.match(r"^(?:figure|fig\.)\s*\d+", normalize_text(text), re.I))


def extract_figure_number(text):
    match = re.match(
        r"^(?:figure|fig\.)\s*(\d+)",
        normalize_text(text),
        re.I,
    )

    return int(match.group(1)) if match else None


def heading_score(element, body_size):
    text = normalize_text(element.get("text", ""))
    words = text.split()
    score = 0

    if element.get("size", 0) >= body_size + 3:
        score += 4
    elif element.get("size", 0) >= body_size + 1:
        score += 2

    if is_bold_or_medium(element):
        score += 2
    if len(words) <= 8:
        score += 2
    if element.get("space_before", 0) >= body_size * 1.2:
        score += 1
    if element.get("x0", 9999) <= element.get("width", 0) * 0.12:
        score += 1
    if text.isupper() and len(words) <= 10:
        score += 1

    return score


def is_heading(element, body_size):
    text = normalize_text(element.get("text", ""))
    if is_noise_text(element) or is_table_caption(text) or is_figure_caption(text):
        return False
    if len(text) > 120 or text.endswith((".", ":", ";")):
        return False
    return heading_score(element, body_size) >= 5
