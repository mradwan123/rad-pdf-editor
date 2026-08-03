"""Small parsing helpers shared by tool dialogs."""

from __future__ import annotations


def parse_int_list(text: str) -> list[int]:
    """Parse a comma-separated list of page numbers, e.g. "1,3,5"."""
    return [int(x) for x in text.split(",") if x.strip()]
