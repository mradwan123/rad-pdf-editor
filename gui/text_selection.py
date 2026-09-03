"""Mapping a point on a page to a character index, so text can be
selected by dragging.

Phase 6c (docs/GUI_PLAN.md §3.2). This exists because
**`QPdfDocument.getSelection()` - the obvious API for exactly this - is
unusable in PySide6 6.11.1.** Verified rather than assumed: it returns
an invalid, empty `QPdfSelection` for every point range tried,
including ranges squarely over text that `getAllText()` reports on the
same page.

What *does* work is the index-based half of the API:

- `getAllText(page)` returns the page's text plus `bounds()`, which is
  **one polygon per line** (confirmed: a 3-line page gives 3 polygons,
  a dense page gives 45), in top-left-origin PDF points.
- `getSelectionAtIndex(page, start, length)` returns correct text and
  correct rects for any index range.

So a point is resolved by finding its line from `bounds()`, then binary
searching within that line's index range. Walking a page character by
character is not an option: `getSelectionAtIndex` costs ~906 us per
call, which is 2.6 s for a 2923-character page. A binary search is
~log2(line length) calls - roughly 6 ms - and results are cached, so a
drag over already-probed text costs nothing.

`getAllText().text()` separates lines with "\\r\\n", which is what lets
line *k* of the text be matched to polygon *k* of `bounds()`.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtPdf import QPdfDocument, QPdfSelection

#: Line separator used by QPdfSelection.text().
_LINE_SEPARATOR = "\r\n"


class PageTextIndex:
    """Point <-> character-index mapping for one page, built lazily."""

    def __init__(self, document: QPdfDocument, page: int) -> None:
        """`page` is 1-based, as everywhere else in this project."""
        self._document = document
        self._page = page
        selection = document.getAllText(page - 1)
        self.text = selection.text()
        self._line_rects = [polygon.boundingRect() for polygon in selection.bounds()]
        self._line_ranges = _line_ranges(self.text)
        # A page whose text and bounds disagree (no text at all, or a
        # producer QtPdf reads differently than expected) is treated as
        # unselectable rather than mis-mapped.
        self._usable = len(self._line_rects) == len(self._line_ranges)
        self._char_rects: dict[int, QRectF] = {}

    @property
    def is_empty(self) -> bool:
        return not self._usable or not self.text

    @property
    def length(self) -> int:
        return len(self.text)

    def index_at(self, point: QPointF) -> int:
        """Character index nearest `point` (in top-left-origin PDF
        points). Clamped into the page, so a drag off the edge selects
        to the start or end rather than nothing."""
        if self.is_empty:
            return 0
        line = self._line_at(point.y())
        start, end = self._line_ranges[line]
        return self._index_in_line(start, end, point.x())

    def text_between(self, start: int, end: int) -> str:
        if self.is_empty:
            return ""
        start, end = sorted((start, end))
        if end <= start:
            return ""
        return self._selection(start, end).text()

    def rects_between(self, start: int, end: int) -> list[QRectF]:
        """Rects covering the selection, one per line, in PDF points."""
        if self.is_empty:
            return []
        start, end = sorted((start, end))
        if end <= start:
            return []
        return [polygon.boundingRect() for polygon in self._selection(start, end).bounds()]

    # --- internals --------------------------------------------------------

    def _selection(self, start: int, end: int) -> QPdfSelection:
        return self._document.getSelectionAtIndex(self._page - 1, start, end - start)

    def _line_at(self, y: float) -> int:
        """The line containing `y`, or the vertically nearest one - a
        click in the gap between two lines still selects."""
        for i, rect in enumerate(self._line_rects):
            if rect.top() <= y <= rect.bottom():
                return i
        return min(
            range(len(self._line_rects)),
            key=lambda i: abs(self._line_rects[i].center().y() - y),
        )

    def _char_rect(self, index: int) -> QRectF:
        cached = self._char_rects.get(index)
        if cached is None:
            # QPdfSelection.boundingRectangle(), not boundingRect() -
            # QPolygonF has the latter, this class does not.
            cached = self._selection(index, index + 1).boundingRectangle()
            self._char_rects[index] = cached
        return cached

    def _index_in_line(self, start: int, end: int, x: float) -> int:
        """First index in [start, end) whose character sits at or past
        `x`, by binary search - the insertion point a caret would take."""
        low, high = start, end
        while low < high:
            mid = (low + high) // 2
            rect = self._char_rect(mid)
            # Compare against the character's midpoint so clicking the
            # right half of a glyph selects past it, as a text caret does.
            if rect.center().x() < x:
                low = mid + 1
            else:
                high = mid
        return low


def _line_ranges(text: str) -> list[tuple[int, int]]:
    """[start, end) index range of each line, matching the order of
    `QPdfSelection.bounds()`'s per-line polygons."""
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for line in text.split(_LINE_SEPARATOR):
        ranges.append((cursor, cursor + len(line)))
        cursor += len(line) + len(_LINE_SEPARATOR)
    return ranges
