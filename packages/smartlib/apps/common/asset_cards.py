from __future__ import annotations

from pathlib import Path
from typing import Any


def configure_asset_card_list(widget: Any, QtCore: Any, QtWidgets: Any, *, compact: bool = False) -> None:
    """Apply the Asset Manager card view contract to a QListWidget."""

    icon_size = QtCore.QSize(128, 72)
    grid_size = QtCore.QSize(150, 150) if compact else QtCore.QSize(160, 168)
    widget.setViewMode(QtWidgets.QListView.IconMode)
    widget.setResizeMode(QtWidgets.QListView.Adjust)
    widget.setMovement(QtWidgets.QListView.Static)
    widget.setIconSize(icon_size)
    widget.setGridSize(grid_size)
    widget.setUniformItemSizes(True)
    widget.setWordWrap(True)
    widget.setStyleSheet(asset_card_stylesheet())


def asset_card_stylesheet() -> str:
    return """
        QListWidget {
            background: #292929;
            border: 1px solid #3a3a3a;
        }
        QListWidget::item {
            background: #383838;
            border: 1px solid #4a4a4a;
            padding: 5px;
            margin: 4px;
            color: #e0e0e0;
        }
        QListWidget::item:selected {
            background: #4d6f86;
            border: 1px solid #7fa8c2;
        }
    """


def asset_card_text(
    *,
    asset: str,
    category: str = "",
    group: str = "",
    variant: str = "",
    status: str = "",
    asset_type: str = "",
    description: str = "",
) -> str:
    status = status or "-"
    type_text = asset_type or category or "-"
    path_text = "/".join(part for part in (category, group) if part)
    if variant and variant != "default":
        path_text = f"{path_text} / {variant}" if path_text else variant
    lines = [
        asset,
        path_text,
        f"Status: {status}",
        f"Type: {type_text}",
    ]
    if description:
        lines.append(str(description)[:34])
    while len(lines) < 5:
        lines.append("")
    return "\n".join(lines[:5])


def asset_tooltip(
    *,
    asset: str,
    category: str = "",
    group: str = "",
    variant: str = "",
    status: str = "",
    description: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    rows = [
        f"Asset: {asset}",
        f"Category: {category}",
        f"Group: {group}",
        f"Variant: {variant or 'default'}",
    ]
    if status:
        rows.append(f"status: {status}")
    if description:
        rows.append(f"description: {description}")
    for key, value in (extra or {}).items():
        if value not in ("", None):
            rows.append(f"{key}: {value}")
    return "\n".join(rows)


def asset_icon(QtCore: Any, QtGui: Any, *, thumbnail: str | Path = "", label: str = "", width: int = 128, height: int = 72):
    thumbnail_path = Path(thumbnail) if thumbnail else None
    if thumbnail_path and thumbnail_path.exists():
        pixmap = QtGui.QPixmap(str(thumbnail_path))
        if not pixmap.isNull():
            return QtGui.QIcon(thumbnail_canvas(QtCore, QtGui, pixmap, width=width, height=height))

    pixmap = QtGui.QPixmap(width, height)
    pixmap.fill(QtGui.QColor("#2f343a"))
    painter = QtGui.QPainter(pixmap)
    painter.setPen(QtGui.QColor("#9fb6c8"))
    font = painter.font()
    font.setPixelSize(18)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, str(label or "")[:12])
    painter.end()
    return QtGui.QIcon(pixmap)


def thumbnail_canvas(QtCore: Any, QtGui: Any, source: Any, *, width: int = 128, height: int = 72):
    canvas = QtGui.QPixmap(width, height)
    canvas.fill(QtGui.QColor("#2f343a"))
    scaled = source.scaled(
        max(1, width - 8),
        max(1, height - 8),
        QtCore.Qt.KeepAspectRatio,
        QtCore.Qt.SmoothTransformation,
    )
    painter = QtGui.QPainter(canvas)
    painter.drawPixmap((canvas.width() - scaled.width()) // 2, (canvas.height() - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


def set_label_thumbnail(QtCore: Any, QtGui: Any, label_widget: Any, thumbnail: str | Path, fallback_text: str = "Thumbnail") -> None:
    path = Path(thumbnail) if thumbnail else None
    if not path or not path.exists():
        label_widget.clear()
        label_widget.setText(fallback_text)
        return
    pixmap = QtGui.QPixmap(str(path))
    if pixmap.isNull():
        label_widget.clear()
        label_widget.setText(fallback_text)
        return
    label_widget.setPixmap(
        pixmap.scaled(label_widget.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    )

