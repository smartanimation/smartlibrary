from __future__ import annotations

from pathlib import Path


def report_inputs(construct, planned, composition=None, source_construct=None):
    """Inventory actual Construct inputs, source-only records and fixed products."""
    rows, seen = [], set()
    def add(raw, role="APPLIED"):
        row = dict(raw)
        row["type"] = row.get("component_type") or row.get("type") or "data"
        row.setdefault("role", role)
        if not row.get("enabled", True):
            row["state"] = "EXCLUDED"
        elif not row.get("state"):
            row["state"] = "RECORDED" if row.get("path") else "UNRESOLVED"
        key = (row["type"], row.get("name"), row.get("path"))
        if key not in seen:
            rows.append(row)
            seen.add(key)
    for row in construct.get("components") or []:
        add(row)
    for row in planned.get("inputs") or []:
        role = "SOURCE ONLY" if composition and row.get("type") in {"rig", "animation_curve", "usd"} else "APPLIED"
        add(row, role)
    if composition:
        for row in (source_construct or {}).get("components") or []:
            add(row, "SOURCE ONLY")
        def dependency(ref, name, kind, role="DEPENDENCY"):
            if not ref or not ref.get("path"):
                return
            version = ref.get("version") or next((part for part in reversed(Path(ref["path"]).parts) if part.startswith("v") and part[1:].isdigit()), "-")
            add({"type":kind, "name":name, "path":ref["path"], "version":version,
                 "sha256":ref.get("sha256", ""), "state":"PINNED", "role":role})
        dependency(composition.get("entrypoint"), "Shot entrypoint", "shot", "APPLIED")
        dependency(composition.get("source"), "Animation source", "source", "SOURCE ONLY")
        for member in composition.get("members", []):
            target = member["instance_id"]
            for product, ref in member["products"].items():
                dependency(ref, target, product, "APPLIED")
                for dep in ref.get("dependencies", []):
                    dependency(dep, target, "asset")
                dependency(ref.get("transfer_manifest"), target, "mapping")
        for target, ref in composition.get("looks", {}).items():
            dependency(ref, target, "look", "APPLIED")
            for dep in ref.get("dependencies", []):
                dependency(dep, target, "texture")
        for name, ref in composition.get("dependencies", {}).items():
            dependency(ref, name, "dependency")
        for target in composition.get("excluded_members", []):
            add({"type":"animation", "name":target, "enabled":False})
    return rows


def summary_inputs(inputs):
    """Main scene inputs for the PDF; the complete inventory stays in JSON."""
    detail_types = {"asset", "texture", "dependency", "mapping", "transfer", "rend", "deform", "skeleton", "source", "shot", "precomp", "render_manifest", "layer_camera"}
    animated = {r.get("name") for r in inputs if r.get("type") == "animation" and r.get("enabled", True)}
    rows, seen = [], set()
    for item in inputs:
        kind = item.get("type") or "data"
        if not item.get("enabled", True) or kind in detail_types or item.get("role") == "DEPENDENCY":
            continue
        if kind == "rig" and item.get("role") == "SOURCE ONLY" and item.get("name") in animated:
            continue
        key = (kind, item.get("name"))
        if key not in seen:
            seen.add(key)
            rows.append(item)
    return sorted(rows, key=lambda r: r.get("type") != "animation")


