from dataclasses import dataclass

import fitz


@dataclass
class Page:
    page_number: int  # 1-based, matches the page a human sees opening this PDF file
    text: str


def load_pages(pdf_path: str) -> list[Page]:
    doc = fitz.open(pdf_path)
    try:
        return [Page(page_number=i + 1, text=doc[i].get_text("text")) for i in range(len(doc))]
    finally:
        doc.close()
