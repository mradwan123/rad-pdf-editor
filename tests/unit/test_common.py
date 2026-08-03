"""Unit tests for core/ops/common.py's shared helpers.

resolve_page_targets in particular: consolidated from four
near-identical per-module copies during a bug-fix pass after finding
that duplicate page numbers (e.g. pages=[1, 1]) silently double-applied
per-page operations (confirmed: RotatePagesOperation with pages=[1, 1]
and angle=90 produced a 180-degree rotation instead of 90). These tests
guard against that regression across every op that uses it.
"""

from __future__ import annotations

import pytest

from core.errors import OperationError
from core.ops.common import resolve_page_targets


def test_empty_pages_means_all_pages_ascending() -> None:
    assert resolve_page_targets([], 5) == [1, 2, 3, 4, 5]


def test_explicit_pages_are_returned() -> None:
    assert resolve_page_targets([3, 1], 5) == [1, 3]


def test_duplicate_pages_are_deduplicated() -> None:
    assert resolve_page_targets([1, 1, 2], 3) == [1, 2]


def test_result_is_always_ascending_regardless_of_input_order() -> None:
    assert resolve_page_targets([3, 1, 2], 3) == [1, 2, 3]


def test_out_of_range_page_raises() -> None:
    with pytest.raises(OperationError):
        resolve_page_targets([99], 3)


def test_zero_page_raises() -> None:
    with pytest.raises(OperationError):
        resolve_page_targets([0], 3)


def test_negative_page_raises() -> None:
    with pytest.raises(OperationError):
        resolve_page_targets([-1], 3)
