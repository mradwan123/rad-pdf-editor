"""The "Rad PDF Editor" mark - drawn programmatically via QPainter
rather than checked in as a binary asset, so the logo lives in code
and stays trivially themed to gui/palette.py's silver/gray/black
palette (no external design tool round-trip needed to tweak it).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _draw_badge(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    margin = size * 0.04
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = size * 0.22

    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#2c2d31"))
    gradient.setColorAt(1.0, QColor("#131315"))

    badge_path = QPainterPath()
    badge_path.addRoundedRect(rect, radius, radius)
    painter.fillPath(badge_path, gradient)

    border_pen = painter.pen()
    border_pen.setColor(QColor("#8b8f99"))
    border_pen.setWidthF(max(1.0, size * 0.02))
    painter.setPen(border_pen)
    painter.drawPath(badge_path)

    # Folded-corner page glyph (upper right) - a small nod to "this is
    # a document editor" alongside the monogram.
    fold = size * 0.20
    fold_x = rect.right() - fold - size * 0.10
    fold_y = rect.top() + size * 0.10
    fold_path = QPainterPath()
    fold_path.moveTo(fold_x, fold_y)
    fold_path.lineTo(fold_x + fold, fold_y)
    fold_path.lineTo(fold_x + fold, fold_y + fold)
    fold_path.closeSubpath()
    painter.fillPath(fold_path, QColor("#c7cad1"))

    font = QFont("Sans Serif")
    font.setBold(True)
    font.setPixelSize(int(size * 0.5))
    painter.setFont(font)
    text_pen = painter.pen()
    text_pen.setColor(QColor("#f2f3f5"))
    painter.setPen(text_pen)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "R")

    painter.end()
    return pixmap


def build_app_icon() -> QIcon:
    """Multi-resolution app/window icon - the badge at each of the
    standard sizes Qt/the OS may request (taskbar, title bar, alt-tab)."""
    icon = QIcon()
    for size in _ICON_SIZES:
        icon.addPixmap(_draw_badge(size))
    return icon


def build_logo_pixmap(size: int = 128) -> QPixmap:
    """A single badge at `size`, for in-window branding (e.g. the
    empty-state welcome screen)."""
    return _draw_badge(size)
