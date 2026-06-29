from extractors.section_parser import parse_sections


def detect_structure(pages_data):
    return parse_sections(pages_data)
