"""Find-in-document bar.

Phase 6c (docs/GUI_PLAN.md §3.2). `QPdfSearchModel` works off a
`QPdfDocument` with no `QPdfView` (§2.1), and returns each hit as a
`QPdfLink` carrying its page and its rectangles in **top-left-origin
PDF points** - the same convention `getSelectionAtIndex` uses, verified
against a page with text at known positions.

The one real gotcha, found by measuring rather than reading: the model
searches **asynchronously**, on a timer. A single `processEvents()`
after `setSearchString` reports zero hits on a document that plainly
contains the term; results accumulate over ~0.1 s and several event
loop turns. So this bar reports counts as they arrive rather than
assuming the search is finished when it returns, and
`wait_until_settled` exists for tests.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QRectF, Qt, Signal
from PySide6.QtPdf import QPdfDocument, QPdfSearchModel
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

#: How long the search must produce no new hits before it is considered
#: finished, and the poll interval used to measure that.
_SETTLE_QUIET = 0.30
_SETTLE_TICK = 0.01


class FindBar(QWidget):
    """Search box + hit navigation for one document."""

    #: (1-based page, rect in PDF points) of the hit to scroll to.
    result_activated = Signal(int, QRectF)
    #: page -> rects, for the canvas to highlight. Emitted as results
    #: arrive. Declared as `object`, not `dict`: PySide6 cannot marshal
    #: a Python dict through a typed signal and fails at emit time with
    #: "_pythonToCppCopy: Cannot copy-convert (dict) to C++" on stderr -
    #: the connection silently delivers nothing, so the highlights never
    #: reached the canvas. `object` passes the value through untouched.
    results_changed = Signal(object)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = QPdfSearchModel(self)
        self._model.countChanged.connect(self._on_count_changed)
        self._current = -1

        self.input = QLineEdit()
        self.input.setPlaceholderText(self.tr("Find in document"))
        self.input.setClearButtonEnabled(True)
        self.input.setAccessibleName(self.tr("Find in document"))
        self.input.textChanged.connect(self._on_text_changed)
        self.input.returnPressed.connect(self.find_next)

        self.previous_button = QPushButton(self.tr("Previous"))
        self.previous_button.clicked.connect(self.find_previous)
        self.next_button = QPushButton(self.tr("Next"))
        self.next_button.clicked.connect(self.find_next)
        self.close_button = QPushButton(self.tr("Close"))
        self.close_button.clicked.connect(self._on_close)

        self.status = QLabel("")
        self.status.setObjectName("findStatus")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.previous_button)
        layout.addWidget(self.next_button)
        layout.addWidget(self.close_button)

    # --- public API -------------------------------------------------------

    def set_document(self, document: QPdfDocument | None) -> None:
        self._model.setDocument(document if document is not None else QPdfDocument(self))
        self._current = -1
        self._update_status()

    def activate(self) -> None:
        """Show the bar and focus it, keeping any existing search."""
        self.setVisible(True)
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.input.selectAll()

    @property
    def count(self) -> int:
        return self._model.count()

    def results_by_page(self) -> dict[int, list[QRectF]]:
        """Every hit so far, as 1-based page -> rects in PDF points."""
        results: dict[int, list[QRectF]] = {}
        for i in range(self._model.count()):
            link = self._model.resultAtIndex(i)
            results.setdefault(link.page() + 1, []).extend(link.rectangles())
        return results

    def find_next(self) -> None:
        self._step(1)

    def find_previous(self) -> None:
        self._step(-1)

    def wait_until_settled(self, timeout_ms: int = 3_000) -> int:
        """Spin until the asynchronous search stops producing new hits.

        Settling is measured in *elapsed time*, not event-loop turns: a
        few `processEvents()` calls return in microseconds, while the
        search genuinely needs on the order of 0.1 s to produce its
        first hit. Counting turns reported zero results on a document
        that plainly contained the term. For tests - the bar itself
        never blocks.
        """
        deadline = QDeadlineTimer(timeout_ms)
        last = self._model.count()
        unchanged_for = 0.0
        while not deadline.hasExpired():
            QCoreApplication.processEvents()
            time.sleep(_SETTLE_TICK)
            current = self._model.count()
            if current != last:
                last = current
                unchanged_for = 0.0
            else:
                unchanged_for += _SETTLE_TICK
                if unchanged_for >= _SETTLE_QUIET:
                    break
        return last

    # --- internals --------------------------------------------------------

    def _on_text_changed(self, text: str) -> None:
        self._current = -1
        self._model.setSearchString(text)
        self._update_status()

    def _on_count_changed(self) -> None:
        self._update_status()
        self.results_changed.emit(self.results_by_page())

    def _on_close(self) -> None:
        self.setVisible(False)
        self.results_changed.emit({})
        self.closed.emit()

    def _step(self, delta: int) -> None:
        total = self._model.count()
        if total == 0:
            return
        self._current = (self._current + delta) % total
        link = self._model.resultAtIndex(self._current)
        rects = link.rectangles()
        if rects:
            self.result_activated.emit(link.page() + 1, rects[0])
        self._update_status()

    def _update_status(self) -> None:
        total = self._model.count()
        if not self.input.text():
            self.status.setText("")
        elif total == 0:
            self.status.setText(self.tr("No results"))
        elif self._current < 0:
            self.status.setText(self.tr("{0} results").format(total))
        else:
            self.status.setText(self.tr("{0} of {1}").format(self._current + 1, total))
        self.previous_button.setEnabled(total > 0)
        self.next_button.setEnabled(total > 0)
