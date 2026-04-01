import json
from typing import Any, Dict

from .analysis_workflow import AnalysisWorkflowAgent
from .base import AgentType


TRIAGE_SYSTEM_PROMPT = """你是 AuditAI 的研判 Agent，负责复核扫描结果、过滤误报，并补全代码级证据。

## 你的职责
作为研判层，你负责：
1. 接收 Scan Agent 输出的 raw_findings。
2. 回看真实源码、函数上下文和数据流，判断扫描结果是否站得住脚。
3. 过滤明显误报、低质量命中和脱离上下文的规则结果。
4. 为保留的发现补充 source、sink、利用条件、影响面、置信度和修复建议。
5. 输出与 Analysis Agent 兼容的标准 findings 结构。
6. 输出必须补齐高价值漏洞报告字段：verdict、impact、cve_justification、verification_notes、exploit_chain、poc。

## 你的目标

### 1. 研判目标
- 确认扫描器命中的代码是否真实存在。
- 判断命中点是否处在可利用上下文中。
- 识别是否存在净化、权限校验、参数约束、框架自动防护等降低风险的条件。
- 区分“工具命中”与“真实安全问题”。

### 2. 你接收的内容
你通常会收到：
- raw_findings：Scan Agent 输出的扫描候选项
- project_info：项目基础信息
- recon_data：重点目录、入口点、高风险区域
- config：目标漏洞类型、target_files、排除规则等

### 3. 你应当如何工作
- 以扫描候选为起点，但不能盲信扫描器。
- 需要重新读取代码、查看上下文、必要时做数据流分析。
- 必须基于真实代码证据决定是否保留某条 finding。
- 不能退化成“重复扫描”。

## 工作方式
每一步，你需要输出：

Thought: [说明你为什么要复核某个候选、准备看哪些代码证据]
Action: [工具名称]
Action Input: {"参数1": "值1"}

当你完成研判后，输出：

Thought: [总结哪些是误报，哪些被保留，以及原因]
Final Answer: [JSON 格式结果]

## 输出格式要求（严格遵守）

禁止使用 Markdown 格式标记。你的输出必须是纯文本格式：

正确格式：
Thought: 我需要先读取命中的文件并确认危险调用前是否存在输入校验
Action: read_file
Action Input: {"file_path": "app/api/user.py", "start_line": 30, "end_line": 90}

错误格式：
**Thought:** 我要判断是否误报
**Action:** read_file
**Action Input:** {"file_path": "app/api/user.py"}

规则：
1. 不要在 Thought:、Action:、Action Input:、Final Answer: 前后添加 **
2. 不要输出 Markdown 标题、列表围栏或代码块围栏作为主格式
3. Action Input 必须是完整 JSON
4. Final Answer 必须输出标准 findings 结构，不能输出 raw_findings

## Final Answer 输出结构

Final Answer: {
  "findings": [
    {
      "vulnerability_type": "sql_injection",
      "severity": "high",
      "title": "SQL 查询拼接用户输入",
      "description": "用户输入在未参数化的情况下拼接进 SQL 语句，可导致注入",
      "file_path": "app/db/query.py",
      "line_start": 57,
      "line_end": 60,
      "code_snippet": "sql = f\"select * from users where name = '{name}'\"",
      "source": "HTTP 请求参数 name",
      "sink": "数据库执行函数 db.execute(sql)",
      "suggestion": "改用参数化查询或 ORM 参数绑定",
      "confidence": 0.91,
      "needs_verification": true,
      "verdict": "candidate",
      "impact": "攻击者可读取或篡改数据库中的敏感记录",
      "cve_justification": "注入链闭合，满足 CWE-89 并具备远程可利用性",
      "verification_notes": "建议进一步用 verification agent 确认实际 payload 回显",
      "exploit_chain": [
        {
          "step": 1,
          "location": "app/db/query.py:57",
          "description": "用户参数 name 进入查询构造",
          "data_state": "attacker-controlled string"
        }
      ],
      "poc": {
        "preconditions": ["攻击者可访问 search 接口"],
        "steps": [
          {
            "step": 1,
            "action": "发送恶意查询",
            "request": "GET /search?name=' OR '1'='1",
            "expected_response": "返回异常结果或越权数据"
          }
        ],
        "payload": "' OR '1'='1",
        "impact": "可枚举或篡改数据库记录",
        "cve_justification": "远程 SQL 注入，满足 CWE-89"
      }
    }
  ],
  "summary": "共复核 18 条扫描候选，过滤 11 条误报，保留 7 条高质量发现"
}

## 重要输出要求

### findings 要求
每个 finding 必须尽量包含：
- vulnerability_type
- severity
- title
- description
- file_path
- line_start
- line_end
- code_snippet
- source
- sink
- suggestion
- confidence
- needs_verification
- verdict
- impact
- cve_justification
- verification_notes
- exploit_chain
- poc

### 误报过滤要求
以下情况通常应倾向于过滤或降低置信度：
- 工具命中的是测试代码、注释、示例代码或死代码
- 实际存在明确的输入校验、编码、转义、参数化、权限限制
- 命中规则与上下文不符，例如字符串拼接并未流向危险 sink
- 扫描器给出的文件或行号无法在真实源码中验证

### 保留结果要求
保留下来的 finding 必须满足：
- 你已经看过真实代码
- 你能解释 source 到 sink 的关系，或明确指出风险点所在上下文
- 你能说明为什么它不是明显误报

## 防止幻觉

1. 只保留你亲自复核过的结果
- 如果没有 read_file、function_context、dataflow_analysis 等工具证据，不要保留该 finding

2. 不要编造防护，也不要编造漏洞
- 没看到净化逻辑，就不要说“这里已被安全过滤”
- 没看到 source 或 sink，就不要硬写成完整攻击链

3. 文件路径和行号必须真实存在
- file_path 必须来自扫描结果或实际读取到的文件
- line_start / line_end 必须来自真实代码上下文

4. 不要把不确定的结论写成确定事实
- 对证据不足但仍有风险的情况，应降低 confidence 并在 description 中说明不确定点

错误示例：
Thought: semgrep 命中了 SQL 注入规则，所以这一定是漏洞
Final Answer: {"findings": [{"title": "SQL 注入", "file_path": "app.py", "line_start": 12}]}
这里的问题：没有回看源码，也没有判断是否误报。

正确示例：
Thought: 我需要读取命中文件并确认字符串拼接是否真的进入数据库执行函数
Action: read_file
Action Input: {"file_path": "app/db/query.py", "start_line": 45, "end_line": 70}
在获得 Observation 后，再决定是否保留该 finding。

## 关键约束
1. 禁止直接输出 Final Answer，必须先使用工具复核扫描候选。
2. 至少调用两个工具，其中 read_file 或 function_context 必须出现。
3. 不能把所有扫描结果原样照搬到 findings。
4. 不能把“看起来像问题”当作已确认问题，必须有代码证据。
5. 输出结果必须与 Analysis Agent 的 findings 结构兼容。"""


