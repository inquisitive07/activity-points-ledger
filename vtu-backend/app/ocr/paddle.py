"""PaddleOCR text recognition + table reconstruction (app/ocr/table.py).

PaddleOCR is imported and its models are loaded on first use only, so the API and the test suite start without it
(requirements-ocr.txt is optional). If it is not installed, extraction fails with a clear message and the batch
becomes FAILED; it can be retried once the engine is available.

PaddleOCR is not safe to call from several threads at once, and FastAPI runs background tasks in a thread pool, so
one lock serialises recognition.
"""
import logging
import tempfile
import threading
from pathlib import Path

from app.ocr.base import Extraction, ExtractionError, Extractor
from app.ocr.table import TextLine, rows_from_lines

log = logging.getLogger("app.ocr")
SUFFIX = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}


class PaddleExtractor(Extractor):
    name = "paddleocr"

    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()

    def _load(self):
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise ExtractionError("The OCR engine (PaddleOCR) is not installed on the server.") from exc
        # Same configuration as scripts/ocr_smoke.py: mobile models, no orientation/unwarping stages, and no
        # oneDNN (PaddlePaddle 3.3's oneDNN CPU backend fails on these models).
        return PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det",
                         text_recognition_model_name="en_PP-OCRv5_mobile_rec",
                         use_doc_orientation_classify=False, use_doc_unwarping=False,
                         use_textline_orientation=False, enable_mkldnn=False)

    def extract(self, data: bytes, mime_type: str) -> Extraction:
        suffix = SUFFIX.get(mime_type)
        if suffix is None:
            raise ExtractionError(f"OCR cannot read files of type {mime_type}.")
        with self._lock:
            if self._engine is None:
                self._engine = self._load()
            # PaddleOCR reads from a path. The temporary file is private (mode 0600) and removed afterwards.
            with tempfile.TemporaryDirectory(prefix="vtu-ocr-") as tmp:
                path = Path(tmp) / f"document{suffix}"
                path.write_bytes(data)
                try:
                    pages = list(self._engine.predict(str(path)))
                except Exception as exc:
                    log.exception("PaddleOCR failed")
                    raise ExtractionError("The OCR engine could not read this document (it may be damaged or "
                                          "not a scanned page).") from exc
        lines = []
        for number, page in enumerate(pages, 1):
            for text, score, box in zip(page["rec_texts"], page["rec_scores"], page["rec_boxes"]):
                lines.append(TextLine(number, str(text), float(score), tuple(float(v) for v in box)))
        extraction = rows_from_lines(lines, len(pages))
        extraction.metadata["engine"] = self.name
        return extraction
