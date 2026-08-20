"""File > Properties... - a read-only report on the document in the
active tab: metadata, the file on disk, page geometry, and PDF
structure/security flags.

Deliberately **not** a `BaseToolDialog`, following the precedent
`tab_placement_dialog.py` set (and for the same kind of reason): that
shell is an options form plus OK/Cancel, whose `values()` feed a
`ToolPlugin.build_operation()`. This dialog configures no `Operation`
and has no values to return - it reports, and its buttons are Copy /
Edit Metadata / Close. Being a plain Python `QDialog` subclass also
keeps `patch.object(PropertiesDialog, "exec", ...)` working in tests,
which a `QMessageBox`-based report would not (CLAUDE.md documents that
trap for `QMenu.exec` and `QMessageBox`'s compiled instance `.exec`).

It is not a tool, either: it mutates nothing, produces no undo entry
and is not registered in `TOOL_DIALOGS`. CLAUDE.md's "everything is an
`Operation`" rule is scoped to tools, the same reasoning the View
menu's plain `QAction`s already rely on. The one thing here that *does*
change the document - Edit Metadata... - deliberately hands off to
`MainWindow`'s ordinary tool-running path so the edit lands in the
undo stack and the audit log exactly as `Tools > Metadata` does.

All the reading lives in `core/document_info.py` (Qt-free, unit-tested
without a display server); this file only turns a `DocumentInfo` into
labelled rows, and those rows into either widgets or clipboard text -
one row list feeding both, so the copied report can't drift from the
displayed one.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.document_info import (
    DocumentInfo,
    PageGeometryInfo,
    PageSizeGroup,
    human_readable_size,
)
from core.logging_config import get_logger

log = get_logger(__name__)

#: (section title, [(label, value)]) - the single structure both the
#: widgets and the clipboard text are built from.
Section = tuple[str, list[tuple[str, str]]]

#: Minimum label column width in the plain-text report; the real
#: width grows to fit the longest label so no value is jammed up
#: against its own colon.
_TEXT_LABEL_WIDTH = 20

#: A report row is wider than a two-field options form; without a floor
#: the word-wrapped file path collapses the dialog into a tall, narrow
#: column (confirmed by grabbing it before this was added). Deliberately
#: a *minimum* only - Qt still sizes the dialog from its content, and
#: this is the one place in gui/dialogs that needs a size hint at all,
#: because it's the one dialog whose content is prose rather than a
#: fixed set of input widgets.
_MINIMUM_WIDTH = 520


class _ReportScrollArea(QScrollArea):
    """A QScrollArea whose `sizeHint` follows the widget inside it.

    The stock one reports a small fixed hint regardless of content, so
    a dialog laid out around it opens as a 520x125 sliver with every
    section scrolled out of sight (measured, not guessed - that is
    literally what the first grab() of this dialog showed). Qt still
    caps the result at two thirds of the screen and the scroll area
    takes over from there, which is the whole reason it's here.
    """

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override, fixed name
        content = self.widget()
        if content is None:  # pragma: no cover - always set in __init__
            return super().sizeHint()
        hint = content.sizeHint()
        margin = 2 * self.frameWidth()
        scrollbar = self.verticalScrollBar().sizeHint().width()
        return QSize(hint.width() + margin + scrollbar, hint.height() + margin)


class PropertiesDialog(QDialog):
    """`info` is the report to show. `on_edit_metadata`, when given, is
    called by the Edit Metadata... button and must return a fresh
    `DocumentInfo` if the document was actually changed (or None if the
    user cancelled) - the dialog then refreshes itself in place rather
    than closing and reopening, so it never sits there showing values
    the user just edited away. Omitting the callback (as the unit tests
    do) simply disables that button.
    """

    def __init__(
        self,
        info: DocumentInfo,
        on_edit_metadata: Callable[[], DocumentInfo | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Document Properties"))
        self.setModal(True)
        self._on_edit_metadata = on_edit_metadata
        self._info = info
        self._sections: list[Section] = []

        self._notice = QLabel()
        self._notice.setObjectName("propertiesNotice")
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)

        # The section group boxes go straight into this layout, with
        # no intermediate container widget. That is not tidiness: an
        # intermediate widget's QWidgetItem caches a size hint taken
        # before its own children exist, and nothing re-validates it
        # while the dialog is still unshown - measured, the outer
        # layout kept reporting 0x0 and the dialog opened as a 520x125
        # sliver. Adding the group boxes to *this* layout invalidates
        # this layout, which is the one the dialog is sized from.
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.addWidget(self._notice)
        self._content_layout.addStretch(1)
        content = self._content

        scroll = _ReportScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setAccessibleName(self.tr("Document properties"))
        self._scroll = scroll

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.copy_button = self.buttons.addButton(
            self.tr("&Copy to Clipboard"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.edit_metadata_button = self.buttons.addButton(
            self.tr("&Edit Metadata..."), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.copy_button.clicked.connect(self._copy_to_clipboard)
        self.edit_metadata_button.clicked.connect(self._edit_metadata)
        self.edit_metadata_button.setEnabled(on_edit_metadata is not None)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(self.buttons)
        self.setMinimumWidth(_MINIMUM_WIDTH)

        self.set_info(info)

    # --- sizing -----------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        """Trim the dead space Qt's height hint leaves behind.

        A word-wrapping QLabel reports its height at its own
        *preferred* width, which is narrower than the width this
        dialog actually opens at - so the height Qt derives from that
        hint is too tall by however much less the text wraps once the
        real width is known. Measured on a two-page fixture: the
        content hints 857px tall at its preferred 276px width, but
        needs only 704px at the 482px it really gets, leaving ~200px
        of empty space above the button row on any screen tall enough
        not to hit Qt's own two-thirds cap first (which is exactly why
        an offscreen grab() doesn't show it - the cap hides it).

        Only ever shrinks, never grows, so that cap, the minimum width
        and any later user resize all still win.
        """
        super().showEvent(event)
        self._shrink_to_content()

    def _shrink_to_content(self) -> None:
        """Resize down to the height the content actually needs at the
        width it actually has. A no-op when the content is already
        taller than the window (the scroll area's job from there)."""
        content = self._scroll.widget()
        layout = None if content is None else content.layout()
        if layout is None or not layout.hasHeightForWidth():  # pragma: no cover
            return
        # Everything that isn't the scrollable viewport: the button
        # row, the layout margins and spacing, the frame.
        chrome = self.height() - self._scroll.viewport().height()
        target = layout.heightForWidth(self._scroll.viewport().width()) + chrome
        if target < self.height():
            self.resize(self.width(), target)

    # --- content ----------------------------------------------------------

    def set_info(self, info: DocumentInfo) -> None:
        """Replace the report shown, rebuilding every row. Used both
        for the initial fill and to refresh after a metadata edit."""
        self._info = info
        self._sections = self._build_sections(info)
        self._rebuild_widgets()

    def report_text(self) -> str:
        """The whole report as plain text, for the clipboard - a
        labelled layout matching what's on screen, deliberately not a
        JSON/`repr` dump, since the point is pasting it into a mail or
        a ticket."""
        title = self.tr("Document Properties")
        lines = [title, "=" * len(title)]
        width = max(
            [_TEXT_LABEL_WIDTH, *(len(label) + 2 for _, rows in self._sections for label, _ in rows)]
        )
        for section_title, rows in self._sections:
            lines.extend(["", section_title, "-" * len(section_title)])
            for label, value in rows:
                prefix = f"{label + ':':<{width}}"
                value_lines = value.split("\n")
                lines.append(f"{prefix}{value_lines[0]}")
                lines.extend(f"{' ' * width}{extra}" for extra in value_lines[1:])
        return "\n".join(lines) + "\n"

    def _rebuild_widgets(self) -> None:
        # Everything after the notice (index 0) is regenerated: the
        # previous run's group boxes and the trailing stretch.
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(1)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # Unparent before deleteLater: the deferred-delete
                # event isn't delivered until the event loop turns, and
                # a still-parented widget would keep painting where the
                # layout used to put it in the meantime.
                widget.setParent(None)
                widget.deleteLater()

        if self._info.read_error:
            self._notice.setText(self._info.read_error)
            self._notice.setVisible(True)
        else:
            self._notice.setVisible(False)

        for section_title, rows in self._sections:
            group = QGroupBox(section_title)
            form = QFormLayout(group)
            for label, value in rows:
                value_label = QLabel(value)
                value_label.setWordWrap(True)
                # Let the user select and copy one value on its own
                # (the Copy button takes the whole report).
                value_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                value_label.setAccessibleName(label)
                form.addRow(f"{label}:", value_label)
            self._content_layout.addWidget(group)
        self._content_layout.addStretch(1)

    # --- buttons ------------------------------------------------------------

    def _copy_to_clipboard(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:  # pragma: no cover - no clipboard on this platform
            log.warning("No clipboard available; properties not copied")
            return
        clipboard.setText(self.report_text())

    def _edit_metadata(self) -> None:
        if self._on_edit_metadata is None:  # pragma: no cover - button is disabled
            return
        updated = self._on_edit_metadata()
        if updated is not None:
            self.set_info(updated)

    # --- row building --------------------------------------------------------

    def _build_sections(self, info: DocumentInfo) -> list[Section]:
        return [
            (self.tr("Document metadata"), self._metadata_rows(info)),
            (self.tr("File on disk"), self._file_rows(info)),
            (self.tr("Page geometry"), self._geometry_rows(info)),
            (self.tr("PDF technical"), self._technical_rows(info)),
        ]

    def _unavailable_rows(self) -> list[tuple[str, str]]:
        return [(self.tr("Status"), self.tr("Unavailable - the document could not be read"))]

    def _text_value(self, value: str | None) -> str:
        """"(not set)" for an absent field, "(empty)" for one that is
        present but blank - the two are different facts and a blank row
        would hide both."""
        if value is None:
            return self.tr("(not set)")
        if not value.strip():
            return self.tr("(empty)")
        return value

    def _metadata_rows(self, info: DocumentInfo) -> list[tuple[str, str]]:
        metadata = info.metadata
        if metadata is None:
            return self._unavailable_rows()
        return [
            (self.tr("Title"), self._text_value(metadata.title)),
            (self.tr("Author"), self._text_value(metadata.author)),
            (self.tr("Subject"), self._text_value(metadata.subject)),
            (self.tr("Keywords"), self._text_value(metadata.keywords)),
            (self.tr("Creator"), self._text_value(metadata.creator)),
            (self.tr("Producer"), self._text_value(metadata.producer)),
            (self.tr("Creation date"), self._text_value(metadata.creation_date)),
            (self.tr("Modification date"), self._text_value(metadata.mod_date)),
        ]

    def _file_rows(self, info: DocumentInfo) -> list[tuple[str, str]]:
        file_info = info.file
        rows: list[tuple[str, str]] = []
        if file_info.path is None:
            rows.append(
                (
                    self.tr("Location"),
                    self.tr("(not saved to disk yet - this document was built in the app)"),
                )
            )
        else:
            rows.append((self.tr("Location"), str(file_info.path)))

        if file_info.path is not None and not file_info.exists:
            missing = self.tr("(file not found)")
            rows.extend(
                [
                    (self.tr("Size"), missing),
                    (self.tr("Created"), missing),
                    (self.tr("Modified"), missing),
                ]
            )
        elif file_info.path is not None:
            size = file_info.size_bytes or 0
            rows.append(
                (
                    self.tr("Size"),
                    self.tr("{0} ({1} bytes)").format(human_readable_size(size), f"{size:,}"),
                )
            )
            rows.append(
                (
                    self.tr("Created"),
                    self._timestamp(file_info.created)
                    or self.tr("(not recorded by this filesystem)"),
                )
            )
            rows.append(
                (
                    self.tr("Modified"),
                    self._timestamp(file_info.modified) or self.tr("(unknown)"),
                )
            )

        # The honesty row: every other section describes the working
        # copy (the current, possibly-unsaved edit state), while the
        # timestamps just above describe the untouched file on disk.
        # Saying so is what keeps "modified: 3 days ago" from reading
        # as "your 20 edits are safely on disk".
        rows.append(
            (
                self.tr("Unsaved changes"),
                self.tr(
                    "Yes - the properties shown here describe your current edits, "
                    "not the file on disk"
                )
                if file_info.has_unsaved_changes
                else self.tr("No"),
            )
        )
        return rows

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else None

    def _geometry_rows(self, info: DocumentInfo) -> list[tuple[str, str]]:
        geometry = info.geometry
        if geometry is None:
            return self._unavailable_rows()

        rows = [(self.tr("Pages"), str(geometry.page_count))]
        rows.append((self.tr("Page size"), self._page_size_value(geometry)))
        rows.append((self.tr("Orientation"), self._orientation_value(geometry)))
        rows.append((self.tr("Rotation"), self._rotation_value(geometry)))
        if geometry.unreadable_pages:
            rows.append(
                (
                    self.tr("Note"),
                    self.tr("{0} page(s) could not be measured").format(
                        geometry.unreadable_pages
                    ),
                )
            )
        return rows

    def _page_size_value(self, geometry: PageGeometryInfo) -> str:
        groups = geometry.size_groups
        if not groups:
            return self.tr("(unknown)")
        if len(groups) == 1:
            return self._size_text(groups[0])
        # Mixed sizes are called out explicitly and every size is
        # listed - reporting page 1's size as if it were the whole
        # document's would be a quiet lie.
        header = self.tr("Mixed ({0} sizes)").format(len(groups))
        lines = [
            self.tr("{0} - {1} page(s)").format(self._size_text(group), group.page_count)
            for group in groups
        ]
        return "\n".join([header, *lines])

    def _size_text(self, group: PageSizeGroup) -> str:
        return self.tr("{0} x {1} pt ({2} x {3} mm, {4} x {5} in)").format(
            _number(group.width_pt, 2),
            _number(group.height_pt, 2),
            f"{group.width_mm:.1f}",
            f"{group.height_mm:.1f}",
            f"{group.width_in:.2f}",
            f"{group.height_in:.2f}",
        )

    def _orientation_value(self, geometry: PageGeometryInfo) -> str:
        names = {
            "portrait": self.tr("Portrait"),
            "landscape": self.tr("Landscape"),
            "square": self.tr("Square"),
        }
        counts: dict[str, int] = {}
        for group in geometry.size_groups:
            counts[group.orientation] = counts.get(group.orientation, 0) + group.page_count
        if not counts:
            return self.tr("(unknown)")
        if len(counts) == 1:
            return names[next(iter(counts))]
        parts = [
            self.tr("{0} on {1} page(s)").format(names[orientation], count)
            for orientation, count in sorted(counts.items(), key=lambda item: -item[1])
        ]
        return self.tr("Mixed") + " - " + ", ".join(parts)

    def _rotation_value(self, geometry: PageGeometryInfo) -> str:
        groups = geometry.rotation_groups
        if not groups:
            return self.tr("(unknown)")
        if len(groups) == 1:
            degrees = groups[0].degrees
            if degrees == 0:
                return self.tr("None (0\u00b0)")
            return self.tr("{0}\u00b0 (all pages)").format(degrees)
        parts = [
            self.tr("{0}\u00b0 on {1} page(s)").format(group.degrees, group.page_count)
            for group in groups
        ]
        return self.tr("Mixed") + " - " + ", ".join(parts)

    def _technical_rows(self, info: DocumentInfo) -> list[tuple[str, str]]:
        technical = info.technical
        rows = [
            (self.tr("PDF version"), technical.pdf_version or self.tr("(unknown)")),
            (self.tr("Fast web view (linearized)"), self._yes_no(technical.linearized)),
            (self.tr("Tagged (accessible)"), self._yes_no(technical.tagged)),
            (self.tr("Encrypted"), self._yes_no(technical.encrypted)),
        ]
        if not technical.encrypted:
            return rows

        permissions = technical.permissions
        if permissions is None:
            rows.append((self.tr("Permissions"), self.tr("(unknown - password required)")))
            return rows

        if not permissions.printing:
            printing = self.tr("Not allowed")
        elif permissions.high_quality_printing:
            printing = self.tr("Allowed")
        else:
            printing = self.tr("Allowed (low resolution only)")
        rows.extend(
            [
                (self.tr("Printing"), printing),
                (self.tr("Copying content"), self._allowed(permissions.copying)),
                (self.tr("Modifying"), self._allowed(permissions.modifying)),
                (self.tr("Annotating"), self._allowed(permissions.annotating)),
            ]
        )
        return rows

    def _yes_no(self, value: bool | None) -> str:
        if value is None:
            return self.tr("(unknown)")
        return self.tr("Yes") if value else self.tr("No")

    def _allowed(self, value: bool) -> str:
        return self.tr("Allowed") if value else self.tr("Not allowed")

    def button_for_copy(self) -> QPushButton:
        """The real Copy button, so a test can click it and run the
        real handler rather than calling the private slot."""
        return self.copy_button


def _number(value: float, decimals: int = 0) -> str:
    """A point dimension without a pointless trailing ".0" - "612", not
    "612.00", while a genuinely fractional 595.276 still shows as
    595.28. Only points get this treatment: mm and inches keep fixed
    decimals so the two numbers in "215.9 x 279.4 mm" always line up
    with each other."""
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