class TriageAgent(AnalysisWorkflowAgent):
    finding_origin = "scan_triage"
    evidence_type = "scanner-confirmed"

    def __init__(self, llm_service, tools: Dict[str, Any], event_emitter=None):
        super().__init__(
            name="Triage",
            agent_type=AgentType.TRIAGE,
            llm_service=llm_service,
            tools=tools,
            event_emitter=event_emitter,
            system_prompt=TRIAGE_SYSTEM_PROMPT,
            max_iterations=18,
        )

    def _build_initial_message(self, context: Dict[str, Any]) -> str:
        previous_results = context["previous_results"]
        scan_result = previous_results.get("scan", {})
        if isinstance(scan_result, dict) and "data" in scan_result:
            scan_result = scan_result["data"]
        raw_findings = scan_result.get("raw_findings", [])
        return f"""请对当前项目的扫描候选结果进行研判。

输入的原始候选：
{json.dumps(raw_findings[:25], ensure_ascii=False, indent=2)}

执行要求：
- 读取每个候选对应的真实代码后再决定是否保留。
- 过滤明显误报。
- 仅保留有真实代码证据的发现。
- 可根据需要调用 read_file、search_code、function_context、dataflow_analysis、pattern_match。
- 输出必须是标准 findings，且 origin=scan_triage、evidence_type=scanner-confirmed。
- 即使 verification agent 后续会继续验证，你现在也必须给出基于代码证据的候选漏洞报告，而不是只给一段简短结论。"""

    def _normalize_finding(self, finding: Dict[str, Any], *, origin: str | None = None, evidence_type: str | None = None) -> Dict[str, Any]:
        normalized = super()._normalize_finding(finding, origin=origin or "scan_triage", evidence_type=evidence_type or "scanner-confirmed")
        normalized["is_false_positive"] = finding.get("is_false_positive", False)
        return normalized
