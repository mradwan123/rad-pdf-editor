"""Page rasterisation for the GUI.

Split out of `gui/main_window.py` in Phase 6a (docs/GUI_PLAN.md §3.1)
so that the rendering path has one home before Phase 6b turns it into
an asynchronous, cached one. Nothing here knows about `MainWindow`,
tabs or documents - it takes a path, a size and a list widget.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from core.logging_config import get_logger

log = get_logger(__name__)

#: Translation context. Kept as "MainWindow" rather than "Rendering"
#: so the user-visible strings moved out of main_window.py keep the
#: context they were already collected under (SPEC.md 6.2 - i18n
#: readiness; no .ts files ship yet, but the contexts should not churn
#: just because a function moved file).
_TR_CONTEXT = "MainWindow"


def render_thumbnails(thumbnail_list: QListWidget, path: Path, size: QSize) -> None:
    """Fill `thumbnail_list` with one item per page of `path`.

    Each item's `Qt.ItemDataRole.UserRole` holds the 1-based page
    number it represents in the *current* working document, which is
    what drag-reordering reads back in visual order to build a
    `ReorderPagesOperation`'s `page_order`.
    """
    # No parent: this is a short-lived, throwaway document used only to
    # render thumbnails for this one refresh. A window-parented
    # QPdfDocument would live as long as the window - confirmed via
    # review to leak one instance per call (every operation/undo/redo
    # triggers a refresh), unbounded over a session.
    pdf_doc = QPdfDocument()
    if pdf_doc.load(str(path)) != QPdfDocument.Error.None_:
        log.error("Could not load PDF for thumbnail rendering: %s", path)
        return
    for i in range(pdf_doc.pageCount()):
        rendered = pdf_doc.render(i, size)
        # QtPdf leaves any unpainted area of the page fully transparent
        # (alpha=0) rather than opaque white - most visible on
        # blank/near-empty pages. Composite onto a white backdrop so a
        # thumbnail always reads as a page, not as "nothing" wherever
        # the source PDF painted nothing.
        page_image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        page_image.fill(Qt.GlobalColor.white)
        painter = QPainter(page_image)
        painter.drawImage(0, 0, rendered)
        painter.end()
        label = QCoreApplication.translate(_TR_CONTEXT, "Page {0}").format(i + 1)
        item = QListWidgetItem(QIcon(QPixmap.fromImage(page_image)), label)
        item.setData(Qt.ItemDataRole.UserRole, i + 1)
        thumbnail_list.addItem(item)
