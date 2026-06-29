import logging
from pathlib import Path

from excel.excel_writer import save_excel
from extractors.block_detector import detect_structure
from extractors.pdf_reader import extract_pdf_content


INPUT_FOLDER = Path("input_pdfs")
OUTPUT_FOLDER = Path("output_excel")


def process_pdf(pdf_file, output_folder=OUTPUT_FOLDER):
    logging.info("Processing: %s", pdf_file.name)

    pages_data = extract_pdf_content(pdf_file)
    structured_data = detect_structure(pages_data)

    output_file = output_folder / f"{pdf_file.stem}.xlsx"
    save_excel(structured_data, output_file)

    return output_file


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    OUTPUT_FOLDER.mkdir(exist_ok=True)

    pdf_files = sorted(INPUT_FOLDER.glob("*.pdf"))
    if not pdf_files:
        logging.warning("No PDF files found in %s", INPUT_FOLDER)
        return

    failures = []
    for pdf_file in pdf_files:
        try:
            process_pdf(pdf_file)
        except Exception as exc:
            failures.append((pdf_file.name, exc))
            logging.exception("Failed to process %s", pdf_file.name)

    if failures:
        failed_names = ", ".join(name for name, _ in failures)
        raise RuntimeError(f"Failed to process {len(failures)} PDF(s): {failed_names}")

    logging.info("Done")


if __name__ == "__main__":
    main()
