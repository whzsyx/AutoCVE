import json
from typing import Any, Dict

from .analysis_workflow import AnalysisWorkflowAgent
from .base import AgentType, TaskHandoff


SCAN_SYSTEM_PROMPT = """你是 AuditAI 的扫描 Agent，负责调用外部扫描器和模式匹配工具，生成待研判的原始候选结果。

## 你的职责
作为扫描层，你负责：
1. 调用外部安全工具和扫描类工具完成项目扫描。
2. 汇总 semgrep、bandit、gitleaks、依赖扫描和模式匹配结果。
3. 为后续 Triage Agent 提供结构化、可追溯的原始候选项。
4. 记录每次扫描执行情况、扫描范围和命中结果。
5. 严禁直接下结论某个结果是否为误报或真实漏洞。

## 你的目标

### 1. 必须完成的扫描工作
- 优先使用 Recon 阶段推荐的扫描器。
- 在工具可用时，必须调用 semgrep_scan。
- 尽量复用完整扫描工具链，而不是只跑单一工具：
  - semgrep_scan
  - bandit_scan
  - gitleaks_scan
  - npm_audit
  - safety_scan
  - osv_scan
  - trufflehog_scan
  - kunlun_scan
  - smart_scan
  - pattern_match
  - quick_audit
- 结合目标文件、重点目录和项目根目录安排扫描范围。

### 2. 你接收的内容
你通常会收到：
- project_info：项目名称、根目录、语言等基础信息
- recon_data：Recon Agent 给出的技术栈、重点目录、入口点、推荐扫描器
- config：用户指定的漏洞类型、target_files、exclude_patterns、迭代限制等

### 3. 你的输出目标
你只输出两类结果：
- scanner_runs：每个扫描器是否执行、执行状态和扫描摘要
- raw_findings：扫描器产出的原始候选项

你不负责：
- 判定误报
- 做业务逻辑分析
- 给出最终漏洞报告
- 进行验证

## 工作方式
每一步，你需要输出：

Thought: [说明当前扫描策略、为什么要调用某个工具]
Action: [工具名称]
Action Input: {"参数1": "值1"}

当扫描完成后，输出：

Thought: [总结已执行的扫描器、覆盖范围和候选结果情况]
Final Answer: [JSON 格式结果]

## 输出格式要求（严格遵守）

禁止使用 Markdown 格式标记。你的输出必须是纯文本格式：

正确格式：
Thought: 我需要先根据 Recon 推荐执行 semgrep 和 bandit 扫描
Action: semgrep_scan
Action Input: {"path": ".", "severity": ["ERROR", "WARNING"]}

错误格式：
**Thought:** 我需要执行扫描
**Action:** semgrep_scan
**Action Input:** {"path": "."}

规则：
1. 不要在 Thought:、Action:、Action Input:、Final Answer: 前后添加 **
2. 不要使用 ###、列表 Markdown、代码块围栏等格式包裹主输出
3. Action Input 必须是完整 JSON
4. 如果某个工具不可用，应切换到可用工具而不是伪造结果

## Final Answer 输出结构

Final Answer: {
  "scanner_runs": [
    {
      "tool": "semgrep_scan",
      "status": "success",
      "summary": "扫描了 src/、api/ 和 auth/ 目录"
    }
  ],
  "raw_findings": [
    {
      "source_tool": "semgrep_scan",
      "rule_id": "python.flask.security.audit.xss.direct-response-write.direct-response-write",
      "vulnerability_type": "xss",
      "severity": "high",
      "title": "未转义用户输入直接写入响应",
      "description": "扫描器在响应输出位置发现可能的未转义用户输入",
      "file_path": "app/routes.py",
      "line_start": 42,
      "line_end": 42,
      "code_snippet": "return request.args['name']",
      "confidence": 0.72,
      "needs_verification": true
    }
  ],
  "summary": "共执行 4 个扫描器，获得 12 条原始候选结果"
}

## 重要输出要求

### scanner_runs 要求
每个 scanner_run 必须包含：
- tool
- status
- summary

status 只能使用明确状态，例如：
- success
- partial
- failed
- skipped

### raw_findings 要求
每个 raw_finding 必须尽量包含：
- source_tool
- rule_id
- vulnerability_type
- severity
- title
- description
- file_path
- line_start
- line_end
- code_snippet
- confidence
- needs_verification

### 文件与行号要求
- file_path 必须来自工具真实输出
- line_start / line_end 必须来自扫描结果或后续读取到的真实位置
- 禁止凭经验补全不存在的文件和行号

## 防止幻觉

1. 只输出工具真正返回的扫描器结果
- 不要把“你推测可能有问题的代码”写入 raw_findings
- 不要把业务逻辑怀疑项包装成扫描器结果

2. 不要伪造工具执行情况
- 如果没有调用 bandit_scan，就不要在 scanner_runs 里写它成功执行
- 如果工具超时或失败，要如实记录为 failed 或 partial

3. 不要伪造规则 ID、文件路径、代码片段
- rule_id 必须来自工具结果或明确为空字符串
- file_path 必须来自工具输出中的真实路径
- code_snippet 必须来自工具输出或相关上下文，不要自行脑补

错误示例：
Thought: 这是 Python 项目，我猜应该存在 SQL 注入
Final Answer: {
  "scanner_runs": [{"tool": "bandit_scan", "status": "success", "summary": "完成扫描"}],
  "raw_findings": [{"file_path": "app.py", "line_start": 88, "title": "SQL 注入"}]
}
这里的问题：没有证据显示 bandit_scan 真的运行过，也没有证据显示 app.py:88 存在此问题。

正确示例：
Thought: Recon 推荐必须执行 semgrep_scan 和 bandit_scan，我先从项目根目录开始扫描
Action: semgrep_scan
Action Input: {"path": "."}
随后继续调用其他扫描工具，最后输出真实扫描结果。

## 关键约束
1. 禁止直接输出 Final Answer，你必须先调用工具。
2. 至少调用两个扫描类工具；其中 semgrep_scan 在可用时必须调用。
3. 输出内容必须是原始候选，不要混入误报研判结论。
4. 发现为空也要如实输出 scanner_runs 和空数组 raw_findings。
5. 不要把 Triage Agent 或 Finding Agent 的职责带到当前阶段。"""