def render_review_report_pdf(*, report_path: str | Path, thumbnail_path: str | Path,
                             data: dict) -> Path:
    """Render a complete paginated Review Report using Maya's bundled Qt."""
    try:
        from PySide2 import QtCore, QtGui
    except ImportError:
        from PySide6 import QtCore, QtGui

    application = QtGui.QGuiApplication.instance()
    if application is None:
        application = QtGui.QGuiApplication([])

    thumbnail = QtGui.QImage(str(thumbnail_path))
    if thumbnail.isNull():
        raise ValueError(f"Clean Review thumbnail could not be loaded: {thumbnail_path}")
    target = Path(report_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    writer = QtGui.QPdfWriter(str(target))
    writer.setResolution(144)
    try:
        writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    except (AttributeError, TypeError):
        writer.setPageSize(QtGui.QPagedPaintDevice.A4)
    writer.setTitle("SmartPipeline Review Report")
    painter = QtGui.QPainter(writer)
    if not painter.isActive():
        raise RuntimeError(f"Review Report PDF could not be created: {target}")

    page = writer.pageLayout().paintRectPixels(writer.resolution())
    width, height = page.width(), page.height()
    margin = 64
    content_width = width - margin * 2
    navy = QtGui.QColor("#202a35")
    blue = QtGui.QColor("#2d78b7")
    ink = QtGui.QColor("#252a30")
    muted = QtGui.QColor("#68727c")
    line = QtGui.QColor("#d8dde2")
    paper = QtGui.QColor("#ffffff")
    painter.fillRect(page, paper)
    painter.fillRect(QtCore.QRect(0, 0, width, 150), navy)
    painter.fillRect(QtCore.QRect(0, 150, width, 7), blue)

    def font(size, bold=False):
        value = QtGui.QFont("Arial", size)
        value.setBold(bold)
        return value

    painter.setPen(QtGui.QColor("#ffffff"))
    painter.setFont(font(22, True))
    painter.drawText(margin, 72, "REVIEW REPORT")
    painter.setFont(font(11))
    painter.drawText(margin, 115, str(data.get("shot") or ""))
    painter.drawText(
        QtCore.QRect(margin, 82, content_width, 40),
        int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter),
        str(data.get("review_version") or ""),
    )

    y = 195
    image_height = 520
    scaled = thumbnail.scaled(
        content_width, image_height,
        QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation,
    )
    image_x = margin + (content_width - scaled.width()) // 2
    painter.fillRect(QtCore.QRect(margin, y, content_width, image_height), QtGui.QColor("#111111"))
    painter.drawImage(image_x, y + (image_height - scaled.height()) // 2, scaled)
    y += image_height + 42

    def heading(label):
        nonlocal y
        painter.setPen(blue)
        painter.setFont(font(12, True))
        painter.drawText(margin, y, label.upper())
        painter.setPen(line)
        painter.drawLine(margin, y + 12, width - margin, y + 12)
        y += 42

    def row(label, value, right_label="", right_value=""):
        nonlocal y
        middle = margin + content_width // 2
        value_font = font(9)

        def fitted(value, available_width):
            return QtGui.QFontMetrics(value_font).elidedText(
                str(value), QtCore.Qt.ElideMiddle, max(1, int(available_width))
            )

        painter.setFont(font(9, True))
        painter.setPen(muted)
        painter.drawText(margin, y, str(label))
        painter.setFont(value_font)
        painter.setPen(ink)
        painter.drawText(
            margin + 145, y, fitted(value, middle - margin - 165)
        )
        if right_label:
            painter.setFont(font(9, True))
            painter.setPen(muted)
            painter.drawText(middle, y, str(right_label))
            painter.setFont(value_font)
            painter.setPen(ink)
            painter.drawText(
                middle + 130, y,
                fitted(right_value, width - margin - middle - 140),
            )
        y += 29

    heading("Overview")
    row("Project", data.get("project", ""), "Department", data.get("department", ""))
    row("Task", data.get("task", ""), "Created", data.get("created_at", ""))
    row("Frames", data.get("frame_range", ""), "FPS", data.get("fps", ""))
    row("Resolution", data.get("resolution", ""), "Source", data.get("source_file", ""))
    row("AEP Source", data.get("aep_file", ""), "Render Copy", data.get("render_aep_file", ""))

    camera = data.get("camera") or {}
    heading("Review Layer Camera")
    row("Camera", camera.get("name", ""), "Focal Length", camera.get("focal_length", ""))
    row("Field of View", camera.get("field_of_view", ""), "Film Fit", camera.get("film_fit", ""))

    def footer():
        painter.setPen(muted)
        painter.setFont(font(8))
        painter.drawText(QtCore.QRect(margin, height - 65, content_width, 25),
                         int(QtCore.Qt.AlignCenter), "Generated by SmartPipeline - thumbnail extracted from the clean Review Movie")

    heading("Resolved Inputs")
    for item in summary_inputs(data.get("inputs") or []):
        if y + 29 > height - 100:
            footer()
            writer.newPage()
            painter.fillRect(page, paper)
            y = 75
            heading("Resolved Inputs (continued)")
        values = [str(item.get("type") or "data"), str(item.get("name") or "main"),
                  str(item.get("context") or "Version"), str(item.get("version") or "-")]
        fractions = [0.22, 0.32, 0.14, 0.32]
        x = margin
        for value, fraction in zip(values, fractions):
            column_width = int(content_width * fraction)
            painter.setFont(font(9))
            painter.setPen(ink)
            text = QtGui.QFontMetrics(painter.font()).elidedText(value, QtCore.Qt.ElideMiddle, column_width - 12)
            painter.drawText(x, y, text)
            x += column_width
        y += 29
    footer()
    painter.end()
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"Review Report PDF was not written: {target}")
    return target
