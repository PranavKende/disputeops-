"""PDF text extractor using pdfplumber.

Accepts raw PDF bytes and returns a single text blob with page separators.
All pdfplumber I/O is isolated here — nothing else in PaperTrail imports
pdfplumber directly.
"""

from __future__ import annotations

import io
import logging

import pdfplumber

logger = logging.getLogger(__name__)


def extract_text_from_pdf(content: bytes) -> str:
    """Extract text from PDF bytes, joining all pages with separators.

    Args:
        content: Raw PDF file bytes.

    Returns:
        Full document text as a single string.  Pages are separated by
        '\\n\\n--- PAGE N ---\\n\\n' so regex and LLM have positional context
        without page-aware extraction logic.

    Raises:
        ValueError: If content is empty or pdfplumber cannot open it.
    """
    if not content:
        raise ValueError("PDF content is empty")

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        logger.info("PDF opened: %d page(s)", len(pdf.pages))
        for i, page in enumerate(pdf.pages, 1):
            page_text = page.extract_text() or ""
            page_text = page_text.strip()
            if page_text:
                pages.append(f"--- PAGE {i} ---\n\n{page_text}")
            else:
                logger.debug("Page %d produced no text (image-only or blank)", i)

    if not pages:
        raise ValueError("PDF contains no extractable text — may be image-only or encrypted")

    full_text = "\n\n".join(pages)
    logger.info("PDF extracted: %d page(s) with text, %d chars total", len(pages), len(full_text))
    return full_text