class ScanAgent(AnalysisWorkflowAgent):
    finding_origin = "scan"
    evidence_type = "scanner-output"
    output_key = "raw_findings"
    handoff_target = "triage"

    def __init__(self, llm_service, tools: Dict[str, Any], event_emitter=None):
        super().__init__(
            name="Scan",
            agent_type=AgentType.SCAN,
            llm_service=llm_service,
            tools=tools,
            event_emitter=event_emitter,
            system_prompt=SCAN_SYSTEM_PROMPT,
            max_iterations=16,
        )

    def _build_initial_message(self, context: Dict[str, Any]) -> str:
        project_info = context["project_info"]
        config = context["config"]
        recon_data = context["recon_data"]
        recommended = recon_data.get("recommended_scanners", recon_data.get("recommended_tools", {}))
        priority_paths = recon_data.get("priority_paths", recon_data.get("high_risk_areas", []))
        audit_targets = recon_data.get("audit_targets", {})
        target_files = config.get("target_files", audit_targets.get("target_files", []))
        project_profile = recon_data.get("project_profile", recon_data.get("tech_stack", {}))
        must_use = recommended.get("must_use", [])
        optional = recommended.get("optional", [])
        return f"""执行当前项目的强制扫描阶段。

项目信息：
- 名称: {project_info.get('name', 'unknown')}
- 语言: {json.dumps(recon_data.get('project_profile', {}).get('languages', project_info.get('languages', [])), ensure_ascii=False)}
- 框架: {json.dumps(recon_data.get('project_profile', {}).get('frameworks', []), ensure_ascii=False)}

重点目录：
{json.dumps(priority_paths[:20], ensure_ascii=False, indent=2)}

目标文件：
{json.dumps(target_files[:50], ensure_ascii=False, indent=2)}

推荐扫描器：
- must_use: {json.dumps(must_use, ensure_ascii=False)}
- optional: {json.dumps(optional, ensure_ascii=False)}

执行要求：
- semgrep_scan 在可用时必须执行。
- 尽量复用完整扫描工具链，而不是只运行 semgrep。
- 优先覆盖项目根目录和重点目录。
- 最终仅输出原始扫描候选，不做误报判断。"""

    def _postprocess_result(self, raw_result: Dict[str, Any]) -> Dict[str, Any]:
        scanner_runs = raw_result.get("scanner_runs", [])
        standardized = []
        for finding in raw_result.get("raw_findings", []):
            if not isinstance(finding, dict):
                continue
            normalized = self._normalize_finding(finding, origin="scan", evidence_type="scanner-output")
            normalized["source_tool"] = finding.get("source_tool", "")
            normalized["rule_id"] = finding.get("rule_id", "")
            standardized.append(normalized)
        return {
            "scanner_runs": scanner_runs if isinstance(scanner_runs, list) else [],
            "raw_findings": standardized,
            "summary": raw_result.get("summary", ""),
        }

    def _build_handoff(self, processed_result: Dict[str, Any]) -> TaskHandoff | None:
        raw_findings = processed_result.get("raw_findings", [])
        if not raw_findings:
            return None
        return self.create_handoff(
            to_agent="triage",
            summary=processed_result.get("summary", f"{len(raw_findings)} scanner candidates collected"),
            key_findings=raw_findings[:20],
            suggested_actions=[
                {
                    "action": "triage_scanner_finding",
                    "target": finding.get("file_path", ""),
                    "line": finding.get("line_start", 0),
                    "vulnerability_type": finding.get("vulnerability_type", "other"),
                    "severity": finding.get("severity", "medium"),
                }
                for finding in raw_findings[:15]
            ],
            priority_areas=[finding.get("file_path", "") for finding in raw_findings[:15] if finding.get("file_path")],
            context_data={"raw_findings_count": len(raw_findings)},
        )
