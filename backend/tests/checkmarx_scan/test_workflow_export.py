from io import BytesIO
from zipfile import ZipFile

from app.services.checkmarx_scan.export import build_results_workbook
from app.services.checkmarx_scan.workflow import parse_workflow_verdict


def test_parse_workflow_verdict_extracts_boolean_and_reason():
    text = (
        "**真实漏洞**\n"
        "模板占位：否\n"
        "**解释**\n"
        "模板占位解释\n"
        "**真实漏洞**\n"
        "否\n"
        "**解释**\n"
        "Checkmarx 命中的参数没有进入危险函数，属于误报。"
    )

    verdict = parse_workflow_verdict(text)

    assert verdict.real_vuln is False
    assert verdict.reason == "Checkmarx 命中的参数没有进入危险函数，属于误报。"


def test_build_results_workbook_writes_ai_verdict_and_reason():
    workbook_bytes = build_results_workbook(
        [
            {
                "scan_id": "123",
                "path_id": "456",
                "vulnerability": "SQL Injection",
                "type": "High",
                "url": "https://checkmarx.local/cxwebclient/ViewerMain.aspx?scanid=123&pathid=456",
                "ai_judgement": False,
                "ai_reason": "参数受白名单约束，无法控制 SQL 片段。",
                "request_skipped": True,
            }
        ]
    )

    with ZipFile(BytesIO(workbook_bytes)) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

    for header in ("scan_id", "path_id", "Vulnerability", "Type", "URL", "AI判断", "AI判断原因"):
        assert header in sheet_xml
    assert "<c r=\"F2\" t=\"inlineStr\"><is><t>False</t></is></c>" in sheet_xml
    assert "参数受白名单约束，无法控制 SQL 片段。" in sheet_xml
    assert "request_skipped" not in sheet_xml
