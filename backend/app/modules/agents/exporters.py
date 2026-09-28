"""Export a Studio result as CSV, XLSX, PDF, or PPTX, from rows re-run for the caller.

The exporter never reads stored rows: the caller re-runs the artifact's
statement under their own access and passes the redacted result here. Only the
standard library and matplotlib (already a dependency) are used; XLSX and PPTX
are written as minimal Office Open XML packages.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

MAX_EXPORT_ROWS = 5000
MAX_PDF_ROWS = 200
MAX_PPTX_ROWS = 60
PPTX_ROWS_PER_SLIDE = 12
PPTX_COLUMNS = 8

MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _safe_formula(text: str) -> str:
    # A leading = + - @ turns a cell into a formula in spreadsheet apps.
    return "'" + text if text[:1] in {"=", "+", "-", "@"} and not _is_number(text) else text


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
    except Exception:  # noqa: BLE001 - any parse failure means "not a number"
        return False
    return True


def to_csv(columns: list[str], rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_safe_formula(str(column)) for column in columns])
    for row in rows[:MAX_EXPORT_ROWS]:
        writer.writerow([_safe_formula(_cell_text(value)) for value in row])
    return buffer.getvalue().encode("utf-8-sig")


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def to_xlsx(columns: list[str], rows: list[list[Any]], *, title: str = "Result") -> bytes:
    def cell(ref: str, value: Any) -> str:
        if isinstance(value, bool) or value is None:
            return f'<c r="{ref}" t="inlineStr"><is><t>{escape(_cell_text(value))}</t></is></c>'
        if isinstance(value, int | float | Decimal):
            return f'<c r="{ref}"><v>{value}</v></c>'
        text = _cell_text(value)
        return (f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                f"{escape(text)}</t></is></c>")

    body = []
    for row_index, values in enumerate([columns, *rows[:MAX_EXPORT_ROWS]], start=1):
        cells = "".join(
            cell(f"{_column_letter(col)}{row_index}", value)
            for col, value in enumerate(values)
        )
        body.append(f'<row r="{row_index}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    name = escape((title or "Result")[:31].replace("/", "-"))
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def to_pdf(columns: list[str], rows: list[list[Any]], *, title: str = "Result") -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot
    from matplotlib.backends.backend_pdf import PdfPages

    shown = rows[:MAX_PDF_ROWS]
    per_page = 30
    buffer = io.BytesIO()
    with PdfPages(buffer) as pdf:
        for start in range(0, max(len(shown), 1), per_page):
            figure, axis = pyplot.subplots(figsize=(11.69, 8.27))  # A4 landscape
            axis.axis("off")
            page_title = title if start == 0 else f"{title} (cont.)"
            axis.set_title(page_title[:120], loc="left", fontsize=12)
            chunk = [[_cell_text(value)[:40] for value in row]
                     for row in shown[start:start + per_page]]
            if chunk:
                table = axis.table(cellText=chunk, colLabels=[str(c)[:30] for c in columns],
                                   loc="upper left", cellLoc="left")
                table.auto_set_font_size(False)
                table.set_fontsize(7)
            if len(rows) > MAX_PDF_ROWS and start + per_page >= len(shown):
                axis.text(0, 0.02, f"Showing {MAX_PDF_ROWS} of {len(rows)} rows. "
                          "Export CSV or XLSX for all rows.", fontsize=8,
                          transform=axis.transAxes)
            pdf.savefig(figure)
            pyplot.close(figure)
    return buffer.getvalue()


_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_PML = "application/vnd.openxmlformats-officedocument.presentationml."
_SLIDE_WIDTH, _SLIDE_HEIGHT, _MARGIN = 12192000, 6858000, 457200
_EMPTY_TREE = (
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    "<p:grpSpPr/>"
)
_THEME = (
    _XML + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'name="Nova"><a:themeElements><a:clrScheme name="Nova">'
    '<a:dk1><a:srgbClr val="1F2328"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
    + "".join(
        f'<a:accent{n}><a:srgbClr val="{color}"/></a:accent{n}>'
        for n, color in enumerate(
            ("2563EB", "0F766E", "B45309", "7C3AED", "BE123C", "4B5563"), start=1
        )
    )
    + '<a:hlink><a:srgbClr val="2563EB"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="7C3AED"/></a:folHlink></a:clrScheme>'
    '<a:fontScheme name="Nova">'
    '<a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>'
    "</a:majorFont>"
    '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>'
    "</a:minorFont></a:fontScheme>"
    '<a:fmtScheme name="Nova"><a:fillStyleLst>'
    + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
    + "</a:fillStyleLst><a:lnStyleLst>"
    + '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' * 3
    + "</a:lnStyleLst><a:effectStyleLst>"
    + "<a:effectStyle><a:effectLst/></a:effectStyle>" * 3
    + "</a:effectStyleLst><a:bgFillStyleLst>"
    + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
    + "</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>"
)


def _run(text: str, *, size: int, bold: bool = False) -> str:
    weight = ' b="1"' if bold else ""
    return (
        f'<a:p><a:r><a:rPr lang="en-US" sz="{size}"{weight} dirty="0"/>'
        f'<a:t xml:space="preserve">{escape(text)}</a:t></a:r></a:p>'
    )


def _pptx_slide(title: str, columns: list[str], rows: list[list[str]], note: str) -> str:
    width = _SLIDE_WIDTH - 2 * _MARGIN
    column_width = width // max(len(columns), 1)
    row_height = 370840

    def cell(text: str, *, header: bool) -> str:
        return (
            f"<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{_run(text, size=1100, bold=header)}"
            "</a:txBody><a:tcPr/></a:tc>"
        )

    table_rows = "".join(
        f'<a:tr h="{row_height}">' + "".join(cell(value, header=index == 0) for value in row)
        + "</a:tr>"
        for index, row in enumerate([columns, *rows])
    )
    shapes = [
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr txBox="1"/><p:nvPr/>'
        f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="{_MARGIN}" y="{_MARGIN}"/>'
        f'<a:ext cx="{width}" cy="731520"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
        f"</a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>"
        f"{_run(title, size=2400, bold=True)}</p:txBody></p:sp>",
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="3" name="Result"/>'
        '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr>'
        f'<p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="{_MARGIN}" y="1371600"/>'
        f'<a:ext cx="{column_width * max(len(columns), 1)}" '
        f'cy="{row_height * (len(rows) + 1)}"/></p:xfrm><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
        '<a:tbl><a:tblPr firstRow="1" bandRow="1"/><a:tblGrid>'
        + f'<a:gridCol w="{column_width}"/>' * max(len(columns), 1)
        + f"</a:tblGrid>{table_rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>",
    ]
    if note:
        shapes.append(
            '<p:sp><p:nvSpPr><p:cNvPr id="4" name="Note"/><p:cNvSpPr txBox="1"/><p:nvPr/>'
            f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="{_MARGIN}" y="{_SLIDE_HEIGHT - 822960}"/>'
            f'<a:ext cx="{width}" cy="365760"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
            f"</a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>"
            f"{_run(note, size=1000)}</p:txBody></p:sp>"
        )
    return (
        f"{_XML}<p:sld {_NS}><p:cSld><p:spTree>{_EMPTY_TREE}{''.join(shapes)}"
        "</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def to_pptx(columns: list[str], rows: list[list[Any]], *, title: str = "Result") -> bytes:
    """One table slide per 12 rows, up to 60 rows and 8 columns."""
    shown_columns = [str(column)[:40] for column in columns[:PPTX_COLUMNS]]
    shown = [
        [_cell_text(value)[:60] for value in row[:PPTX_COLUMNS]]
        for row in rows[:MAX_PPTX_ROWS]
    ]
    omitted = []
    if len(rows) > MAX_PPTX_ROWS:
        omitted.append(f"{MAX_PPTX_ROWS} of {len(rows)} rows")
    if len(columns) > PPTX_COLUMNS:
        omitted.append(f"{PPTX_COLUMNS} of {len(columns)} columns")
    note = ("Showing " + " and ".join(omitted) + ". Export XLSX for the full result."
            if omitted else "")
    chunks = [
        shown[start:start + PPTX_ROWS_PER_SLIDE]
        for start in range(0, max(len(shown), 1), PPTX_ROWS_PER_SLIDE)
    ]
    heading = (title or "Result")[:120]
    slides = [
        _pptx_slide(heading if index == 0 else f"{heading} (cont.)", shown_columns, chunk,
                    note if index == len(chunks) - 1 else "")
        for index, chunk in enumerate(chunks)
    ]
    layout_rel = (
        f'{_XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        f'relationships"><Relationship Id="rId1" Type="{_REL}slideLayout" '
        'Target="../slideLayouts/slideLayout1.xml"/></Relationships>'
    )
    files = {
        "[Content_Types].xml": (
            f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" ContentType="application/'
            'vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/ppt/presentation.xml" '
            f'ContentType="{_PML}presentation.main+xml"/>'
            f'<Override PartName="/ppt/slideMasters/slideMaster1.xml" '
            f'ContentType="{_PML}slideMaster+xml"/>'
            f'<Override PartName="/ppt/slideLayouts/slideLayout1.xml" '
            f'ContentType="{_PML}slideLayout+xml"/>'
            '<Override PartName="/ppt/theme/theme1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            + "".join(
                f'<Override PartName="/ppt/slides/slide{n}.xml" '
                f'ContentType="{_PML}slide+xml"/>'
                for n in range(1, len(slides) + 1)
            )
            + "</Types>"
        ),
        "_rels/.rels": (
            f'{_XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{_REL}officeDocument" '
            'Target="ppt/presentation.xml"/></Relationships>'
        ),
        "ppt/presentation.xml": (
            f'{_XML}<p:presentation {_NS}><p:sldMasterIdLst>'
            '<p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>'
            + "".join(
                f'<p:sldId id="{255 + n}" r:id="rId{n + 2}"/>'
                for n in range(1, len(slides) + 1)
            )
            + f'</p:sldIdLst><p:sldSz cx="{_SLIDE_WIDTH}" cy="{_SLIDE_HEIGHT}"/>'
            '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
        ),
        "ppt/_rels/presentation.xml.rels": (
            f'{_XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{_REL}slideMaster" '
            'Target="slideMasters/slideMaster1.xml"/>'
            f'<Relationship Id="rId2" Type="{_REL}theme" Target="theme/theme1.xml"/>'
            + "".join(
                f'<Relationship Id="rId{n + 2}" Type="{_REL}slide" Target="slides/slide{n}.xml"/>'
                for n in range(1, len(slides) + 1)
            )
            + "</Relationships>"
        ),
        "ppt/slideMasters/slideMaster1.xml": (
            f"{_XML}<p:sldMaster {_NS}><p:cSld><p:bg><p:bgRef idx=\"1001\">"
            '<a:schemeClr val="bg1"/></p:bgRef></p:bg>'
            f"<p:spTree>{_EMPTY_TREE}</p:spTree></p:cSld>"
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
            'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
            'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
            "</p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/>"
            "</p:txStyles></p:sldMaster>"
        ),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": (
            f'{_XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{_REL}slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/>'
            f'<Relationship Id="rId2" Type="{_REL}theme" Target="../theme/theme1.xml"/>'
            "</Relationships>"
        ),
        "ppt/slideLayouts/slideLayout1.xml": (
            f'{_XML}<p:sldLayout {_NS} preserve="1"><p:cSld name="Blank">'
            f"<p:spTree>{_EMPTY_TREE}</p:spTree></p:cSld>"
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
        ),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": (
            f'{_XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{_REL}slideMaster" '
            'Target="../slideMasters/slideMaster1.xml"/></Relationships>'
        ),
        "ppt/theme/theme1.xml": _THEME,
    }
    for number, slide in enumerate(slides, start=1):
        files[f"ppt/slides/slide{number}.xml"] = slide
        files[f"ppt/slides/_rels/slide{number}.xml.rels"] = layout_rel
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def export(fmt: str, columns: list[str], rows: list[list[Any]], *, title: str) -> bytes:
    if fmt == "csv":
        return to_csv(columns, rows)
    if fmt == "xlsx":
        return to_xlsx(columns, rows, title=title)
    if fmt == "pdf":
        return to_pdf(columns, rows, title=title)
    if fmt == "pptx":
        return to_pptx(columns, rows, title=title)
    raise ValueError(f"Unsupported export format {fmt!r}.")
