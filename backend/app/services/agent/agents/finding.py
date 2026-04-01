import json
import re
import os
import tempfile
from typing import Any, Dict, List, Optional

from .analysis_workflow import AnalysisWorkflowAgent
from .base import AgentType
from .finding_controller import FindingController
from .finding_evidence import EvidenceBundleStore
from .finding_loop_detector import FindingLoopDetector
from .finding_skill_preloader import FindingSkillPreloader
from .finding_skill_protocol import build_finding_skill_protocol
from .finding_skill_router import build_finding_skill_route_message
from .finding_synthesizer import FindingSynthesizer
from .finding_worker import CandidateWorker


FINDING_SYSTEM_PROMPT = """你是 AuditAI 的高级漏洞挖掘 Agent，你的唯一使命是通过源码审计发现能够申报 CVE 或能被 各大厂商src / HackerOne / Bugcrowd 等赏金平台接收的真实安全漏洞。

你不是普通的代码扫描器——你是一位经验丰富的安全研究员，专注于发现高价值、可利用、有明确的POC、有实际危害的漏洞。你所审计的项目均已获取厂商授权，你的成果仅用于推动项目所属厂商的安全建设。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 核心原则

1. 只产出 CVE 级别的发现。任何不足以申报 CVE 或不会被赏金平台接收的问题，一律不报告。
2. 零容忍误报。每一个 finding 都必须有经过你亲自验证的完整 source→sink 利用链。不允许猜测、假设或套用模板。
3. 完整 POC 是交付标准。若尚未经过动态验证，也必须给出基于源码推导的可复现 POC。
4. 质量远大于数量。1 个真实可利用的高危漏洞，价值远高于 20 个无法验证的疑似问题。
5. 当项目没有满足要求的漏洞时，可以反馈“当前项目未发现可满足CVE申报条件的漏洞”，而不是生成一些低质量无价值漏洞或反馈一些你自己猜测可能存在风险但没有完整利用链的漏洞。
6. 你最终输出的结果应是你认为符合CVE条件的漏洞内容、漏洞位置、完整 source→sink 利用链、基于源码推导的可复现 POC以及修复建议。
7. 每一个发现必须回答"攻击者如何从外部触发它"，无法从外部触发的不报告。
8. 你需要尽可能地扫描整个项目，不放过任何一个符合CVE条件的漏洞，而不是在发现1-2个漏洞后就停止审计。
9. 你接下来的所有回复内容尽量用中文回复，不要使用英文


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## CVE 级别漏洞标准

你输出的每一个 finding 必须满足以下全部条件，缺一不可：

1. 可远程利用或可被未授权 / 低权限攻击者触发
2. 存在真实安全影响（RCE、数据泄露、权限提升、服务拒绝等）
3. 拥有从攻击者可控输入到危险操作的完整利用链
4. 你已通过代码审计完整验证该链路的每一环，无断裂、无假设
5. 能够给出可复现的 POC（请求构造、参数、预期结果）；如果尚未运行验证工具，至少要给出代码可推导的静态 POC

以下问题不满足 CVE 标准，禁止报告：
- 仅影响自身账户的低风险问题（如自身 XSS、CSRF 无敏感操作）
- 无安全影响的信息泄露（版本号、非敏感路径）
- 需要管理员权限才能触发的"漏洞"（除非存在权限提升链路）
- Best practice 偏差（缺少 HSTS、Cookie 标志位等）
- 理论可行但无法构造实际利用路径的问题
- 已有身份校验保护且无法绕过的接口

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 优先关注的漏洞类型（按 CVE 接受率排序）

### 第一梯队（CVE 接受率极高，优先投入精力）
- 远程代码执行 (RCE)：反序列化、模板注入 (SSTI)、命令注入、表达式注入
- 认证绕过：JWT 伪造/篡改、Session 固定、OAuth 流程缺陷、默认凭据
- SQL 注入：特别是 ORM 绕过、动态拼接、存储过程注入
- 服务端请求伪造 (SSRF)：可访问内网、云元数据、内部服务
- 路径穿越 / 任意文件读写：可读取敏感文件或写入 webshell

### 第二梯队（需满足特定利用条件）
- 权限提升：普通用户→管理员、跨租户数据访问
- IDOR / 越权：可访问或篡改其他用户的敏感资源
- XXE：可读取服务器文件或实现 SSRF
- 不安全的反序列化：即使未直接达到 RCE，但可篡改关键业务对象

### 第三梯队（仅在有完整利用链时报告）
- 存储型/反射型 XSS：可窃取管理员 Session 或触发敏感操作
- 密码重置漏洞：可接管任意账户
- 竞态条件 (TOCTOU)：可导致金额篡改或权限绕过

### 第四梯队
- 上述漏洞类型不包含，但凭你的经验判断可以申报CVE，或者过往CVE/hackerone等漏洞平台有过类似案例的漏洞
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 审计方式

1.使用Code Audit技能对项目进行代码审计（优先，核心方法，需要尽可能完全按照该skill要求进行审核）
/code-audit deep 
需要完整过一遍Code Audit中所有涉及漏洞类型的checklist

2.你凭借自身丰富的安全代码审计经验或根据项目历史CVE（如果存在）分析风险点，尤其注重各类逻辑漏洞，这是规则扫描以及传统SAST容易忽视的地方

3.如果使用上述方式未发现CVE级别的风险或你认为仍探测不全面，则尝试以下审计方式（非必须，仅在上述两个方法均未发现CVE或发现数量较少时进行补充审核）：

### 阶段一：攻击面测绘
1. 分析 Recon Agent 提供的入口点、技术栈、高风险区域。
2. 识别所有外部可达的输入点（HTTP 路由、API 端点、WebSocket、文件上传、消息队列消费者）。
3. 定位危险 sink 函数（exec/eval、SQL 执行、命令执行、文件操作、反序列化、模板渲染、HTTP 请求发起）。

### 阶段二：纵深挖掘
1. 对每个 source-sink 组合追踪完整数据流，逐跳确认：输入是否经过有效校验、净化、转义？中间是否存在绕过点？
2. 重点关注：
   - 黑名单过滤（通常可绕过）vs 白名单校验
   - 校验逻辑与使用逻辑不在同一层（TOCTOU）
   - 类型混淆 / 编码差异导致的绕过
   - 异常路径中跳过的安全检查
   - 多步操作中仅第一步做了鉴权
3. 一旦你已经拿到一个高价值漏洞的闭合利用链，并且能够写出 exploit_chain 与 poc，就应立即结束进一步搜索并输出 Final Answer。不要为了补充“更多背景”而耗尽迭代次数。

### 阶段三：垂类分析与历史 CVE 借鉴
当常规审计未发现明确漏洞时：
1. 根据项目类型（Web 框架 / ORM / 文件解析器 / 认证库 / API 网关 / CMS 等）进行垂类安全分析。
2. 使用 search_code 或 rag_query 搜索该类项目或该框架历史上常见的 CVE 模式。例如：
   - Spring 框架 → SpEL 注入、Actuator 未授权、路径归一化绕过
   - Django/Flask → 模板注入、ORM 注入、Debug 模式信息泄露
   - FastAPI/Starlette → 路径穿越、Multipart 解析漏洞
   - Java 反序列化 → Gadget Chain 利用
   - 文件解析库 → XXE、ZIP Slip、恶意文件名处理
3. 根据识别到的历史 CVE 模式，有针对性地在当前项目中搜索同类代码模式。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 利用链与 POC 规范

### 利用链要求
每个 finding 必须包含完整的利用链描述，格式为：

攻击者输入 → [传播路径节点1] → [传播路径节点2] → ... → 危险操作（sink）

要求：
- 链路中每一跳都必须标注具体的文件路径和行号
- 明确标注每一跳的数据变换（如 URL 解码、类型转换、字符串拼接）
- 如果链路中存在条件分支，必须说明满足漏洞触发的条件
- 如果中间经过校验函数，必须说明该校验为何可被绕过或不生效

### POC 要求
每个 finding 必须给出可复现的 POC，需包含：
- 前置条件（如需要的用户角色、系统配置状态）
- 完整的攻击请求（HTTP 方法、URL、Headers、Body）
- 预期的漏洞触发结果（服务端行为、响应内容、可观测的副作用）
- 如果是多步攻击，给出每一步的请求和预期响应

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 工作方式

每一步，你需要输出：

Thought: [说明当前审计的攻击面或数据流，以及为什么认为这条路径可能存在 CVE 级别漏洞]
Action: [工具名称]
Action Input: {"参数1": "值1"}

当你完成挖掘后，你需要输出：

Thought: [总结发现了哪些 CVE 级别漏洞，每个漏洞的利用链是否完整，POC 是否可复现]
Final Answer: [JSON 格式结果]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 输出格式要求（严格遵守）

禁止使用 Markdown 格式标记。输出必须是纯文本格式：

正确格式：
Thought: Recon 显示 /api/admin/execute 端点接受用户输入的 command 参数，我需要确认是否存在命令注入
Action: read_file
Action Input: {"file_path": "app/api/admin.py", "start_line": 45, "end_line": 90}

错误格式：
**Thought:** 可能存在命令注入
**Action:** read_file
**Action Input:** {"file_path": "app/api/admin.py"}

规则：
1. 不要在 Thought:、Action:、Action Input:、Final Answer: 前后添加 **
2. 不要使用其他 Markdown 格式（如 `###`、`*斜体*` 等）
3. Action Input 必须是完整 JSON，不能为空或截断
4. Final Answer 必须使用下方标准结构

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## Final Answer 输出结构

Final Answer: {
  "findings": [
    {
      "vulnerability_type": "command_injection",
      "severity": "critical",
      "title": "/api/admin/execute 端点存在未过滤的操作系统命令注入",
      "description": "端点从请求参数 `cmd` 中获取用户输入，经过 URL 解码后直接传入 subprocess.Popen() 的 shell=True 模式执行。虽然存在 is_admin() 中间件，但该中间件仅校验 JWT 签名是否有效，未校验 JWT 中的 role 字段，导致任何持有有效 JWT 的普通用户均可触发命令执行。",
      "file_path": "app/api/admin.py",
      "line_start": 67,
      "line_end": 74,
      "code_snippet": "cmd = urllib.parse.unquote(request.args.get('cmd'))\nresult = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)",

      "source": "HTTP GET 参数 cmd（任何持有有效 JWT 的用户可控）",
      "sink": "subprocess.Popen(cmd, shell=True) at app/api/admin.py:72",
      "suggestion": "1. 使用白名单限制可执行命令 2. 使用 subprocess.run([...]) 替代 shell=True 3. 在中间件中校验 JWT 的 role 字段",
      "confidence": 0.95,
      "needs_verification": true,
      "verdict": "candidate",

      "exploit_chain": [
        {
          "step": 1,
          "location": "app/api/admin.py:52",
          "description": "Flask 路由 /api/admin/execute 接收 GET 请求",
          "data_state": "原始 HTTP 请求参数 cmd"
        },
        {
          "step": 2,
          "location": "app/middleware/auth.py:18",
          "description": "is_admin() 中间件校验 JWT 签名有效性但未校验 role 字段",
          "data_state": "鉴权通过，请求进入处理函数",
          "bypass_reason": "JWT payload 中的 role 字段未被检查，普通用户的 JWT 也能通过校验"
        },
        {
          "step": 3,
          "location": "app/api/admin.py:67",
          "description": "URL 解码用户输入: urllib.parse.unquote(request.args.get('cmd'))",
          "data_state": "解码后的命令字符串，攻击者完全可控"
        },
        {
          "step": 4,
          "location": "app/api/admin.py:72",
          "description": "命令字符串直接传入 subprocess.Popen(cmd, shell=True)",
          "data_state": "操作系统命令执行（SINK）"
        }
      ],

      "poc": {
        "preconditions": [
          "攻击者拥有任意有效用户账户（普通用户即可）",
          "目标服务运行于 Linux 环境"
        ],
        "steps": [
          {
            "step": 1,
            "action": "使用普通用户凭据登录获取 JWT",
            "request": "POST /api/auth/login HTTP/1.1\nContent-Type: application/json\n\n{\"username\": \"normal_user\", \"password\": \"password123\"}",
            "expected_response": "200 OK，响应中包含 access_token"
          },
          {
            "step": 2,
            "action": "使用普通用户 JWT 调用管理员命令执行端点",
            "request": "GET /api/admin/execute?cmd=id HTTP/1.1\nAuthorization: Bearer <普通用户的access_token>",
            "expected_response": "200 OK，响应体包含 uid=xxx(www-data) 等命令执行结果"
          },
          {
            "step": 3,
            "action": "验证任意命令执行能力",
            "request": "GET /api/admin/execute?cmd=cat%20/etc/passwd HTTP/1.1\nAuthorization: Bearer <普通用户的access_token>",
            "expected_response": "200 OK，响应体包含 /etc/passwd 文件内容"
          }
        ],
        "impact": "未授权远程代码执行（RCE），攻击者可以服务进程身份执行任意操作系统命令，可完全控制服务器。",
        "cve_justification": "该漏洞允许低权限用户通过权限校验缺陷绕过管理员鉴权，直接执行任意操作系统命令，属于 CWE-78 (OS Command Injection) 和 CWE-862 (Missing Authorization)，满足 CVE 申报标准。"
      },
      "impact": "未授权远程代码执行（RCE），攻击者可以服务进程身份执行任意操作系统命令，可完全控制服务器。",
      "cve_justification": "该漏洞允许低权限用户通过权限校验缺陷绕过管理员鉴权，直接执行任意操作系统命令，属于 CWE-78 (OS Command Injection) 和 CWE-862 (Missing Authorization)，满足 CVE 申报标准。",
      "verification_notes": "如果尚未执行动态验证，请在此明确说明仍需 verification agent 做的验证动作"
    }
  ],
  "summary": "对 N 条高危攻击面完成深度审计，发现 M 个 CVE 级别漏洞，均已提供完整利用链和可复现 POC"
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## findings 各字段说明

### 基础字段
- vulnerability_type: 漏洞类型（command_injection / sql_injection / ssrf / path_traversal / deserialization / ssti / auth_bypass / idor / xxe / xss / business_logic / other）
- severity: 严重程度（critical / high，只输出这两个等级；medium/low 级别不满足 CVE 标准，不报告）
- title: 漏洞标题，需具体到受影响的端点或功能
- description: 完整的漏洞成因描述，需包含：攻击者可控输入、缺失的安全机制、危险操作、业务影响
- file_path: 漏洞核心代码所在文件路径（必须来自你实际读取的文件）
- line_start / line_end: 漏洞核心代码行号（必须来自你实际读取的代码）
- code_snippet: 漏洞核心代码片段
- source: 攻击者可控输入的具体来源
- sink: 危险操作的具体位置
- suggestion: 修复建议
- confidence: 置信度（0.0-1.0），必须与证据强度严格匹配：
  - 0.90-1.0: 完整 source→sink 链路已验证，POC 可构造
  - 0.80-0.89: 链路完整但部分跳需要运行时条件
  - 0.70-0.79: 仅在漏洞价值高且利用链已经闭合时作为 candidate 报告，并明确 verification_notes
- needs_verification: 是否需要 Verification Agent 进一步动态验证
- verdict: `candidate` 或 `confirmed`。静态代码链闭合但未运行动态验证时使用 `candidate`

### 利用链字段（exploit_chain）
- step: 步骤序号
- location: 代码位置（文件:行号）
- description: 该步骤的操作或数据流转
- data_state: 数据在该节点的状态
- bypass_reason:（可选）如果该步骤涉及绕过安全机制，说明绕过原因

### POC 字段（poc）
- preconditions: 触发漏洞的前置条件列表
- steps: POC 步骤列表，每步包含 step(序号)、action(操作说明)、request(完整请求)、expected_response(预期响应)
- impact: 漏洞的实际安全影响
- cve_justification: CVE 申报依据，需引用 CWE 编号并说明为何满足 CVE 标准

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 防止幻觉与误报（最高优先级约束）

1. 绝对禁止凭空捏造
   - file_path 必须来自你通过 list_files / read_file / search_code 实际确认存在的文件
   - line_start / line_end 必须来自你通过 read_file 实际读到的代码行
   - code_snippet 必须是你实际读到的代码的精确引用
   - exploit_chain 中每一跳的 location 必须是你实际验证过的代码位置

2. 禁止套用漏洞模板
   - 不要因为项目使用了某个框架就假设存在该框架的历史漏洞
   - 不要因为看到某个函数名就假设它的实现不安全
   - 必须逐行读取关键代码，确认安全缺陷确实存在

3. 利用链必须闭合
   - 如果 source→sink 链路中任何一环你无法通过代码阅读确认，则该 finding 不成立
   - 如果中间存在你无法确认是否可绕过的安全检查，则该 finding 不成立
   - 宁可不报，也不报一个链路断裂的伪漏洞

4. POC 必须合理
   - POC 中的请求格式必须与代码中的路由定义一致
   - POC 中引用的参数名必须与代码中实际使用的参数名一致
   - 预期响应必须基于代码逻辑推导，不能凭空编造

5. confidence 必须诚实
   - 如果你对利用链的某一环不完全确定，必须降低 confidence
   - confidence < 0.80 的发现不应出现在最终输出中

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 关键约束

1. 禁止直接输出 Final Answer，必须先通过工具调用阅读源码并验证利用链。
2. 至少调用三个工具，其中 read_file 和 search_code / rag_query / function_context / dataflow_analysis 各至少出现一次。
3. 不依赖 Scan Agent 输出作为主要证据来源——你是独立的安全研究员，不是扫描结果的搬运工。
4. 只报告 critical 和 high 级别的漏洞。medium / low / info 级别不满足 CVE 或赏金标准，不报告。
5. 每个 finding 必须包含完整的 exploit_chain 和 poc 字段。缺少利用链或 POC 的发现不允许输出。
6. 如果经过充分审计后确实未发现 CVE 级别漏洞，输出空 findings 列表并在 summary 中诚实说明已审计的攻击面和未发现漏洞的原因。这远好于输出一个虚假的 finding。
7. 输出必须与 Analysis Agent 的 findings 结构保持兼容（exploit_chain 和 poc 为新增扩展字段）。"""


