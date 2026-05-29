"""Email body and attachment extractor.

Parses RFC 2822 messages from raw string.  Extracts the plain-text body and,
if a PDF attachment is present, routes it through the PDF extractor.  The PDF
text wins on conflict (it is the authoritative document); the email body is
appended as supplementary context.
"""

from __future__ import annotations

import email as _email_stdlib
import email.policy
import logging

from agents.paper_trail.extractors.pdf import extract_text_from_pdf

logger = logging.getLogger(__name__)


def extract_text_from_email(raw_message: str) -> str:
    """Extract document text from a raw RFC 2822 email message.

    Strategy:
      1. Parse the MIME tree.
      2. Collect all text/plain parts as the email body.
      3. Scan for application/pdf attachments; extract text via pdfplumber.
      4. If a PDF attachment was found: return PDF text + email body as context.
      5. If no PDF: return plain-text body only.

    Args:
        raw_message: Full RFC 2822 message text (headers + body).

    Returns:
        Extracted text blob ready for regex + LLM processing.

    Raises:
        ValueError: If no plain-text content and no PDF attachment found.
    """
    msg = _email_stdlib.message_from_string(raw_message, policy=email.policy.default)

    plain_parts: list[str] = []
    pdf_texts: list[str] = []

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", ""))

        if content_type == "text/plain" and "attachment" not in disposition:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                plain_parts.append(payload.decode(charset, errors="replace"))

        elif content_type == "application/pdf":
            payload = part.get_payload(decode=True)
            if payload:
                filename = part.get_filename() or "attachment.pdf"
                logger.info("Email: extracting PDF attachment '%s'", filename)
                pdf_text = extract_text_from_pdf(payload)
                pdf_texts.append(pdf_text)

    if pdf_texts and plain_parts:
        body = "\n\n".join(plain_parts).strip()
        pdf_blob = "\n\n".join(pdf_texts)
        logger.info("Email: PDF attachment + plain body, combining (PDF wins on conflict)")
        return f"{pdf_blob}\n\n--- EMAIL BODY (supplementary) ---\n\n{body}"

    if pdf_texts:
        return "\n\n".join(pdf_texts)

    if plain_parts:
        return "\n\n".join(plain_parts).strip()

    raise ValueError("Email contains no plain-text body and no PDF attachment")
