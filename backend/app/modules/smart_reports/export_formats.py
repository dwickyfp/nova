"""Package the shared renderer snapshots and exact governed table values."""

import base64
import io
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

from app.modules.agents import exporters


def _zip(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def workbook(snapshot: dict) -> bytes:
    tables = [
        (
            "Evidence",
            ["Report", "Revision", "As of", "Timezone"],
            [
                [
                    snapshot["spec"]["title"],
                    snapshot["pages"][0]["state"]["spec_revision"],
                    snapshot["pages"][0]["result"]["as_of"],
                    snapshot["spec"]["timezone"],
                ]
            ],
        )
    ]
    definitions = {
        w["id"]: w for p in snapshot["spec"]["pages"] for s in p["sections"] for w in s["widgets"]
    }
    for page in snapshot["pages"]:
        for result in page["result"]["widgets"]:
            rows = list(result["rows"])
            if result["totals"]:
                rows.append([result["totals"].get(column) for column in result["columns"]])
            tables.append(
                (
                    definitions[result["widget_id"]]["title"],
                    [result.get("column_labels", {}).get(c, c) for c in result["columns"]],
                    rows,
                )
            )
    files: dict[str, bytes | str] = {}
    sheets, relations, overrides = [], [], []
    for index, (title, columns, rows) in enumerate(tables, 1):
        # The legacy helper has an explicit 5,000-row cap; split without losing totals.
        if len(rows) > exporters.MAX_EXPORT_ROWS:
            raise ValueError("Export table exceeds 5,000 rows including its totals")
        archive = zipfile.ZipFile(io.BytesIO(exporters.to_xlsx(columns, rows)))
        if index == 1:
            files = {name: archive.read(name) for name in archive.namelist()}
        files[f"xl/worksheets/sheet{index}.xml"] = archive.read("xl/worksheets/sheet1.xml")
        safe_title = re.sub(r"[\\/*?:\[\]]", "-", title)
        name = f"{index} {safe_title}"[:31]
        sheets.append(f'<sheet name={quoteattr(name)} sheetId="{index}" r:id="rId{index}"/>')
        relations.append(
            f'<Relationship Id="rId{index}" Type="{exporters._REL}worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    files["xl/workbook.xml"] = (
        f"{exporters._XML}<workbook "
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'xmlns:r="{exporters._REL[:-1]}"><sheets>{"".join(sheets)}</sheets></workbook>'
    )
    files["xl/_rels/workbook.xml.rels"] = (
        f'{exporters._XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(relations)}</Relationships>'
    )
    content = files["[Content_Types].xml"]
    assert isinstance(content, bytes)
    files["[Content_Types].xml"] = content.decode().replace(
        "</Types>", "".join(overrides[1:]) + "</Types>"
    )
    return _zip(files)


def slides(rendered: dict) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(exporters.to_pptx([], [], title="Nova report")))
    files: dict[str, bytes | str] = {name: source.read(name) for name in source.namelist()}
    ids, relations, overrides = [], [], []
    for index, image in enumerate(rendered["images"], 1):
        png = base64.b64decode(image["png"], validate=True)
        files[f"ppt/media/image{index}.png"] = png
        scale = min(
            exporters._SLIDE_WIDTH / image["width"],
            exporters._SLIDE_HEIGHT / image["height"],
        )
        width, height = round(image["width"] * scale), round(image["height"] * scale)
        left = (exporters._SLIDE_WIDTH - width) // 2
        title = escape(f"Report page {image['page'] + 1}")
        files[f"ppt/slides/slide{index}.xml"] = (
            f"{exporters._XML}<p:sld {exporters._NS}><p:cSld><p:spTree>"
            f'{exporters._EMPTY_TREE}<p:pic><p:nvPicPr><p:cNvPr id="2" name="{title}"/>'
            '<p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/>'
            "<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm>"
            f'<a:off x="{left}" y="0"/><a:ext cx="{width}" cy="{height}"/>'
            '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
            "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
        )
        files[f"ppt/slides/_rels/slide{index}.xml.rels"] = (
            f"{exporters._XML}<Relationships "
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{exporters._REL}slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/>'
            f'<Relationship Id="rId2" Type="{exporters._REL}image" '
            f'Target="../media/image{index}.png"/></Relationships>'
        )
        ids.append(f'<p:sldId id="{255 + index}" r:id="rId{index + 2}"/>')
        relations.append(
            f'<Relationship Id="rId{index + 2}" Type="{exporters._REL}slide" '
            f'Target="slides/slide{index}.xml"/>'
        )
        overrides.append(
            f'<Override PartName="/ppt/slides/slide{index}.xml" '
            f'ContentType="{exporters._PML}slide+xml"/>'
        )
    for name, pattern, replacement in [
        (
            "ppt/presentation.xml",
            r"<p:sldIdLst>.*?</p:sldIdLst>",
            f"<p:sldIdLst>{''.join(ids)}</p:sldIdLst>",
        ),
        ("ppt/_rels/presentation.xml.rels", r'<Relationship Id="rId3"[^>]*/>', "".join(relations)),
        (
            "[Content_Types].xml",
            r'<Override PartName="/ppt/slides/slide1.xml"[^>]*/>',
            '<Default Extension="png" ContentType="image/png"/>' + "".join(overrides),
        ),
    ]:
        content = files[name]
        assert isinstance(content, bytes)
        files[name] = re.sub(pattern, replacement, content.decode(), flags=re.DOTALL)
    return _zip(files)
