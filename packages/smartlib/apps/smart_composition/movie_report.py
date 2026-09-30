"""Single-page review summary using the USD runtime's Qt PDF support."""
from html import escape
from pathlib import PurePosixPath
import re
from pxr.Usdviewq.qt import QtCore, QtGui


def text(value):
    return escape(str(value)).replace('_', '_&#8203;')


def filename(value):
    return PurePosixPath(str(value).replace('\\', '/')).name


def version(value):
    parts = PurePosixPath(str(value).replace('\\', '/')).parts
    return next((p for p in reversed(parts) if re.fullmatch(r'v\d+', p)), '-')


def create_report(path, job, thumbnail, focal_mm):
    writer = QtGui.QPdfWriter(str(path))
    writer.setTitle('USD Composition Review')
    writer.setCreator('Smart Composition')
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    writer.setPageMargins(QtCore.QMarginsF(16, 16, 16, 16))
    writer.setResolution(72)
    doc = QtGui.QTextDocument()
    doc.setDefaultFont(QtGui.QFont('Arial', 9))
    doc.setTextWidth(writer.width())
    doc.addResource(QtGui.QTextDocument.ImageResource, QtCore.QUrl('preview'), QtGui.QImage(str(thumbnail)))
    shot = job['snapshot']['shot']
    title = ' / '.join(shot[k] for k in ('episode', 'sequence', 'shot'))
    w, h = job['resolution']
    image_width = min(440, int(210 * w / h))
    body = [f'<h1>USD Composition Review</h1><h2>{text(title)}</h2>',
        f'<p>Working review / Not approved &nbsp; | &nbsp; {text(job["created_at"][:10])}<br>'
        f'Frames: {job["frame_range"][0]} - {job["frame_range"][1]} &nbsp; | &nbsp; '
        f'{job["fps"]:g} fps &nbsp; | &nbsp; {w} x {h}<br>'
        f'Camera: {text(filename(job["camera"]))} &nbsp; | &nbsp; {focal_mm:g} mm (first frame)</p>',
        f'<p align="center"><img src="preview" width="{image_width}" height="{int(image_width*h/w)}"></p>',
        f'<p><b>Composition:</b> {text(filename(job["snapshot"]["entrypoint"]["path"]))} '
        f'&nbsp; {text(job["snapshot"]["version"])}<br>'
        f'<b>Movie:</b> {text(filename(job["files"]["movie"]))}</p>',
        '<h3>Composition layers</h3>',
        '<table width="100%" cellspacing="0" cellpadding="5" border="0">'
        '<tr bgcolor="#dce9ef"><th align="left">Layer / Target</th><th align="left">USD file</th>'
        '<th>Product</th><th>USD version</th></tr>']
    for i, product in enumerate(job['products']):
        data = product['data']
        usd = data['entrypoint']['path']
        color = '#f0f4f6' if i % 2 == 0 else '#ffffff'
        body.append(f'<tr bgcolor="{color}"><td>{text(data["kind"].title())} / {text(data["target"])}</td>'
            f'<td>{text(filename(usd))}</td><td align="center">{text(data["version"])}</td>'
            f'<td align="center">{text(version(usd))}</td></tr>')
    body.append('</table>')
    sections = job['snapshot'].get('sections', {})
    if sections:
        body.append('<p><b>Sections:</b> ' + ' &nbsp; | &nbsp; '.join(
            f'{text(kind.title())} {text(version(ref["path"]))}' for kind, ref in sections.items()) + '</p>')
    dependencies = sorted({(filename(ref['path']), version(ref['path']))
                           for ref in job['snapshot'].get('dependencies', [])})
    if dependencies:
        body.append('<h3>Referenced USD files</h3><p>' + '<br>'.join(
            f'{text(name)} &nbsp; {text(ver)}' for name, ver in dependencies) + '</p>')
    body.append('<p style="color:#666666;font-size:8pt">Product = Shot Product version. USD version = referenced file version.<br>'
        'Full paths and verification hashes are recorded in the accompanying JSON.</p>')
    doc.setHtml('<html><body style="color:#202b33"><style>h1{color:#184d65;font-size:20pt;margin-bottom:4px}'
        'h2{color:#24627b;font-size:13pt;margin-top:0} h3{color:#24627b;font-size:10pt}'
        'p{line-height:115%}</style>' + ''.join(body) + '</body></html>')
    # Draw once onto one A4 page. Fit unusually long asset lists without clipping.
    scale = min(1.0, writer.height() / doc.size().height())
    painter = QtGui.QPainter(writer)
    try:
        painter.scale(scale, scale)
        doc.drawContents(painter)
    finally:
        painter.end()
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError('PDF report was not generated')
