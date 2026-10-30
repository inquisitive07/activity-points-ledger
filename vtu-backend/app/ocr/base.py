"""The extractor interface. An Extractor turns a document's bytes into raw participant rows; it knows nothing about
students, events or the database. Matching and validation (app/ocr/validation.py) and batch processing
(app/ocr/pipeline.py) work only with the types below, so the OCR engine can be replaced without touching them.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ExtractionError(Exception):
    """The document could not be turned into participant rows. The message is shown to the Dean (OCR_BATCH
    .error_message), so it must explain the problem without internal details."""


@dataclass
class ExtractedRow:
    """One participant row exactly as read. Values are raw text: nothing here has been corrected or parsed."""
    page_number: int
    row_number: int | None
    usn: str | None
    name: str | None
    points: str | None
    confidence: float | None                  # lowest confidence of the text that makes up the row, 0..1
    raw: dict = field(default_factory=dict)   # engine output for this row (text, cells, boxes)
    structure_issue: str | None = None        # set when the row did not have the expected USN / NAME / POINTS shape


@dataclass
class Extraction:
    pages_processed: int
    rows: list[ExtractedRow]
    title_text: str = ""                      # text above the table, where the event and club names are expected
    metadata: dict = field(default_factory=dict)   # engine/document-level details kept in OCR_BATCH.extracted_metadata


class Extractor(ABC):
    name = "extractor"

    @abstractmethod
    def extract(self, data: bytes, mime_type: str) -> Extraction:
        """Read `data` (a PDF, JPEG or PNG). Raises ExtractionError when no participant table can be read."""
