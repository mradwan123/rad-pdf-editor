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
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from core.registry.registry import Registry, discover_and_load
from gui.dialogs.fill_form_dialog import FillFormDialog
from gui.dialogs.merge_dialog import MergeDialog
from gui.dialogs.rotate_dialog import RotateDialog
from gui.dialogs.run_workflow_dialog import RunWorkflowDialog
from gui.dialogs.tab_placement_dialog import TabPlacementDialog
from gui.dialogs.tool_dialog_registry import TOOL_DIALOGS
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


# --- Real button-text-truncation audit --------------------------------
#
# The 25% flat multiplier above was a blanket heuristic, not a verified
# guarantee that no button in the app ever renders narrower than its
# own text needs. This section actually measures it, per dialog,
# instead of trusting the ratio.
#
# The authoritative "does this button need more room than it has"
# check is comparing the button's *live rendered width* against its
# own `QPushButton.sizeHint().width()` - Qt computes that value from
# the real font metrics *and* the active style's real button padding
# (confirmed by hand: a naive `QFontMetrics(...).horizontalAdvance(text)
# + a guessed flat pixel padding` constant is NOT a reliable stand-in
# for this - "Remove Selected" needs only 110px by Qt's own style-aware
# computation, but a flat +24px-over-text-advance guess claims it needs
# 120px, which would have flagged a false positive on a button that
# actually renders every character just fine). `sizeHint()` stays
# accurate regardless of what a surrounding layout later does to the
# widget (confirmed: reading it back on a button already placed in a
# stretched QHBoxLayout and shown still returns its own unstretched
# preferred size, not the layout-assigned geometry) - so it's safe to
# call on the same live, already-shown button instance being measured.
def _assert_no_button_is_narrower_than_its_own_text_needs(dialog: QDialog) -> None:
    dialog.show()
    QApplication.processEvents()
    try:
        buttons = dialog.findChildren(QPushButton)
        assert buttons, "expected at least one QPushButton in this dialog"
        for button in buttons:
            if not button.text():
                continue
            natural_width = button.sizeHint().width()
            assert button.width() >= natural_width, (
                f"button {button.text()!r} rendered at {button.width()}px "
                f"but needs {natural_width}px to show its full text without "
                "the user manually resizing the dialog"
            )
    finally:
        dialog.close()


@pytest.mark.parametrize("tool_id", sorted(TOOL_DIALOGS.keys()))
def test_every_tool_dialog_button_fits_its_own_text(tool_id: str) -> None:
    # Covers every BaseToolDialog subclass reachable through the Tools
    # menu / Workflow builder's "Add Step..." picker - all of Merge,
    # Rotate, Watermark, FillForm's placeholder factory, etc. - not
    # just the 3-4 dialogs the original 25%-ratio tests happened to
    # cover.
    _qapp()
    dialog = TOOL_DIALOGS[tool_id](None)
    _assert_no_button_is_narrower_than_its_own_text_needs(dialog)


def test_fill_form_dialog_with_real_long_field_names_fits_its_buttons() -> None:
    # The generic TOOL_DIALOGS sweep above uses the placeholder
    # zero-field factory (see tool_dialog_registry.py's comment on why
    # fill_form can't be constructed generically) - this exercises the
    # real non-standard constructor with actual field names, including
    # a deliberately long one.
    _qapp()
    dialog = FillFormDialog(["name", "email", "date", "a_pretty_long_field_name_example"])
    _assert_no_button_is_narrower_than_its_own_text_needs(dialog)


def test_workflow_builder_dialog_buttons_fit_their_text() -> None:
    # WorkflowBuilderDialog's Add Step/Remove Selected/Move Up/Move
    # Down row is built the same way MergeDialog's is (a QHBoxLayout of
    # QPushButtons inside add_full_width) - worth its own check since
    # that's the shape most likely to have a longest-label button
    # (Remove Selected) get squeezed if the row's leftover width were
    # ever redistributed unevenly among the buttons.
    _qapp()
    registry = Registry()
    discover_and_load(registry)
    dialog = WorkflowBuilderDialog(registry)
    _assert_no_button_is_narrower_than_its_own_text_needs(dialog)


def test_run_workflow_dialog_buttons_fit_their_text() -> None:
    _qapp()
    dialog = RunWorkflowDialog(["My Saved Workflow With A Longish Name"])
    _assert_no_button_is_narrower_than_its_own_text_needs(dialog)


@pytest.mark.parametrize("document_name", ["some_document.pdf", None])
def test_tab_placement_dialog_buttons_fit_their_text(document_name: str | None) -> None:
    # TabPlacementDialog is deliberately a plain QDialog subclass, not
    # a BaseToolDialog subclass (see the module's own docstring for
    # why: QMessageBox.setButtonText doesn't exist in Qt 6, and
    # QMessageBox's own .exec() isn't patchable, so a hand-rolled
    # QMessageBox wasn't an option either). That means it does *not*
    # inherit the 25%-wider sizeHint() override at all - so this is
    # the one dialog in the audit where "does it need the override" is
    # a real, not rhetorical, question. Its longest button ("Replace
    # Current Tab") is measured directly here rather than assumed safe
    # by association with the other BaseToolDialog checks above.
    _qapp()
    dialog = TabPlacementDialog(document_name)
    _assert_no_button_is_narrower_than_its_own_text_needs(dialog)
