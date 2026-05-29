"""PaperTrail — Stage 2 freight invoice document extractor.

Public interface:
    extract_case(source, filing_date=None, case_id_override=None) -> DisputeCase

Source types:
    PdfSource(kind="pdf", content=bytes, filename=None)
    EmailSource(kind="email", raw_message=str)
    EdiSource(kind="edi", content=str)  # raises NotImplementedError — v2 deferred
"""

from agents.paper_trail.paper_trail import (
    EdiSource,
    EmailSource,
    ExtractionSource,
    PdfSource,
    extract_case,
)

__all__ = ["extract_case", "PdfSource", "EmailSource", "EdiSource", "ExtractionSource"]
