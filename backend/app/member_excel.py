"""Bounded, formula-free XLSX member import and a blank text-formatted template."""

from decimal import Decimal, InvalidOperation
from io import BytesIO
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from app.loan_export import NS, _cell, _styles, _xml

MAX_UPLOAD = 2 * 1024 * 1024
MAX_EXPANDED = 16 * 1024 * 1024
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
TAG = "{" + NS + "}"


def _parse(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper() or b"\x00" in data:
        raise ValueError("Excel 含不支持的 XML 声明，请另存为标准 .xlsx 文件。")
    return ET.fromstring(data)


def parse_members(content):
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError("Excel 文件不能为空且不能超过 2 MiB。")
    try:
        return _read_workbook(content)
    except (BadZipFile, ET.ParseError, KeyError, IndexError, InvalidOperation, OSError, RuntimeError, OverflowError, NotImplementedError):
        raise ValueError("无法读取 Excel，请使用模板另存为标准 .xlsx 文件。") from None


def _read_workbook(content):
    with ZipFile(BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > 100 or sum(info.file_size for info in entries) > MAX_EXPANDED:
            raise ValueError("Excel 解压后内容过大，请仅保留成员数据。")
        names = [info.filename for info in entries]
        if len(names) != len(set(names)) or any(name.startswith("/") or ".." in name.split("/") or "\\" in name for name in names):
            raise ValueError("Excel 文件结构不正确。")
        if any(info.flag_bits & 1 for info in entries) or any(name.lower().endswith(".bin") or "externallinks/" in name.lower() for name in names):
            raise ValueError("不支持加密文件、宏或外部链接，请使用普通 .xlsx 文件。")
        trees = {}
        for name in names:
            if name.endswith((".xml", ".rels")):
                root = _parse(archive.read(name))
                trees[name] = root
                if any(node.attrib.get("TargetMode", "").lower() == "external" for node in root.iter()):
                    raise ValueError("Excel 含外部链接，请移除后再导入。")
                if any(node.tag == TAG + "f" for node in root.iter()):
                    raise ValueError("Excel 含公式，请将公式转换成值后再导入。")
                if name == "[Content_Types].xml" and any("macroenabled" in n.attrib.get("ContentType", "").lower() for n in root):
                    raise ValueError("不支持包含宏的 Excel。")
        book = trees["xl/workbook.xml"]
        sheets = book.findall(TAG + "sheets/" + TAG + "sheet")
        if not sheets:
            raise ValueError("Excel 中没有工作表。")
        selected = next((sheet for sheet in sheets if sheet.get("name") == "成员"), sheets[0])
        relation_id = selected.attrib["{" + REL + "}id"]
        relations = trees["xl/_rels/workbook.xml.rels"]
        relation = next((r for r in relations if r.get("Id") == relation_id), None)
        if relation is None or not relation.get("Type", "").endswith("/worksheet"):
            raise ValueError("成员工作表无法读取。")
        target = relation.attrib["Target"]
        path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
        if not path.startswith("xl/"):
            raise ValueError("成员工作表路径不正确。")
        sheet = trees[path]
        shared = []
        if "xl/sharedStrings.xml" in trees:
            shared = ["".join(t.text or "" for t in node.findall(".//" + TAG + "t")) for node in trees["xl/sharedStrings.xml"]]
        formats, styles = {}, []
        if "xl/styles.xml" in trees:
            root = trees["xl/styles.xml"]
            formats = {n.attrib["numFmtId"]: n.attrib["formatCode"] for n in root.findall(TAG + "numFmts/" + TAG + "numFmt")}
            styles = [n.get("numFmtId", "0") for n in root.findall(TAG + "cellXfs/" + TAG + "xf")]

        def value(cell, row_number):
            kind = cell.get("t", "n")
            if kind == "inlineStr":
                return "".join(node.text or "" for node in cell.findall(".//" + TAG + "t"))
            raw = cell.findtext(TAG + "v", "")
            if kind == "s":
                index = int(raw)
                if index < 0:
                    raise ValueError(f"第 {row_number} 行：文本索引不正确。")
                return shared[index]
            if kind == "str":
                return raw
            if not raw:
                return ""
            if kind != "n":
                raise ValueError(f"第 {row_number} 行：请使用文本填写姓名、学号及宿舍号。")
            number = Decimal(raw)
            if not number.is_finite() or number != number.to_integral_value() or abs(number) >= 10 ** 15 or number < 0:
                raise ValueError(f"第 {row_number} 行：数字不是有效的整数编号或可能丢失精度，请改用文本。")
            result = str(int(number))
            style = int(cell.get("s", "0"))
            if style < 0:
                raise ValueError(f"第 {row_number} 行：单元格格式不正确。")
            format_id = styles[style] if styles else "0"
            pattern = formats.get(format_id, "")
            if re.fullmatch(r"0{1,128}", pattern):
                return result.zfill(len(pattern))
            if format_id not in {"0", "1", "49"} and pattern not in {"General", "@", "0"}:
                raise ValueError(f"第 {row_number} 行：编号格式无法可靠识别，请改为文本后导入。")
            return result

        rows = sheet.findall(TAG + "sheetData/" + TAG + "row")
        headers, output, seen_rows = None, [], set()
        previous = 0
        for row in rows:
            number = int(row.get("r", str(previous + 1)))
            previous = number
            if number < 1 or number in seen_rows:
                raise ValueError("Excel 中存在重复或无效的行号。")
            seen_rows.add(number)
            cells = {}
            next_column = 0
            for cell in row.findall(TAG + "c"):
                ref = cell.get("r", "")
                if ref:
                    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", ref)
                    if match is None or int(match[2]) != number:
                        raise ValueError(f"第 {number} 行：单元格坐标不正确。")
                    column = 0
                    for letter in match[1]:
                        column = column * 26 + ord(letter) - 64
                    column -= 1
                else:
                    column = next_column
                next_column = column + 1
                if column in cells:
                    raise ValueError(f"第 {number} 行：单元格重复。")
                cells[column] = value(cell, number)
            if not any(text.strip() for text in cells.values()):
                continue
            if headers is None:
                labels = {"姓名": "name", "学号": "school_code", "宿舍号": "room_number", "房号": "room_number"}
                headers = {}
                for column, text in cells.items():
                    field = labels.get(text.strip())
                    if field:
                        if field in headers:
                            raise ValueError(f"第 {number} 行：表头重复。")
                        headers[field] = column
                if set(headers) != {"name", "school_code", "room_number"}:
                    raise ValueError(f"第 {number} 行：首个非空行须包含姓名、学号、宿舍号三个表头。")
                continue
            values = tuple(cells.get(headers[field], "") for field in ("name", "school_code", "room_number"))
            if not any(v.strip() for v in values):
                raise ValueError(f"第 {number} 行：成员信息为空，但其他列存在内容，请检查。")
            output.append((number, values))
            if len(output) > 5000:
                raise ValueError("每次最多导入 5000 名成员，请拆分文件。")
        if not output:
            raise ValueError("Excel 中没有成员，请在表头下填写成员信息。")
        return output


def template_workbook():
    sheet = ET.Element("worksheet", xmlns=NS)
    view = ET.SubElement(ET.SubElement(sheet, "sheetViews"), "sheetView", workbookViewId="0", showGridLines="0")
    ET.SubElement(view, "pane", ySplit="1", topLeftCell="A2", activePane="bottomLeft", state="frozen")
    ET.SubElement(sheet, "sheetFormatPr", defaultRowHeight="26")
    cols = ET.SubElement(sheet, "cols")
    for i, width in enumerate((32, 24, 24), 1):
        ET.SubElement(cols, "col", min=str(i), max=str(i), width=str(width), customWidth="1", style="4")
    data = ET.SubElement(sheet, "sheetData")
    row = ET.SubElement(data, "row", r="1", ht="32", customHeight="1")
    for i, text in enumerate(("姓名", "学号", "宿舍号")):
        _cell(row, f"{chr(65+i)}1", text, 3)
    # Blank formatted cells, never example people that might be accidentally imported.
    row = ET.SubElement(data, "row", r="2")
    for letter in "ABC":
        _cell(row, letter + "2", "", 4)
    notes = ET.Element("worksheet", xmlns=NS)
    ET.SubElement(ET.SubElement(notes, "cols"), "col", min="1", max="1", width="100", customWidth="1")
    data = ET.SubElement(notes, "sheetData")
    for i, text in enumerate(("正雅苑成员导入说明", "在“成员”工作表填写姓名、学号、宿舍号；请保留表头。", "学号与宿舍号使用文本格式，保留前导 0；请勿使用公式。", "外籍成员填写完整大写姓名，包含姓名中的空格。", "每次最多 5000 人、文件不超过 2 MiB；同一文件不能有重复学号。", "导入按学号新增或更新，不删除未出现在本次文件中的原成员。"), 1):
        row = ET.SubElement(data, "row", r=str(i), ht="32", customHeight="1")
        _cell(row, "A" + str(i), text, 3 if i == 1 else 4)
    book = ET.Element("workbook", xmlns=NS, **{"xmlns:r": REL})
    sheets = ET.SubElement(book, "sheets")
    for i, name in enumerate(("成员", "说明"), 1):
        ET.SubElement(sheets, "sheet", name=name, sheetId=str(i), **{"r:id": "rId" + str(i)})
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in (1, 2)) + '</Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="' + REL + '/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in (1, 2)) + f'<Relationship Id="rId3" Type="{REL}/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", _xml(book))
        archive.writestr("xl/worksheets/sheet1.xml", _xml(sheet))
        archive.writestr("xl/worksheets/sheet2.xml", _xml(notes))
        archive.writestr("xl/styles.xml", _styles())
    return output.getvalue()
