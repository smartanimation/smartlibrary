from smartlib.review.report import report_inputs, render_review_report_pdf


def test_inventory_retains_excluded_sources_and_fixed_assets():
    source={"components":[{"component_type":"rig", "name":"hero", "path":"anim/v002/rig.ma"}]}
    composition={"members":[{"instance_id":"hero", "products":{"rend":{"path":"rend/v003/rig.ma", "dependencies":[{"path":"asset/v004/rig.mb", "sha256":"abc"}]}}}]}
    rows=report_inputs({"components":[{"component_type":"placement", "name":"props", "enabled":False}]}, {}, composition, source)
    assert rows[0]["state"] == "EXCLUDED"
    assert next(r for r in rows if r["type"]=="rig")["role"]=="SOURCE ONLY"
    asset=next(r for r in rows if r["type"]=="asset")
    assert asset["sha256"]=="abc" and asset["version"]=="v004"


def test_pdf_retains_all_rows_across_pages(tmp_path):
    from PySide6 import QtGui, QtPdf
    app=QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
    QtGui.QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
    thumbnail=QtGui.QImage(64,64,QtGui.QImage.Format_RGB32)
    thumbnail.fill(QtGui.QColor("gray"));thumbnail.save(str(tmp_path/"thumb.png"))
    target=render_review_report_pdf(report_path=tmp_path/"report.pdf", thumbnail_path=tmp_path/"thumb.png",
        data={"inputs":[{"type":"rig", "name":f"member_{i:03d}", "enabled":i!=34, "path":"D:/fixed/v001/rig.ma", "sha256":"private_digest"} for i in range(35)]})
    pdf=QtPdf.QPdfDocument();pdf.load(str(target))
    assert pdf.pageCount()>1
    text=" ".join(pdf.getAllText(i).text() for i in range(pdf.pageCount()))
    assert all(f"member_{i:03d}" in text for i in range(34))
    assert "member_034" not in text
    assert "D:/fixed" not in text and "private_digest" not in text



def test_summary_hides_dependencies_but_keeps_main_inputs():
    from smartlib.review.report import summary_inputs
    rows=[{"type":"animation", "name":"hero", "version":"rend v001"},
          {"type":"asset", "name":"texture"}, {"type":"rig", "name":"hero", "role":"SOURCE ONLY"},
          {"type":"camera", "name":"cam"}, {"type":"placement", "enabled":False}]
    assert [r["type"] for r in summary_inputs(rows)] == ["animation", "camera"]
