"""OCR: extraction (base, table, paddle), matching and validation (validation), batch processing (pipeline).

Routes get the extractor and the background session factory through the dependencies below, so tests substitute a
deterministic fake extractor and their own transaction without loading any OCR model.
"""
from app.db import SessionLocal
from app.ocr.base import ExtractedRow, Extraction, ExtractionError, Extractor
from app.ocr.paddle import PaddleExtractor

__all__ = ["ExtractedRow", "Extraction", "ExtractionError", "Extractor", "get_extractor", "get_ocr_session_factory"]

_paddle = PaddleExtractor()          # loads its models on first use, not here


def get_extractor() -> Extractor:
    return _paddle


def get_ocr_session_factory():
    """What background processing opens its session with (a context manager factory)."""
    return SessionLocal
