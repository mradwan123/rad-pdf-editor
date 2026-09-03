"""The application's icon set, drawn rather than shipped.

Phase 6g (docs/GUI_PLAN.md §3.6). Before this the app had **no icons at
all** - the toolbar was four text labels - which is the single most
visible thing separating it from a finished tool.

Drawn with `QPainter`, the same technique `gui/resources.py` already
uses for the app mark, for three reasons that all still hold:

- no binary assets in the repo, and nothing for PyInstaller to bundle
  as `datas` (`packaging/rad-pdf-editor.spec` stays as it is);
- they re-colour with the palette, so a light/dark switch needs no
  second set of files; and
- an icon is a few vector calls, so the set costs less than sourcing,
  licensing and checking in an SVG family.

Every glyph is drawn inside a nominal 24x24 box and scaled, so they
line up regardless of the requested size.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

#: Every glyph is authored against this box and scaled to the request.
_GRID = 24.0


def _pen(painter: QPainter, colour: QColor, width: float = 2.0) -> None:
    pen = QPen(colour, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _draw_open(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawPolyline([QPointF(3, 19), QPointF(3, 6), QPointF(10, 6), QPointF(12, 9), QPointF(20, 9)])
    p.drawPolyline([QPointF(3, 19), QPointF(6, 12), QPointF(22, 12), QPointF(19, 19), QPointF(3, 19)])


def _draw_save(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawRect(QRectF(4, 4, 16, 16))
    p.drawRect(QRectF(8, 4, 8, 6))
    p.drawRect(QRectF(7, 13, 10, 7))


def _draw_undo(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    path = QPainterPath(QPointF(5, 11))
    path.arcTo(QRectF(5, 5, 14, 12), 180, -220)
    p.drawPath(path)
    p.drawPolyline([QPointF(5, 5), QPointF(5, 11), QPointF(11, 11)])


def _draw_redo(p: QPainter, c: QColor) -> None:
    p.save()
    p.translate(_GRID, 0)
    p.scale(-1, 1)
    _draw_undo(p, c)
    p.restore()


def _draw_zoom(p: QPainter, c: QColor, *, plus: bool) -> None:
    _pen(p, c)
    p.drawEllipse(QRectF(4, 4, 12, 12))
    p.drawLine(QPointF(15, 15), QPointF(20, 20))
    p.drawLine(QPointF(7, 10), QPointF(13, 10))
    if plus:
        p.drawLine(QPointF(10, 7), QPointF(10, 13))


def _draw_fit_width(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawRect(QRectF(4, 6, 16, 12))
    p.drawLine(QPointF(2, 12), QPointF(7, 12))
    p.drawLine(QPointF(17, 12), QPointF(22, 12))


def _draw_fit_page(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawRect(QRectF(7, 3, 10, 18))
    p.drawLine(QPointF(12, 1), QPointF(12, 5))
    p.drawLine(QPointF(12, 19), QPointF(12, 23))


def _draw_find(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawEllipse(QRectF(4, 4, 12, 12))
    p.drawLine(QPointF(15, 15), QPointF(20, 20))


def _draw_select(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawLine(QPointF(9, 4), QPointF(15, 4))
    p.drawLine(QPointF(12, 4), QPointF(12, 20))
    p.drawLine(QPointF(9, 20), QPointF(15, 20))


def _draw_highlight(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawLine(QPointF(4, 17), QPointF(20, 17))
    p.setBrush(c)
    p.setOpacity(0.35)
    p.drawRect(QRectF(4, 8, 16, 6))
    p.setOpacity(1.0)


def _draw_rect(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawRect(QRectF(4, 6, 16, 12))


def _draw_circle(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawEllipse(QRectF(4, 6, 16, 12))


def _draw_line(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawLine(QPointF(5, 19), QPointF(19, 5))


def _draw_ink(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    path = QPainterPath(QPointF(4, 16))
    path.cubicTo(QPointF(8, 6), QPointF(12, 22), QPointF(20, 8))
    p.drawPath(path)


def _draw_note(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawRect(QRectF(4, 5, 16, 12))
    p.drawLine(QPointF(7, 9), QPointF(17, 9))
    p.drawLine(QPointF(7, 13), QPointF(13, 13))
    p.drawPolyline([QPointF(8, 17), QPointF(8, 21), QPointF(12, 17)])


def _draw_redact(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawRect(QRectF(4, 6, 16, 12))
    p.setBrush(c)
    p.drawRect(QRectF(6, 9, 12, 6))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _draw_delete(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawLine(QPointF(4, 7), QPointF(20, 7))
    p.drawPolyline([QPointF(6, 7), QPointF(7, 20), QPointF(17, 20), QPointF(18, 7)])
    p.drawLine(QPointF(10, 4), QPointF(14, 4))


def _draw_history(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawEllipse(QRectF(4, 4, 16, 16))
    p.drawPolyline([QPointF(12, 8), QPointF(12, 12), QPointF(16, 14)])


def _draw_properties(p: QPainter, c: QColor) -> None:
    _pen(p, c)
    p.drawRect(QRectF(5, 3, 14, 18))
    p.drawLine(QPointF(8, 8), QPointF(16, 8))
    p.drawLine(QPointF(8, 12), QPointF(16, 12))
    p.drawLine(QPointF(8, 16), QPointF(13, 16))


#: name -> glyph painter. Adding a tool means adding one entry here.
_GLYPHS: dict[str, Callable[[QPainter, QColor], None]] = {
    "open": _draw_open,
    "save": _draw_save,
    "undo": _draw_undo,
    "redo": _draw_redo,
    "zoom_in": lambda p, c: _draw_zoom(p, c, plus=True),
    "zoom_out": lambda p, c: _draw_zoom(p, c, plus=False),
    "fit_width": _draw_fit_width,
    "fit_page": _draw_fit_page,
    "find": _draw_find,
    "select": _draw_select,
    "highlight": _draw_highlight,
    "rect": _draw_rect,
    "circle": _draw_circle,
    "line": _draw_line,
    "ink": _draw_ink,
    "note": _draw_note,
    "redact": _draw_redact,
    "delete": _draw_delete,
    "history": _draw_history,
    "properties": _draw_properties,
}


def icon_names() -> list[str]:
    return sorted(_GLYPHS)


def build_icon(name: str, colour: QColor, size: int = 24) -> QIcon:
    """A themed icon, drawn at `size` pixels.

    Unknown names return a null icon rather than raising: a missing
    glyph should leave an action looking plain, never stop the window
    being built.
    """
    glyph = _GLYPHS.get(name)
    if glyph is None:
        return QIcon()
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / _GRID, size / _GRID)
    glyph(painter, colour)
    painter.end()
    return QIcon(pixmap)
