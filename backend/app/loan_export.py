"""Dependency-free XLSX export for the administrator's filtered loan records."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import math
import unicodedata
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZIP_DEFLATED, ZipFile

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
BEIJING = timezone(timedelta(hours=8))
HEADER_ROW = 6
COLUMN_WIDTHS = [25, 22, 22, 24, 9, 24, 24, 12, 12]


def _xml(value):
    return tostring(value, encoding="utf-8", xml_declaration=True)


def _text(value):
    return "".join(c for c in str(value) if c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)


def _row_height(values, widths, minimum):
    """Reserve wrapped lines for long names without enlarging ordinary rows."""
    lines = 1
    for value, width in zip(values, widths):
        wrapped = 0
        for line in str(value).split("\n"):
            units = sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in line)
            wrapped += max(1, math.ceil(units / max(1, width - 3)))
        lines = max(lines, wrapped)
    return min(409, max(minimum, lines * 16 + 10))


def _cell(row, ref, value, style):
    numeric = isinstance(value, (int, float))
    cell = SubElement(row, "c", r=ref, t="n" if numeric else "inlineStr", s=str(style))
    if numeric:
        SubElement(cell, "v").text = str(value)
    else:
        SubElement(SubElement(cell, "is"), "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}).text = _text(value)


def _date(value):
    if not value:
        return ""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(BEIJING).replace(tzinfo=None)
    # The Excel 1900 date system includes its historic fictional leap day.
    return (local - datetime(1899, 12, 30)).total_seconds() / 86400


def _styles():
    root = Element("styleSheet", xmlns=NS)
    formats = SubElement(root, "numFmts", count="1")
    SubElement(formats, "numFmt", numFmtId="164", formatCode="yyyy-mm-dd hh:mm:ss")
    fonts = SubElement(root, "fonts", count="5")
    for size, color, bold in [(11, "244956", False), (19, "FFFFFF", True), (10, "5E7983", False), (11, "FFFFFF", True), (11, "007D87", True)]:
        font = SubElement(fonts, "font")
        SubElement(font, "sz", val=str(size))
        SubElement(font, "color", rgb="FF" + color)
        SubElement(font, "name", val="Microsoft YaHei")
        if bold:
            SubElement(font, "b")
    fills = SubElement(root, "fills", count="7")
    for pattern, color in [("none", None), ("gray125", None), ("solid", "007F96"), ("solid", "EFF6F7"), ("solid", "FFF1D9"), ("solid", "E0F2EC"), ("solid", "FFFFFF")]:
        node = SubElement(SubElement(fills, "fill"), "patternFill", patternType=pattern)
        if color:
            SubElement(node, "fgColor", rgb="FF" + color)
            SubElement(node, "bgColor", indexed="64")
    borders = SubElement(root, "borders", count="2")
    for bordered in (False, True):
        border = SubElement(borders, "border")
        for side in ("left", "right", "top", "bottom", "diagonal"):
            edge = SubElement(border, side, **({"style": "hair"} if bordered and side == "bottom" else {}))
            if bordered and side == "bottom":
                SubElement(edge, "color", rgb="FFD3E2E7")
    base = SubElement(root, "cellStyleXfs", count="1")
    SubElement(base, "xf", numFmtId="0", fontId="0", fillId="0", borderId="0")
    xfs = SubElement(root, "cellXfs", count="12")
    # default, title, metadata, heading, plain/stripe text, plain/stripe numbers,
    # plain/stripe dates, active status, returned status.
    specs = [(0, 0, 0, 0, "left"), (1, 2, 0, 0, "left"), (2, 0, 0, 0, "left"), (3, 2, 0, 0, "left"), (0, 6, 1, 49, "left"), (0, 3, 1, 49, "left"), (0, 6, 1, 0, "center"), (0, 3, 1, 0, "center"), (0, 6, 1, 164, "center"), (0, 3, 1, 164, "center"), (0, 4, 1, 49, "center"), (4, 5, 1, 49, "center")]
    for font, fill, border, number, align in specs:
        xf = SubElement(xfs, "xf", fontId=str(font), fillId=str(fill), borderId=str(border), numFmtId=str(number), xfId="0", applyFont="1", applyFill="1", applyBorder="1", applyNumberFormat="1", applyAlignment="1")
        SubElement(xf, "alignment", horizontal=align, vertical="center", wrapText="1")
    styles = SubElement(root, "cellStyles", count="1")
    SubElement(styles, "cellStyle", name="Normal", xfId="0", builtinId="0")
    return _xml(root)


def workbook(loans, *, filters=None, cabinet_label="全部柜子", exported_at=None):
    """Export the already-filtered/sorted records, with their original query context."""
    filters = filters or {}
    exported_at = exported_at or datetime.now(timezone.utc)
    if exported_at.tzinfo is None:
        exported_at = exported_at.replace(tzinfo=timezone.utc)
    sort_labels = {"borrowed_at": "借用时间", "returned_at": "归还时间", "days": "借用天数", "user_name": "人员", "cabinet_name": "柜子", "tool_name": "物品"}
    status = {"all": "全部记录", "active": "借用中", "returned": "已归还"}.get(filters.get("status", "all"), "全部记录")
    sort = sort_labels.get(filters.get("sort", "borrowed_at"), "借用时间")
    direction = "升序" if filters.get("direction") == "asc" else "降序"
    sheet = Element("worksheet", xmlns=NS)
    properties = SubElement(sheet, "sheetPr")
    SubElement(properties, "tabColor", rgb="FF007F96")
    SubElement(properties, "pageSetUpPr", fitToPage="1")
    last_row = HEADER_ROW + len(loans)
    SubElement(sheet, "dimension", ref=f"A1:I{last_row}")
    view = SubElement(SubElement(sheet, "sheetViews"), "sheetView", workbookViewId="0", showGridLines="0")
    SubElement(view, "pane", ySplit=str(HEADER_ROW), topLeftCell=f"A{HEADER_ROW + 1}", activePane="bottomLeft", state="frozen")
    SubElement(view, "selection", pane="bottomLeft", activeCell=f"A{HEADER_ROW + 1}", sqref=f"A{HEADER_ROW + 1}")
    SubElement(sheet, "sheetFormatPr", defaultRowHeight="26")
    columns = SubElement(sheet, "cols")
    for index, width in enumerate(COLUMN_WIDTHS, 1):
        SubElement(columns, "col", min=str(index), max=str(index), width=str(width), customWidth="1")
    data = SubElement(sheet, "sheetData")
    meta = [
        (1, 38, [("A", "正雅苑 · 共享工具箱 | 借还记录", 1)]),
        (2, 27, [("A", f"导出时间：{exported_at.astimezone(BEIJING):%Y-%m-%d %H:%M:%S}（北京时间）    ·    共 {len(loans)} 条记录", 2)]),
        (3, 26, [("A", f'搜索：{filters.get("q", "").strip() or "不限"}' + (f'    |    学号：{filters["school_code"]}' if filters.get("school_code") else ''), 2)]),
        (4, 30, [("A", f"柜子：{cabinet_label}", 2), ("E", f"归还情况：{status}", 2)]),
        (5, 28, [("A", f"排序：{sort} · {direction}    |    下方日期均为北京时间（UTC+8）", 2)]),
    ]
    for number, height, values in meta:
        widths = [sum(COLUMN_WIDTHS[:4]), sum(COLUMN_WIDTHS[4:])] if number == 4 else [sum(COLUMN_WIDTHS)]
        height = _row_height([value for _, value, _ in values], widths, height)
        row = SubElement(data, "row", r=str(number), ht=str(height), customHeight="1")
        for column, value, style in values:
            _cell(row, f"{column}{number}", value, style)
    header = SubElement(data, "row", r=str(HEADER_ROW), ht="32", customHeight="1")
    for index, value in enumerate(["人员", "学校人员编号", "柜子", "物品", "数量", "借用时间", "归还时间", "借用天数", "状态"]):
        _cell(header, f"{chr(65 + index)}{HEADER_ROW}", value, 3)
    for number, loan in enumerate(loans, HEADER_ROW + 1):
        stripe = (number - HEADER_ROW - 1) % 2
        cabinet = loan["cabinet_name"]
        if cabinet != loan["cabinet_code"] + "区":
            cabinet += f'（{loan["cabinet_code"]}）'
        values = [loan["user_name"], str(loan["school_code"]), cabinet, loan["tool_name"], loan["quantity"], _date(loan["borrowed_at"]), _date(loan["returned_at"]), loan["days"], "已归还" if loan["returned_at"] else "借用中"]
        height = _row_height(values, COLUMN_WIDTHS, 36)
        row = SubElement(data, "row", r=str(number), ht=str(height), customHeight="1")
        styles = [4 + stripe] * 4 + [6 + stripe, 8 + stripe, 8 + stripe, 6 + stripe, 11 if loan["returned_at"] else 10]
        for index, (value, style) in enumerate(zip(values, styles)):
            _cell(row, f"{chr(65 + index)}{number}", value, style)
    SubElement(sheet, "autoFilter", ref=f"A{HEADER_ROW}:I{last_row}")
    merges = SubElement(sheet, "mergeCells", count="6")
    for ref in ("A1:I1", "A2:I2", "A3:I3", "A4:D4", "E4:I4", "A5:I5"):
        SubElement(merges, "mergeCell", ref=ref)
    SubElement(sheet, "printOptions", horizontalCentered="1")
    SubElement(sheet, "pageMargins", left="0.25", right="0.25", top="0.4", bottom="0.4", header="0.2", footer="0.2")
    SubElement(sheet, "pageSetup", paperSize="9", orientation="landscape", fitToWidth="1", fitToHeight="0")
    SubElement(SubElement(sheet, "headerFooter"), "oddFooter").text = "&C第 &P / &N 页"
    book = Element("workbook", xmlns=NS, **{"xmlns:r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"})
    SubElement(book, "workbookPr", date1904="0")
    SubElement(SubElement(book, "sheets"), "sheet", name="借还记录", sheetId="1", **{"r:id": "rId1"})
    names = SubElement(book, "definedNames")
    SubElement(names, "definedName", name="_xlnm.Print_Titles", localSheetId="0").text = f"'借还记录'!$1:${HEADER_ROW}"
    SubElement(names, "definedName", name="_xlnm.Print_Area", localSheetId="0").text = f"'借还记录'!$A$1:$I${last_row}"
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", _xml(book))
        archive.writestr("xl/worksheets/sheet1.xml", _xml(sheet))
        archive.writestr("xl/styles.xml", _styles())
    return output.getvalue()
