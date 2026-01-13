"""PDF -> страницы: (image_bytes, pil_image, text)

Использует PyMuPDF (fitz) + Pillow. Возвращает список страниц.
"""

from pathlib import Path
from typing import List, Tuple
import logging

logger = logging.getLogger(__name__)


def pdf_to_pages(pdf_path: Path, dpi: int = 200) -> List[Tuple[bytes, "Image.Image", str]]:
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        raise RuntimeError("Для обработки PDF установите PyMuPDF: pip install pymupdf") from e

    try:
        from PIL import Image
        from io import BytesIO
    except Exception as e:
        raise RuntimeError("Для обработки PDF установите Pillow: pip install pillow") from e

    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img_bytes = pix.tobytes("jpeg")
        img_pil = Image.open(BytesIO(img_bytes))
        text = page.get_text("text") or ""
        pages.append((img_bytes, img_pil, text))
    doc.close()
    logger.info("PDF %s -> %d страниц", pdf_path.name, len(pages))
    return pages


# Backward-compatible alias
def pdf_to_images(pdf_path: Path):
    return [(b, pil) for (b, pil, _t) in pdf_to_pages(pdf_path)]
