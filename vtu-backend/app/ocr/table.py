"""Rebuild participant rows from positioned text lines (the output of a text-recognition engine).

Expected layout of a signed point list: optional title lines (event, club), a header row naming the USN, NAME and
POINTS columns (an optional leading serial-number column is allowed), then one row per participant. Later pages may
repeat the header or continue without it.

The engine may return one text line per cell or one line per printed row, so each visual row is joined back into one
string and split into tokens: the first token is the USN, the last is the points value, everything between is the
name. Nothing is corrected here; a row that does not fit is passed on with a structure_issue for the Dean to see.
"""
import re
from dataclasses import dataclass
from statistics import median

from app.ocr.base import ExtractedRow, Extraction, ExtractionError

_SERIAL = re.compile(r"\d{1,4}\.?")


@dataclass
class TextLine:
    page_number: int
    text: str
    confidence: float
    box: tuple[float, float, float, float]          # x0, y0, x1, y1 in page pixels

    @property
    def y_center(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def height(self) -> float:
        return max(self.box[3] - self.box[1], 1.0)


def _visual_rows(lines: list[TextLine]) -> list[list[TextLine]]:
    """Group one page's lines into rows: lines whose vertical centres are within half a line height."""
    if not lines:
        return []
    tolerance = median(line.height for line in lines) / 2
    rows: list[list[TextLine]] = []
    for line in sorted(lines, key=lambda l: l.y_center):
        if rows and abs(line.y_center - median(l.y_center for l in rows[-1])) <= tolerance:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [sorted(row, key=lambda l: l.box[0]) for row in rows]


def _is_header(text: str) -> bool:
    words = text.upper()
    return "USN" in words and "NAME" in words and "POINT" in words


def parse_row(tokens: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    """(usn, name, points, structure_issue) from one row's tokens. A token counts as a USN or points value only if it
    contains a digit; otherwise that value is reported missing rather than guessed."""
    if len(tokens) > 3 and _SERIAL.fullmatch(tokens[0]):
        tokens = tokens[1:]                          # leading serial number column
    usn = tokens[0] if tokens and any(c.isdigit() for c in tokens[0]) else None
    rest = tokens[1:] if usn else tokens
    points = rest[-1] if rest and any(c.isdigit() for c in rest[-1]) else None
    name_tokens = rest[:-1] if points else rest
    name = " ".join(name_tokens) or None
    issue = None
    if len(tokens) < 3:
        issue = f"Row has {len(tokens)} value(s); expected USN, NAME and POINTS"
    return usn, name, points, issue


def rows_from_lines(lines: list[TextLine], pages: int) -> Extraction:
    """Turn every page's text lines into an Extraction. Raises ExtractionError if no header row is found at all."""
    header_seen, title, header_text, unparsed = False, [], None, []
    rows: list[ExtractedRow] = []
    for page in range(1, pages + 1):
        page_rows = _visual_rows([l for l in lines if l.page_number == page])
        in_table, row_number = header_seen, 0          # a later page without a header continues the table
        for visual in page_rows:
            text = " ".join(l.text.strip() for l in visual if l.text.strip())
            if not text:
                continue
            if _is_header(text):
                header_seen = in_table = True
                header_text = header_text or text
                continue
            if not in_table:
                title.append(text)
                continue
            usn, name, points, issue = parse_row(text.split())
            if usn is None and points is None:
                unparsed.append({"page_number": page, "text": text})   # signature lines, footers, notes
                continue
            row_number += 1
            rows.append(ExtractedRow(
                page_number=page, row_number=row_number, usn=usn, name=name, points=points,
                confidence=round(min(l.confidence for l in visual), 4), structure_issue=issue,
                raw={"text": text, "cells": [{"text": l.text, "confidence": round(l.confidence, 4),
                                              "box": [round(v, 1) for v in l.box]} for l in visual]}))
    if not header_seen:
        raise ExtractionError("No participant table was found: expected a header row naming the USN, NAME and "
                              "POINTS columns. Check that this is a signed point list.")
    if not rows:
        raise ExtractionError("The participant table has a header row but no participant rows could be read.")
    return Extraction(pages_processed=pages, rows=rows, title_text=" ".join(title),
                      metadata={"title_lines": title, "header": header_text, "unparsed_lines": unparsed})