class FindingAgent(AnalysisWorkflowAgent):
    finding_origin = "direct_finding"
    evidence_type = "source-analysis"
    HISTORY_ASSISTANT_LIMIT = 1200
    HISTORY_OBSERVATION_LIMIT = 1600
    HISTORY_FINALIZATION_TOKEN_THRESHOLD = 250000
    REPORT_FINALIZATION_THRESHOLD = 6
    REPORT_PHASE_EVIDENCE_CONFIDENCE = 0.85
    PREEMPTIVE_FINALIZATION_THRESHOLDS = {6, 3, 1}
    RECOVERY_KEYWORDS = {
        "ssrf": ["ssrf", "服务端请求伪造", "server-side request forgery"],
        "path_traversal": ["path traversal", "路径遍历", "directory traversal", "lfi", "rfi"],
        "sql_injection": ["sql injection", "sql注入", "sql 注入", "sqli"],
        "xss": ["xss", "cross-site scripting", "跨站脚本"],
        "auth_bypass": ["auth bypass", "authentication bypass", "认证绕过", "未授权"],
        "idor": ["idor", "越权", "对象引用", "对象归属"],
        "command_injection": ["command injection", "命令注入", "rce", "远程命令执行"],
        "deserialization": ["deserialization", "反序列化"],
        "file_upload": ["file upload", "文件上传", "上传绕过"],
        "business_logic": ["business logic", "业务逻辑", "状态机", "竞态", "race condition"],
    }
    RECOVERY_SEVERITY = {
        "ssrf": "high",
        "path_traversal": "high",
        "sql_injection": "critical",
        "xss": "high",
        "auth_bypass": "high",
        "idor": "high",
        "command_injection": "critical",
        "deserialization": "critical",
        "file_upload": "high",
        "business_logic": "medium",
    }

    def __init__(self, llm_service, tools: Dict[str, Any], event_emitter=None):
        super().__init__(
            name="Finding",
            agent_type=AgentType.FINDING,
            llm_service=llm_service,
            tools=tools,
            event_emitter=event_emitter,
            system_prompt="\n\n".join([FINDING_SYSTEM_PROMPT, build_finding_skill_protocol()]),
            tool_usage_guide="",
            max_iterations=32,
        )
        self._controller = FindingController()
        self._evidence_store = EvidenceBundleStore()
        self._loop_detector = FindingLoopDetector()
        self._synthesizer = FindingSynthesizer()
        self._skill_preloader = FindingSkillPreloader()
        self._worker = CandidateWorker(self._evidence_store)
        self._runtime_state = None
        self._skill_bootstrap_state: Dict[str, Any] = {
            "primary_skill": "",
            "skill_file_path": "",
            "loaded": True,
        }
        self._iteration_candidate_id = None
        self._last_observation_candidate_id = None
        self._last_iteration_control_prompt = ""
        self._task_id = ""

    def _use_structured_tool_calling(self) -> bool:
        return True

    def _build_structured_tool_schemas(self) -> List[Dict[str, Any]]:
        schemas = super()._build_structured_tool_schemas()
        filtered: List[Dict[str, Any]] = []
        for schema in schemas:
            function_payload = schema.get("function") if isinstance(schema, dict) else None
            tool_name = ""
            if isinstance(function_payload, dict):
                tool_name = str(function_payload.get("name") or "").strip()
            else:
                tool_name = str(schema.get("name") or "").strip() if isinstance(schema, dict) else ""
            if tool_name in {"think", "reflect", "load_skill_body", "skill_resource_lookup"}:
                continue
            filtered.append(schema)
        return filtered

    def _record_candidate_message(self, candidate_id: Optional[str], role: str, content: str) -> None:
        if not self._runtime_state:
            return
        if not candidate_id:
            return
        session = self._runtime_state.worker_sessions.get(candidate_id)
        if not session:
            return
        session.message_history.append({"role": role, "content": content})
        if len(session.message_history) > 10:
            session.message_history = [session.message_history[0]] + session.message_history[-9:]

    def _record_active_candidate_message(self, role: str, content: str) -> None:
        candidate_id = self._iteration_candidate_id
        if not candidate_id:
            candidate = self._current_candidate()
            candidate_id = candidate.id if candidate else None
        self._record_candidate_message(candidate_id, role, content)

    async def _prepare_runtime_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        self._evidence_store = EvidenceBundleStore()
        self._loop_detector = FindingLoopDetector()
        self._worker = CandidateWorker(self._evidence_store)
        self._last_iteration_control_prompt = ""
        self._task_id = str(context.get("task_id", "") or "").strip()
        skill_context = context.get("skill_context", {}) or {}
        route_plan = skill_context.get("route_plan", {}) or {}
        primary_skill = str(route_plan.get("primary_skill", "") or "").strip()
        skill_file_path = self._resolve_primary_skill_file_path(skill_context, primary_skill)
        self._skill_bootstrap_state = {
            "primary_skill": primary_skill,
            "skill_file_path": skill_file_path,
            "loaded": not bool(primary_skill and skill_file_path),
        }
        context["preloaded_skill_context"] = await self._skill_preloader.preload(
            context.get("config", {}).get("user_id"),
            context,
        )
        context["skill_bootstrap_state"] = dict(self._skill_bootstrap_state)
        context["finding_runtime_state"] = self._controller.build_runtime_state(context)
        self._runtime_state = context["finding_runtime_state"]
        return context

    def _rounds_left(self, current_iteration: Optional[int] = None) -> int:
        iteration = self._iteration if current_iteration is None else current_iteration
        if iteration <= 0:
            return self.config.max_iterations
        return max(self.config.max_iterations - iteration, 0)

    def _phase_guidance(self) -> str:
        if not self._runtime_state:
            return ""
        if self._runtime_state.phase == "report_finalization":
            return (
                "Do not expand to new candidates. Consolidate the strongest existing evidence, "
                "close the current exploit chain, and prepare the Final Answer from collected observations only."
            )
        return (
            "Stay in evidence collection mode. Keep tracing the active candidate until you either close the exploit chain "
            "or reach the report-finalization threshold."
        )

    def _candidate_summary(self, candidate) -> Dict[str, Any]:
        if not candidate:
            return {
                "bundle_count": 0,
                "file_paths": [],
                "entry_point_refs": [],
                "priority_path_refs": [],
                "evidence_gaps": [],
                "max_confidence": 0.0,
            }
        return self._evidence_store.build_candidate_summary(candidate.evidence_bundle_ids)

    def _is_closed_exploit_chain_candidate(self, candidate) -> bool:
        summary = self._candidate_summary(candidate)
        return (
            summary.get("bundle_count", 0) >= 2
            and not summary.get("evidence_gaps")
            and float(summary.get("max_confidence", 0.0) or 0.0) >= self.REPORT_PHASE_EVIDENCE_CONFIDENCE
        )

    def _select_reporting_candidate(self):
        if not self._runtime_state:
            return None
        closed_candidates = [candidate for candidate in self._runtime_state.queue if self._is_closed_exploit_chain_candidate(candidate)]
        if closed_candidates:
            return max(
                closed_candidates,
                key=lambda candidate: (
                    float(self._candidate_summary(candidate).get("max_confidence", 0.0) or 0.0),
                    len(candidate.evidence_bundle_ids),
                    candidate.priority,
                ),
            )
        current = self._current_candidate()
        if current:
            return current
        return self._runtime_state.queue[0] if self._runtime_state.queue else None

    def _enter_report_finalization_phase(self, reason: str) -> None:
        if not self._runtime_state:
            return
        self._runtime_state.phase = "report_finalization"
        if reason:
            self._runtime_state.phase_reason = reason
        reporting_candidate = self._select_reporting_candidate()
        if reporting_candidate:
            self._runtime_state.active_candidate_id = reporting_candidate.id
            if reason == "closed_exploit_chain_candidate":
                self._persist_candidate_checkpoint(reporting_candidate, trigger=reason)
        for candidate_id, session in self._runtime_state.worker_sessions.items():
            if candidate_id == self._runtime_state.active_candidate_id:
                session.status = "finalizing"
            elif session.status == "active":
                session.status = "paused"

    def _update_runtime_phase(self, current_iteration: Optional[int] = None) -> None:
        if not self._runtime_state:
            return
        if self._runtime_state.phase == "report_finalization":
            return
        if any(self._is_closed_exploit_chain_candidate(candidate) for candidate in self._runtime_state.queue):
            self._enter_report_finalization_phase("closed_exploit_chain_candidate")
            return
        if self._rounds_left(current_iteration) <= self.REPORT_FINALIZATION_THRESHOLD:
            self._enter_report_finalization_phase("tail_budget")

    def _on_iteration_start(self, iteration: int) -> None:
        self._update_runtime_phase(iteration)

    def _build_preemptive_finalization_prompt(self, rounds_left: int) -> str:
        if rounds_left not in self.PREEMPTIVE_FINALIZATION_THRESHOLDS:
            return ""
        if rounds_left == 6:
            return (
                "Stop expanding coverage. You are inside the final 6 reasoning rounds. "
                "Switch from broad exploration to report_finalization preparation, keep the active candidate, "
                "and consolidate only already collected evidence."
            )
        if rounds_left == 3:
            return (
                "Merge the strongest existing evidence now. No new candidates, no broad search, and no extra coverage passes. "
                "Use the remaining rounds to finish exploit_chain, poc, impact, and cve_justification from current observations."
            )
        return (
            "Return Final Answer immediately. Do not emit another Action unless it is strictly required to format the final report. "
            "Use only already collected evidence and produce the compliant final vulnerability JSON now."
        )

    def _build_iteration_control_prompt(self) -> str:
        prompt = self._build_preemptive_finalization_prompt(self._rounds_left())
        if not prompt:
            self._last_iteration_control_prompt = ""
            return ""
        if prompt == self._last_iteration_control_prompt:
            return ""
        self._last_iteration_control_prompt = prompt
        return prompt

    def _final_only_mode_active(self, current_iteration: Optional[int] = None) -> bool:
        if not self._runtime_state:
            return False
        if self._runtime_state.phase != "report_finalization":
            return False
        if self._runtime_state.phase_reason == "closed_exploit_chain_candidate":
            return True
        return self._rounds_left(current_iteration) <= 3

    def _structured_tools_enabled_for_iteration(self) -> bool:
        return not self._final_only_mode_active()

    def _build_no_action_prompt(self) -> str:
        if self._final_only_mode_active():
            return (
                "Finalization lock active. No more tools. Return a compliant Final Answer immediately "
                "using only the already collected evidence."
            )
        return super()._build_no_action_prompt()

    def _should_abort_after_llm_failure(self, assistant_output: str, failure_count: int) -> bool:
        if self._final_only_mode_active() and assistant_output.startswith("[LLM"):
            return True
        return super()._should_abort_after_llm_failure(assistant_output, failure_count)

    def _checkpoint_file_path(self) -> str:
        task_id = str(getattr(self, "_task_id", "") or "").strip()
        if not task_id:
            return ""
        root = str(getattr(self, "_current_project_root", "") or "").strip() or tempfile.gettempdir()
        return os.path.join(root, ".auditai", "finding-checkpoints", f"{task_id}.json")

    def _checkpoint_findings_for_candidate(self, candidate) -> List[Dict[str, Any]]:
        if not self._runtime_state or not candidate:
            return []
        runtime_cls = self._runtime_state.__class__
        session = self._runtime_state.worker_sessions.get(candidate.id)
        checkpoint_state = runtime_cls(
            plan=self._runtime_state.plan,
            coverage=self._runtime_state.coverage,
            queue=[candidate],
            worker_sessions={candidate.id: session} if session else {},
            active_candidate_id=candidate.id,
            phase=self._runtime_state.phase,
            phase_reason=self._runtime_state.phase_reason,
        )
        result = self._synthesizer.synthesize(checkpoint_state, self._evidence_store)
        findings = result.get(self.output_key, [])
        return findings if isinstance(findings, list) else []

    def _persist_candidate_checkpoint(self, candidate, *, trigger: str) -> str:
        checkpoint_path = self._checkpoint_file_path()
        if not checkpoint_path:
            return ""
        os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
        payload = {
            "task_id": self._task_id,
            "phase": self._runtime_state.phase if self._runtime_state else "",
            "phase_reason": self._runtime_state.phase_reason if self._runtime_state else "",
            "trigger": trigger,
            "candidate_id": getattr(candidate, "id", ""),
            "vulnerability_type": getattr(candidate, "vuln_family", ""),
            "summary": f"Checkpoint captured for {getattr(candidate, 'id', 'candidate')} via {trigger}.",
            "findings": self._checkpoint_findings_for_candidate(candidate),
        }
        with open(checkpoint_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return checkpoint_path

    def _load_checkpoint_result(self) -> Dict[str, Any]:
        checkpoint_path = self._checkpoint_file_path()
        if not checkpoint_path:
            return {}
        if not os.path.exists(checkpoint_path):
            return {}
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, TypeError):
            return {}
        findings = payload.get("findings", [])
        if not isinstance(findings, list) or not findings:
            return {}
        return {
            self.output_key: findings,
            "summary": str(payload.get("summary") or "Recovered final report from finding checkpoint."),
        }

    def _should_checkpoint_candidate(self, candidate) -> tuple[bool, str]:
        summary = self._candidate_summary(candidate)
        if self._is_closed_exploit_chain_candidate(candidate):
            return True, "closed_exploit_chain_candidate"
        if summary.get("bundle_count", 0) >= 1 and float(summary.get("max_confidence", 0.0) or 0.0) >= 0.9:
            return True, "high_confidence_candidate"
        return False, ""

    def _maybe_checkpoint_candidate(self, candidate) -> None:
        should_checkpoint, trigger = self._should_checkpoint_candidate(candidate)
        if not should_checkpoint:
            return
        self._persist_candidate_checkpoint(candidate, trigger=trigger)

    def _bootstrap_required(self) -> bool:
        primary_skill = str(self._skill_bootstrap_state.get("primary_skill", "") or "").strip()
        skill_file_path = str(self._skill_bootstrap_state.get("skill_file_path", "") or "").strip()
        return bool(primary_skill and skill_file_path) and not bool(self._skill_bootstrap_state.get("loaded"))

    @staticmethod
    def _normalize_path(value: Any) -> str:
        return str(value or "").replace("\\", "/").strip()

    def _resolve_primary_skill_file_path(self, skill_context: Dict[str, Any], primary_skill: str) -> str:
        if not primary_skill:
            return ""
        candidates = []
        for key in ("matched", "metadata"):
            items = skill_context.get(key) or []
            if isinstance(items, list):
                candidates.extend(items)
        for item in candidates:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or item.get("id") or "").strip()
            if slug != primary_skill:
                continue
            paths = item.get("paths") or {}
            if not isinstance(paths, dict):
                continue
            skill_file_path = paths.get("skill_file_path") or paths.get("skill_file")
            if skill_file_path:
                return str(skill_file_path)
        return ""

    def _is_bootstrap_action(self, invocation) -> bool:
        skill_file_path = self._normalize_path(self._skill_bootstrap_state.get("skill_file_path"))
        if not skill_file_path:
            return False
        action_input = invocation.action_input or {}
        if invocation.action == "read_file":
            return self._normalize_path(action_input.get("file_path")) == skill_file_path
        if invocation.action == "read_many_files":
            file_paths = action_input.get("file_paths") or []
            if not isinstance(file_paths, list):
                return False
            return any(self._normalize_path(path) == skill_file_path for path in file_paths)
        return False

    def _bootstrap_required_observation(self) -> str:
        primary_skill = str(self._skill_bootstrap_state.get("primary_skill", "") or "").strip()
        skill_file_path = str(self._skill_bootstrap_state.get("skill_file_path", "") or "").strip()
        return (
            "Primary audit skill bootstrap is still required. "
            f"Read the primary skill file for {primary_skill} first with read_file(file_path=\"{skill_file_path}\") "
            "or include that exact path in read_many_files before using general audit tools."
        )

    @staticmethod
    def _tool_observation_failed(observation: str) -> bool:
        lowered = (observation or "").lower()
        return lowered.startswith("error") or lowered.startswith("tool execution failed") or lowered.startswith("tool '")

    @staticmethod
    def _compatibility_skill_tool_observation(invocation) -> str:
        action_input = invocation.action_input or {}
        skill_ref = str(action_input.get("skill_ref") or "").strip() or "the active skill"
        return (
            f"Do not use {invocation.action} for Finding runtime skill loading. "
            f"Read {skill_ref}'s catalog paths with generic file tools instead: "
            "read_file(skill_file_path), list_files(references_root), and read_many_files([...])."
        )

    def _build_iteration_messages(self) -> List[Dict[str, str]]:
        if not self._runtime_state or len(self._conversation_history) < 2:
            return self._conversation_history
        self._update_runtime_phase(self._iteration)
        candidate = self._current_candidate()
        if not candidate:
            return self._conversation_history
        self._iteration_candidate_id = candidate.id
        session = self._runtime_state.worker_sessions.get(candidate.id)
        if not session:
            return self._conversation_history
        rounds_left = self._rounds_left()
        context_block = {
            "phase": self._runtime_state.phase,
            "phase_reason": self._runtime_state.phase_reason,
            "current_iteration": self._iteration,
            "max_iterations": self.config.max_iterations,
            "rounds_left": rounds_left,
            "phase_guidance": self._phase_guidance(),
            "active_candidate_id": candidate.id,
            "brief": session.brief,
            "remaining_budget": session.remaining_budget,
            "followup_rounds_left": session.followup_rounds_left,
            "evidence_bundle_ids": candidate.evidence_bundle_ids[-6:],
            "entry_point_refs": candidate.entry_point_refs[:3],
            "sink_refs": candidate.sink_refs[:3],
            "control_refs": candidate.control_refs[:3],
            "rotation_history": self._runtime_state.rotation_history[-4:],
        }
        return [
            self._conversation_history[0],
            self._conversation_history[1],
            {
                "role": "user",
                "content": "Active candidate local context:\n"
                + json.dumps(context_block, ensure_ascii=False, indent=2),
            },
            *session.message_history[-8:],
        ]

    def _on_assistant_turn(self, llm_output: str, step) -> None:
        del step
        self._record_active_candidate_message("assistant", llm_output)

    def _on_observation_turn(self, observation: str, step) -> None:
        del step
        self._record_candidate_message(self._last_observation_candidate_id, "user", f"Observation:\n{observation}")

    def _assistant_history_content(self, assistant_output: str, step) -> str:
        del step
        text = str(assistant_output or "").strip()
        if len(text) <= self.HISTORY_ASSISTANT_LIMIT:
            return text
        return text[: self.HISTORY_ASSISTANT_LIMIT] + "\n...[truncated for history]"

    def _observation_history_content(self, observation: str, step) -> str:
        action = getattr(step, "action", None) or "observation"
        compact = self._trim_observation(observation, max_length=self.HISTORY_OBSERVATION_LIMIT)
        return f"Observation ({action}):\n{compact}"

    def _should_skip_full_history_finalization(self) -> bool:
        return self._total_tokens >= self.HISTORY_FINALIZATION_TOKEN_THRESHOLD

    def _current_candidate(self):
        if not self._runtime_state:
            return None
        return self._controller.get_active_candidate(self._runtime_state)

    def _advance_candidate(self, reason: str = "") -> None:
        candidate = self._current_candidate()
        if not candidate or not self._runtime_state:
            return
        if self._runtime_state.phase == "report_finalization":
            return
        if reason and reason not in candidate.business_flow_notes:
            candidate.business_flow_notes.append(reason)
        self._controller.rotate_candidate(self._runtime_state, reason or "candidate rotated")

    async def _execute_step_actions(self, step, failed_tool_calls: Dict[str, int]) -> str:
        if self._final_only_mode_active():
            return (
                "Finalization lock active. No more tools may run in final-only mode. "
                "Return Final Answer immediately using only the evidence already collected."
            )
        observations: List[str] = []
        active_candidate = self._current_candidate()
        self._last_observation_candidate_id = active_candidate.id if active_candidate else None
        for index, invocation in enumerate(self._iter_step_actions(step), start=1):
            if invocation.action in {"load_skill_body", "skill_resource_lookup"}:
                observations.append(
                    f"{index}. {invocation.action}({json.dumps(invocation.action_input or {}, ensure_ascii=False, sort_keys=True)}) =>\n"
                    f"{self._compatibility_skill_tool_observation(invocation)}"
                )
                continue
            if self._bootstrap_required() and invocation.action not in {"think", "reflect"}:
                if not self._is_bootstrap_action(invocation):
                    observations.append(
                        f"{index}. {invocation.action}({json.dumps(invocation.action_input or {}, ensure_ascii=False, sort_keys=True)}) =>\n"
                        f"{self._bootstrap_required_observation()}"
                    )
                    continue

            preview = self._loop_detector.preview(invocation.action, invocation.action_input)
            if preview.status == "block":
                observations.append(
                    f"{index}. {invocation.action}({json.dumps(invocation.action_input or {}, ensure_ascii=False, sort_keys=True)}) =>\n"
                    f"{preview.message}"
                )
                self._advance_candidate(preview.message)
                active_candidate = self._current_candidate()
                continue

            await self.emit_llm_action(invocation.action, invocation.action_input)
            tool_call_key = f"{invocation.action}:{json.dumps(invocation.action_input or {}, sort_keys=True)}"
            observation = await self.execute_tool(invocation.action, invocation.action_input or {})
            if isinstance(observation, str) and "Error" in observation:
                failed_tool_calls[tool_call_key] = failed_tool_calls.get(tool_call_key, 0) + 1
                if failed_tool_calls[tool_call_key] >= 3:
                    observation += "\nRepeated tool failure detected. Switch tools, narrow the scope, or produce Final Answer."
                    failed_tool_calls[tool_call_key] = 0
            else:
                failed_tool_calls.pop(tool_call_key, None)

            if self._is_bootstrap_action(invocation) and not self._tool_observation_failed(observation):
                self._skill_bootstrap_state["loaded"] = True

            worker_result = None
            if active_candidate:
                worker_result = self._worker.record_tool_result(
                    active_candidate,
                    invocation.action,
                    invocation.action_input or {},
                    observation,
                )
                if worker_result.coverage_delta.get("file_paths"):
                    self._runtime_state.coverage.covered_files.update(worker_result.coverage_delta["file_paths"])
                self._controller.consume_worker_budget(
                    self._runtime_state,
                    active_candidate.id,
                    spent=1,
                    reason="worker budget exhausted",
                )
            evidence_delta = len(worker_result.evidence_bundle_ids) if worker_result else 0
            loop_decision = self._loop_detector.register(
                invocation.action,
                invocation.action_input or {},
                evidence_delta=evidence_delta,
            )

            observation_suffix = ""
            if worker_result and worker_result.evidence_bundle_ids:
                observation_suffix += (
                    "\n\nEvidence Bundles:\n"
                    + json.dumps(worker_result.evidence_bundle_ids, ensure_ascii=False)
                )
            if loop_decision.status in {"warn", "block"}:
                observation_suffix += f"\n\nLoop Detector: {loop_decision.message}"
            observations.append(
                f"{index}. {invocation.action}({json.dumps(invocation.action_input or {}, ensure_ascii=False, sort_keys=True)}) =>\n"
                f"{observation}{observation_suffix}"
            )

            if active_candidate and worker_result and worker_result.evidence_bundle_ids:
                active_candidate.status = worker_result.status
                self._maybe_checkpoint_candidate(active_candidate)
                self._update_runtime_phase(self._iteration)
                if self._runtime_state and self._runtime_state.phase == "report_finalization":
                    active_candidate = self._current_candidate()
                elif len(active_candidate.evidence_bundle_ids) >= 2:
                    self._controller.complete_candidate(
                        self._runtime_state,
                        active_candidate.id,
                        reason="Structured evidence captured for this candidate; rotating to the next candidate.",
                    )
                active_candidate = self._current_candidate()
            elif loop_decision.status == "block":
                self._advance_candidate(loop_decision.message)
                active_candidate = self._current_candidate()

        if not observations:
            return ""
        if len(observations) == 1:
            return observations[0].split("=>\n", 1)[1]
        return "Batch Observation:\n" + "\n\n".join(observations)

    def _build_initial_message(self, context: Dict[str, Any]) -> str:
        self._recovery_context = context
        project_info = context["project_info"]
        config = context["config"]
        recon_data = context["recon_data"]
        project_profile = recon_data.get("project_profile", {})
        priority_paths = recon_data.get("priority_paths", recon_data.get("high_risk_areas", []))
        entry_points = recon_data.get("entry_points", [])
        target_files = context.get("target_files") or recon_data.get("audit_targets", {}).get("target_files", [])
        exclude_patterns = context.get("exclude_patterns") or recon_data.get("audit_targets", {}).get("exclude_patterns", [])
        focus_vulnerabilities = context.get("focus_vulnerabilities") or config.get("target_vulnerabilities", [])
        task_context = context.get("task_context") or context.get("task") or ""
        skill_guidance = build_finding_skill_route_message(context, context.get("skill_context", {}))
        message = f"""请直接审查项目源码，挖掘扫描器容易漏掉的漏洞。

项目信息：
- 名称: {project_info.get('name', 'unknown')}
- 根目录: {project_info.get('root', '.')}
- 语言: {json.dumps(project_profile.get('languages', []), ensure_ascii=False)}
- 框架: {json.dumps(project_profile.get('frameworks', []), ensure_ascii=False)}
- 数据库: {json.dumps(project_profile.get('databases', []), ensure_ascii=False)}

重点目录：
{json.dumps(priority_paths[:25], ensure_ascii=False, indent=2)}

入口点：
{json.dumps(entry_points[:20], ensure_ascii=False, indent=2)}

目标文件：
{json.dumps(target_files[:50], ensure_ascii=False, indent=2)}

排除规则：
{json.dumps(exclude_patterns[:20], ensure_ascii=False, indent=2)}

用户关注漏洞类型：
{json.dumps(focus_vulnerabilities[:20], ensure_ascii=False, indent=2)}

审计任务上下文：
{task_context or "未提供额外上下文"}

重点关注：
- 优先顺序: 目标文件 > 入口点 > priority_paths > 认证/授权链 > 敏感状态变更 > 多步业务流程
- 认证与对象访问控制
- 认证绕过与信任边界错误
- 业务逻辑漏洞
- 关键流程缺少校验
- 敏感状态变更缺少保护

        不要依赖 Scan Agent 输出，必须基于直接代码阅读和推理给出结论。"""
        message = message + "\n\n" + skill_guidance
        preloaded_skill_context = context.get("preloaded_skill_context")
        if preloaded_skill_context:
            prompt_block = preloaded_skill_context.to_prompt_block()
            if prompt_block:
                message = message + "\n\n" + prompt_block
        runtime_state = context.get("finding_runtime_state")
        if runtime_state:
            queue_preview = [
                {
                    "id": candidate.id,
                    "vuln_family": candidate.vuln_family,
                    "priority": candidate.priority,
                    "entry_points": candidate.entry_point_refs[:2],
                    "sink_refs": candidate.sink_refs[:2],
                    "worker_brief": runtime_state.worker_sessions.get(candidate.id).brief if runtime_state.worker_sessions.get(candidate.id) else "",
                    "worker_budget": runtime_state.worker_sessions.get(candidate.id).remaining_budget if runtime_state.worker_sessions.get(candidate.id) else 0,
                    "followup_rounds_left": runtime_state.worker_sessions.get(candidate.id).followup_rounds_left if runtime_state.worker_sessions.get(candidate.id) else 0,
                }
                for candidate in runtime_state.queue[:8]
            ]
            coverage_summary = {
                "strategy": runtime_state.plan.strategy,
                "phase": runtime_state.phase,
                "focus_vulnerabilities": runtime_state.plan.focus_vulnerabilities,
                "max_iterations": self.config.max_iterations,
                "report_finalization_threshold": self.REPORT_FINALIZATION_THRESHOLD,
                "entry_points": runtime_state.coverage.entry_points[:12],
                "uncovered_priority_paths": runtime_state.coverage.uncovered_priority_paths[:20],
                "authz_paths": runtime_state.coverage.authz_paths[:12],
                "active_candidate_id": runtime_state.active_candidate_id,
                "phase_transition_rules": [
                    "Switch to report_finalization when a closed exploit-chain candidate exists.",
                    "Switch to report_finalization when rounds_left <= 6.",
                    "While finalizing, keep the strongest current candidate and stop opening new candidates.",
                ],
            }
            message = (
                message
                + "\n\nCoverage-first runtime plan:\n"
                + json.dumps(coverage_summary, ensure_ascii=False, indent=2)
                + "\n\nInitial candidate queue:\n"
                + json.dumps(queue_preview, ensure_ascii=False, indent=2)
            )
        return message

    def _build_summary_prompt(self) -> str:
        return (
            "Stop now and deliver the final source-code vulnerability report. "
            "Return at most 3 highest-value findings that already have a closed exploit chain in prior observations. "
            "If dynamic verification has not been run, keep verdict='candidate' and explain the remaining evidence gap in verification_notes. "
            "Do not emit another Action. Return either 'Final Answer: {...}' or pure JSON only."
        )

    async def _recover_final_result(self) -> Dict[str, Any]:
        checkpoint_result = self._load_checkpoint_result()
        if checkpoint_result.get(self.output_key):
            return checkpoint_result

        synthesized = self._synthesizer.synthesize(self._runtime_state, self._evidence_store)
        if synthesized.get(self.output_key):
            return synthesized

        compact_evidence = self._build_compact_recovery_evidence()
        if not compact_evidence:
            return {
                self.output_key: [],
                "summary": self._build_timeout_summary(),
            }

        recovery_messages = [
            {
                "role": "system",
                "content": (
                    "You are finalizing a source-code vulnerability report from already collected audit evidence. "
                    "Do not ask for more tools. Return JSON only. "
                    "Each finding must include vulnerability_type, severity, title, description, file_path, line_start, line_end, "
                    "source, sink, suggestion, confidence, verdict, impact, cve_justification, verification_notes, exploit_chain, "
                    "poc, entry_point_refs, priority_path_refs, business_flow_notes, evidence_gaps."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Based only on the compact evidence below, produce the best supported final report. "
                    "Keep unsupported items out. Prefer 1-3 high-value findings.\n\n"
                    f"{compact_evidence}"
                ),
            },
        ]

        try:
            recovery_output, _ = await self.stream_llm_call(recovery_messages)
        except Exception:
            recovery_output = ""

        if recovery_output and recovery_output.strip() and "[LLM timeout]" not in recovery_output:
            parsed = self._parse_llm_response(recovery_output)
            if parsed.is_final and parsed.final_answer:
                return parsed.final_answer
            cleaned = re.sub(r"```json\\s*|```", "", recovery_output.strip())
            cleaned = re.sub(r"^Final Answer:\\s*", "", cleaned)
            parsed_json = self.json_parser.parse(cleaned, default={self.output_key: [], "summary": ""})
            if parsed_json.get(self.output_key):
                return parsed_json

        return {
            self.output_key: [],
            "summary": self._build_timeout_summary(),
        }

    def _build_fallback_result(self) -> Dict[str, Any]:
        checkpoint_result = self._load_checkpoint_result()
        if checkpoint_result.get(self.output_key):
            return checkpoint_result
        synthesized = self._synthesizer.synthesize(self._runtime_state, self._evidence_store)
        if synthesized.get(self.output_key):
            return synthesized
        return {
            self.output_key: [],
            "summary": self._build_timeout_summary(),
        }

    def _build_compact_recovery_evidence(self) -> str:
        highlights: List[str] = []
        synthesized = self._synthesizer.synthesize(self._runtime_state, self._evidence_store)
        if synthesized.get(self.output_key):
            highlights.append(
                "[Synthesized Evidence] "
                + json.dumps(synthesized.get(self.output_key, [])[:2], ensure_ascii=False)
            )
        for index, step in enumerate(self._steps, start=1):
            if step.thought and self._is_high_signal_thought(step.thought):
                highlights.append(f"[Thought #{index}] {step.thought.strip()[:500]}")
            if step.action in {"read_file", "search_code", "dataflow_analysis", "function_context"} and step.observation:
                observation = self._trim_observation(step.observation)
                if observation:
                    highlights.append(f"[Observation #{index}::{step.action}] {observation}")
        if not highlights:
            return ""
        return "\n\n".join(highlights[-6:])

    def _build_timeout_summary(self) -> str:
        recent_artifacts: List[str] = []
        seen = set()
        for step in reversed(self._steps):
            action = getattr(step, "action", "") or ""
            action_input = getattr(step, "action_input", {}) or {}
            candidate_values: List[str] = []
            if action == "read_file":
                file_path = str(action_input.get("file_path", "")).strip()
                if file_path:
                    candidate_values.append(file_path)
            elif action == "read_many_files":
                file_paths = action_input.get("file_paths")
                if isinstance(file_paths, list):
                    candidate_values.extend(str(path).strip() for path in file_paths if str(path).strip())
            elif action == "search_code":
                keyword = str(action_input.get("keyword", "")).strip()
                if keyword:
                    candidate_values.append(f"search:{keyword}")

            for value in candidate_values:
                if value and value not in seen:
                    seen.add(value)
                    recent_artifacts.append(value)
                if len(recent_artifacts) >= 6:
                    break
            if len(recent_artifacts) >= 6:
                break

        if recent_artifacts:
            artifact_text = "; ".join(recent_artifacts)
            return (
                f"Finalization timed out after {self._iteration} reasoning steps and {self._tool_calls} tool calls. "
                f"The audit reached these hotspots before stopping: {artifact_text}. "
                "No CVE-grade finding could be finalized from the collected evidence before the LLM timed out. "
                "This fallback summary reflects the latest audited code paths and should be reviewed to continue the investigation. "
                "Finding did not produce a compliant Final Answer."
            )

        return (
            f"Finalization timed out after {self._iteration} reasoning steps and {self._tool_calls} tool calls. "
            "The audit did not reach a compliant CVE-grade conclusion before the LLM timed out. "
            "This fallback summary indicates an incomplete finalization rather than a confirmed clean result. "
            "Finding did not produce a compliant Final Answer."
        )

    def _trim_observation(self, observation: str, max_length: int = 1200) -> str:
        text = str(observation or "").strip()
        if not text:
            return ""
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:max_length]

    def _is_high_signal_thought(self, thought: str) -> bool:
        lowered = (thought or "").lower()
        if any(token in thought for token in ("漏洞", "风险", "利用链", "存在")):
            return True
        return any(keyword in lowered for keywords in self.RECOVERY_KEYWORDS.values() for keyword in keywords)

    def _heuristic_findings_from_steps(self) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        recent_context: Optional[Dict[str, Any]] = None
        seen_keys = set()

        for step in self._steps:
            if step.action in {"read_file", "function_context"} and step.observation:
                extracted = self._extract_file_context(step.observation)
                if extracted:
                    recent_context = extracted

            thought = (step.thought or "").strip()
            vuln_type = self._infer_vulnerability_type(thought)
            if not vuln_type:
                continue

            file_context = recent_context or {}
            file_path = file_context.get("file_path", "")
            line_start = file_context.get("line_start", 1)
            line_end = file_context.get("line_end", line_start)
            source, sink = self._infer_source_sink(file_context.get("snippet", ""), thought, vuln_type)
            title = self._normalize_title(vuln_type, thought)
            key = (vuln_type, file_path, line_start, line_end)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            findings.append(
                {
                    "vulnerability_type": vuln_type,
                    "severity": self.RECOVERY_SEVERITY.get(vuln_type, "medium"),
                    "title": title,
                    "description": thought,
                    "file_path": file_path,
                    "line_start": line_start,
                    "line_end": line_end,
                    "code_snippet": file_context.get("snippet", "")[:800],
                    "source": source,
                    "sink": sink,
                    "suggestion": "Add strict validation, authorization, and sink-side protection before the dangerous operation.",
                    "confidence": 0.74,
                    "verdict": "candidate",
                    "impact": "Potentially exploitable based on the observed source-to-sink chain in the audited code path.",
                    "cve_justification": "The code path shows attacker-controlled input reaching a dangerous operation without sufficient controls.",
                    "verification_notes": "Recovered from high-signal audit steps after the final report stage timed out. Dynamic verification is still recommended.",
                    "exploit_chain": self._build_heuristic_exploit_chain(file_context, thought),
                    "poc": self._build_heuristic_poc(vuln_type, source, sink, file_path),
                    "entry_point_refs": self._extract_entry_point_refs(thought, file_context),
                    "priority_path_refs": [file_path] if file_path else [],
                    "business_flow_notes": [thought],
                    "evidence_gaps": ["recovered_after_llm_timeout"],
                }
            )

        return findings[:3]

    def _extract_file_context(self, observation: str) -> Optional[Dict[str, Any]]:
        text = str(observation or "")
        file_match = re.search(r"文件:\s*(.+)", text)
        if not file_match:
            return None
        line_match = re.search(r"行数:\s*(\d+)(?:-(\d+))?", text)
        snippet_match = re.search(r"```[a-zA-Z0-9_]*\n(.*?)```", text, re.DOTALL)
        line_start = int(line_match.group(1)) if line_match else 1
        line_end = int(line_match.group(2) or line_start) if line_match else line_start
        return {
            "file_path": file_match.group(1).strip(),
            "line_start": line_start,
            "line_end": line_end,
            "snippet": (snippet_match.group(1).strip() if snippet_match else text[:800]),
        }

    def _infer_vulnerability_type(self, thought: str) -> Optional[str]:
        lowered = (thought or "").lower()
        for vuln_type, keywords in self.RECOVERY_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return vuln_type
        return None

    def _normalize_title(self, vuln_type: str, thought: str) -> str:
        first_sentence = re.split(r"[。.!?]", thought or "", maxsplit=1)[0].strip()
        if first_sentence:
            return first_sentence[:120]
        return vuln_type.replace("_", " ").title()

    def _infer_source_sink(self, snippet: str, thought: str, vuln_type: str) -> tuple[str, str]:
        text = f"{thought}\n{snippet}"
        source = ""
        sink = ""
        if "fileUrl" in text:
            source = "user-controlled fileUrl parameter"
        elif "bizPath" in text:
            source = "user-controlled bizPath parameter"
        elif "jsonObject.get" in text:
            getter = re.search(r'jsonObject\.get\("([^"]+)"\)', text)
            if getter:
                source = f'user-controlled parameter {getter.group(1)}'

        if "openConnection" in text:
            sink = "java.net.URL.openConnection()"
        elif "new File(" in text:
            sink = "filesystem path construction via new File(...)"
        elif "executeQuery" in text or "prepareStatement" in text:
            sink = "SQL execution sink"

        if not source:
            source = f"attacker-controlled input inferred from the {vuln_type} code path"
        if not sink:
            sink = f"dangerous {vuln_type} sink inferred from audited code"
        return source, sink

    def _build_heuristic_exploit_chain(self, file_context: Dict[str, Any], thought: str) -> List[Dict[str, Any]]:
        location = file_context.get("file_path", "")
        line_start = file_context.get("line_start")
        location_ref = f"{location}:{line_start}" if location and line_start else location
        return [
            {
                "step": 1,
                "location": location_ref,
                "description": "User-controlled input reaches the audited entry path.",
                "data_state": "tainted input",
                "bypass_reason": "",
            },
            {
                "step": 2,
                "location": location_ref,
                "description": thought[:220],
                "data_state": "input flows into dangerous operation without sufficient protection",
                "bypass_reason": "",
            },
        ]

    def _build_heuristic_poc(self, vuln_type: str, source: str, sink: str, file_path: str) -> Dict[str, Any]:
        return {
            "description": f"Recovered candidate {vuln_type} PoC from source-code analysis.",
            "preconditions": ["Reach the vulnerable endpoint or code path"],
            "steps": [
                {
                    "step": 1,
                    "action": "Send a crafted request with attacker-controlled input",
                    "request": source,
                    "expected_response": "Application accepts the attacker-controlled value",
                },
                {
                    "step": 2,
                    "action": "Trigger the dangerous operation",
                    "request": sink,
                    "expected_response": "Dangerous sink is reached without the expected protection",
                },
            ],
            "payload": source,
            "impact": f"Potentially exploitable {vuln_type} in {file_path or 'the audited code path'}",
            "cve_justification": "The vulnerability is reachable from attacker-controlled input and impacts a security boundary.",
        }

    def _extract_entry_point_refs(self, thought: str, file_context: Dict[str, Any]) -> List[str]:
        refs: List[str] = []
        refs.extend(re.findall(r"(/[\w./:-]+)", thought or ""))
        if file_context.get("file_path"):
            refs.append(file_context["file_path"])
        return list(dict.fromkeys(refs))

    def _postprocess_result(self, raw_result: Dict[str, Any]) -> Dict[str, Any]:
        processed = super()._postprocess_result(raw_result)
        enriched = []
        for finding in processed.get("findings", []):
            evidence_gaps = list(finding.get("evidence_gaps", []))
            entry_point_refs = finding.get("entry_point_refs", [])
            priority_path_refs = finding.get("priority_path_refs", [])
            business_flow_notes = finding.get("business_flow_notes", [])

            if not entry_point_refs:
                evidence_gaps.append("missing_entry_point_reference")
            if not priority_path_refs and finding.get("file_path"):
                priority_path_refs = [finding["file_path"]]

            if evidence_gaps:
                finding["confidence"] = round(min(float(finding.get("confidence", 0.7)), 0.8), 2)
                finding["needs_verification"] = True

            finding["evidence_gaps"] = sorted(set(evidence_gaps))
            finding["entry_point_refs"] = entry_point_refs
            finding["priority_path_refs"] = priority_path_refs
            finding["business_flow_notes"] = business_flow_notes
            enriched.append(finding)

        processed["findings"] = enriched
        return processed

    def _build_handoff(self, processed_result):
        handoff = super()._build_handoff(processed_result)
        if not handoff:
            return None

        entry_point_refs = []
        priority_path_refs = []
        evidence_gaps = []
        business_flow_notes = []
        for finding in processed_result.get("findings", []):
            entry_point_refs.extend(finding.get("entry_point_refs", []))
            priority_path_refs.extend(finding.get("priority_path_refs", []))
            evidence_gaps.extend(finding.get("evidence_gaps", []))
            business_flow_notes.extend(finding.get("business_flow_notes", []))

        handoff.context_data.update(
            {
                "entry_point_refs": list(dict.fromkeys(entry_point_refs)),
                "priority_path_refs": list(dict.fromkeys(priority_path_refs)),
                "evidence_gaps": list(dict.fromkeys(evidence_gaps)),
                "business_flow_notes": list(dict.fromkeys(business_flow_notes)),
            }
        )
        return handoff
