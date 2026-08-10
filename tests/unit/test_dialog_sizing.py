"""Unit tests for BaseToolDialog's "25% wider" sizing override
(gui/dialogs/base_tool_dialog.py).

Every tool dialog in this codebase is sized purely by Qt's own
layout-driven `sizeHint()` machinery - no dialog anywhere calls
`resize()`/`setFixedSize()`/`setMinimumWidth()` (confirmed by grep
across gui/dialogs/*.py). `BaseToolDialog.sizeHint()` widens whatever
the natural layout-computed size would have been by `_WIDTH_MULTIPLIER`
(1.25), which is exactly what Qt consults to size a dialog on its
first `show()`/`exec()` when nothing else has resized it.

These tests compare the *overridden* `sizeHint()` against the
*unmodified* `QDialog.sizeHint()` implementation - called directly,
unbound, on the same live instance - which is exactly what
`BaseToolDialog.sizeHint()` would have returned had the override not
existed. That gives a real "before vs. after" comparison without
needing two dialog instances or a saved baseline constant.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from core.registry.registry import Registry, discover_and_load
from gui.dialogs.fill_form_dialog import FillFormDialog
from gui.dialogs.merge_dialog import MergeDialog
from gui.dialogs.rotate_dialog import RotateDialog
from gui.dialogs.workflow_builder_dialog import WorkflowBuilderDialog

_TOLERANCE = 0.03


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _assert_25_percent_wider(dialog: QDialog) -> None:
    natural_width = QDialog.sizeHint(dialog).width()
    natural_height = QDialog.sizeHint(dialog).height()
    widened = dialog.sizeHint()

    assert widened.width() == pytest.approx(natural_width * 1.25, rel=_TOLERANCE)
    # Only width changes - height is untouched.
    assert widened.height() == natural_height


def test_standard_constructor_dialog_is_25_percent_wider() -> None:
    _qapp()
    _assert_25_percent_wider(RotateDialog())


def test_add_full_width_dialog_is_25_percent_wider() -> None:
    # MergeDialog exercises add_full_width's custom widget block, not
    # just plain form rows.
    _qapp()
    _assert_25_percent_wider(MergeDialog())


def test_fill_form_dialog_non_standard_constructor_is_25_percent_wider() -> None:
    # FillFormDialog's __init__ is (field_names, parent=None), not the
    # plain (parent=None) every ordinary tool dialog uses.
    _qapp()
    _assert_25_percent_wider(FillFormDialog(["name", "email", "date"]))


def test_workflow_builder_dialog_non_standard_constructor_is_25_percent_wider() -> None:
    # WorkflowBuilderDialog's __init__ is (registry, parent=None).
    _qapp()
    registry = Registry()
    discover_and_load(registry)
    _assert_25_percent_wider(WorkflowBuilderDialog(registry))


def test_shown_dialog_actually_uses_the_widened_size() -> None:
    # sizeHint() alone proves the computation; this proves Qt's real
    # show() path actually applies it to a live top-level widget size,
    # not just that the overridden method returns the right number.
    _qapp()
    dialog = RotateDialog()
    natural_width = QDialog.sizeHint(dialog).width()
    dialog.show()
    try:
        assert dialog.width() == pytest.approx(natural_width * 1.25, rel=_TOLERANCE)
    finally:
        dialog.close()
