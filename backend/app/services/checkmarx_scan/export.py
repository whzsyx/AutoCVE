from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


HEADERS = ["scan_id", "path_id", "Vulnerability", "Type", "URL", "AI判断", "AI判断原因"]


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell_xml(row_index: int, column_index: int, value: Any) -> str:
    cell_ref = f"{_column_name(column_index)}{row_index}"
    text = "" if value is None else str(value)
    return f'<c r="{cell_ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def _worksheet_xml(rows: list[list[Any]]) -> str:
    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(_cell_xml(row_index, column_index, value) for column_index, value in enumerate(row, start=1))
        row_xml.append(f'<row r="{row_index}">{cells}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        f'{"".join(row_xml)}'
        "</sheetData>"
        "</worksheet>"
    )


def _format_ai_judgement(value: Any) -> str:
    if value is True:
        return "True"
    if value is False:
        return "False"
    return "未知"


def build_results_workbook(results: list[dict[str, Any]]) -> bytes:
    rows: list[list[Any]] = [HEADERS]
    for item in results:
        rows.append(
            [
                item.get("scan_id", ""),
                item.get("path_id", ""),
                item.get("vulnerability", ""),
                item.get("type", ""),
                item.get("url", ""),
                _format_ai_judgement(item.get("ai_judgement")),
                item.get("ai_reason", ""),
            ]
        )

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets><sheet name=\"results\" sheetId=\"1\" r:id=\"rId1\"/></sheets>"
            "</workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", _worksheet_xml(rows))
    return buffer.getvalue()

