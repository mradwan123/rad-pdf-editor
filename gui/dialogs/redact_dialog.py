"""Search-and-redact, with the review step decision 10 asks for.

Phase 6f. The point of the review is that a user is shown *where else*
the term lives - metadata, the XMP packet, bookmark titles, embedded
attachments - because those are the places a "redacted" PDF most often
still leaks, and the ones nobody thinks to check.

The dialog only narrows what `RedactOperation` will do; it adds no
capability the CLI lacks, which is why the operation takes explicit
flags rather than depending on an interactive step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from core.ops.redact import RedactionScan, scan_for_text
from gui.dialogs.base_tool_dialog import BaseToolDialog


class RedactDialog(BaseToolDialog):
    """Find a term, review everywhere it appears, then remove it."""

    def __init__(self, parent: QWidget | None = None, document_path: Path | None = None) -> None:
        super().__init__(self.tr("Redact"), parent)
        self._document_path = document_path
        self._scan: RedactionScan | None = None

        self.term = self.add_row(self.tr("Text to redact"), QLineEdit())
        self.term.setPlaceholderText(self.tr("e.g. a name or account number"))
        self.term.returnPressed.connect(self._run_scan)

        self.scan_button = QPushButton(self.tr("Find occurrences"))
        self.scan_button.clicked.connect(self._run_scan)
        self.add_full_width(self.scan_button)

        self.summary = QLabel(self.tr("Enter a term and choose Find occurrences."))
        self.summary.setWordWrap(True)
        self.add_full_width(self.summary)

        self.results = QTreeWidget()
        self.results.setHeaderHidden(True)
        self.results.setAccessibleName(self.tr("Redaction targets"))
        self.add_full_width(self.results)

        self.scrub_metadata = self.add_row(
            self.tr("Also scrub metadata"), QCheckBox()
        )
        self.scrub_metadata.setChecked(True)
        self.scrub_bookmarks = self.add_row(self.tr("Also scrub bookmarks"), QCheckBox())
        self.scrub_bookmarks.setChecked(True)
        self.scrub_attachments = self.add_row(self.tr("Also scrub attachments"), QCheckBox())
        self.scrub_attachments.setChecked(True)

        if document_path is None:
            # The Workflow builder constructs tool dialogs against no
            # document, so there is nothing to scan - the term and the
            # scrub flags are still enough to build a usable step.
            self.scan_button.setEnabled(False)
            self.summary.setText(
                self.tr("No document open - the term will be searched when this step runs.")
            )

    def _run_scan(self) -> None:
        term = self.term.text().strip()
        self.results.clear()
        if not term or self._document_path is None:
            return
        scan = scan_for_text(self._document_path, term)
        self._scan = scan

        if scan.is_empty:
            self.summary.setText(self.tr("No occurrences of '{0}' found.").format(term))
            return

        pages = QTreeWidgetItem([self.tr("Pages ({0} occurrences)").format(scan.total_page_hits)])
        for page in sorted(scan.page_hits):
            count = len(scan.page_hits[page])
            child = QTreeWidgetItem([self.tr("Page {0} - {1}").format(page, count)])
            child.setCheckState(0, Qt.CheckState.Checked)
            child.setData(0, Qt.ItemDataRole.UserRole, page)
            pages.addChild(child)
        self.results.addTopLevelItem(pages)
        pages.setExpanded(True)

        # The non-content hits are listed separately and prominently:
        # they are the ones a content-only redaction leaves behind.
        elsewhere: list[str] = []
        elsewhere += [self.tr("Metadata: {0}").format(k) for k in scan.metadata_keys]
        elsewhere += [self.tr("Bookmark: {0}").format(b) for b in scan.bookmarks]
        elsewhere += [self.tr("Attachment: {0}").format(a) for a in scan.attachments]
        if scan.xmp:
            elsewhere.append(self.tr("XMP metadata packet"))
        if elsewhere:
            other = QTreeWidgetItem([self.tr("Also found outside the page content")])
            for label in elsewhere:
                other.addChild(QTreeWidgetItem([label]))
            self.results.addTopLevelItem(other)
            other.setExpanded(True)

        self.summary.setText(
            self.tr("{0} on pages, {1} elsewhere.").format(scan.total_page_hits, len(elsewhere))
        )

    def selected_pages(self) -> list[int] | None:
        """Pages the user left checked, or None when nothing was scanned."""
        if self._scan is None:
            return None
        root = self.results.topLevelItem(0)
        if root is None:
            return None
        return [
            root.child(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(root.childCount())
            if root.child(i).checkState(0) == Qt.CheckState.Checked
        ]

    def values(self) -> dict[str, Any]:
        pages = self.selected_pages()
        rects: list[tuple[int, float, float, float, float]] = []
        term = self.term.text().strip()
        if self._scan is not None and pages is not None:
            # Deselecting a page means redacting explicit regions on the
            # ones that are left, rather than a document-wide sweep.
            for page in pages:
                rects += [(page, *rect) for rect in self._scan.page_hits.get(page, [])]
        full_sweep = self._scan is None or (
            pages is not None and len(pages) == len(self._scan.page_hits)
        )
        return {
            "rects": [] if full_sweep else rects,
            "search_text": term if full_sweep else "",
            "scrub_metadata": self.scrub_metadata.isChecked(),
            "scrub_bookmarks": self.scrub_bookmarks.isChecked(),
            "scrub_attachments": self.scrub_attachments.isChecked(),
        }
