# 流程调试设计文档

**日期**: 2026-03-18

**目标**: 为 DeepAudit 新增独立的“流程调试”菜单页，用于按任务查看完整 Agent 审计轨迹，包括模型启动提示词、ReAct 思考链、工具调用输入输出、Agent 间 handoff、以及原始事件数据，方便后续调试提示词、技能、模型配置和工具链协作效果。

## 背景

当前 AgentAudit 页面已经能够显示活动日志、Agent 树、统计信息，并通过事件流消费一部分运行时事件，例如：

- `llm_thought`
- `tool_call`
- `tool_result`
- `phase_start`
- `phase_complete`
- `finding`

但这些信息更偏向“运行状态展示”，并不能满足以下调试需求：

- 查看每个 Agent 启动时实际发送给模型的 `system` 和 `user` 内容
- 查看完整 ReAct 过程中的 `thought / action / observation`
- 查看工具调用的原始输入和原始输出
- 查看 Agent 之间完整的 handoff 内容
- 查看整个任务中模型、工具、Agent 通信的完整上下文
- 以更适合调试的结构化方式进行可视化，而不是混在主审计日志里

因此本次改造新增独立调试台，而不是继续堆叠在 AgentAudit 主页面中。

## 产品设计

### 菜单与路由

新增独立菜单项：

- 名称：`流程调试`
- 路由：`/flow-debugger`

该页面不直接依赖当前审计页面状态，而是支持独立选择任务并查看调试轨迹。

### 页面结构

采用“独立菜单页 + 双栏调试台”的结构。

#### 顶部任务选择区

功能：

- 任务下拉选择
- 状态筛选：`running / completed / failed / cancelled`
- 时间范围筛选
- 调试模式标识
- 手动刷新

用途：

- 支持切换不同任务查看轨迹
- 未来支持对比不同运行结果

#### 左栏：流程时间线

按 Agent 节点和时间顺序展示完整执行链：

- `orchestrator`
- `recon`
- `scan`
- `triage`
- `finding`
- `verification`

每个 Agent 节点下展示事件条目，例如：

- agent start
- prompt system
- prompt user
- react thought
- react action
- tool call start
- tool call input
- tool call output
- react observation
- handoff out
- handoff in
- final answer
- agent complete

交互：

- 点击任意事件，右栏显示完整详情
- 支持只看指定 Agent
- 支持只看某类事件（如 prompt、tool、handoff）

#### 右栏：调试详情

展示当前选中事件的完整信息：

- 基本信息：任务、Agent、provider、model、迭代号、时间、事件类型
- 原始 `system` / `user` / `assistant` 消息
- ReAct 思考内容
- 工具调用参数
- 工具输出全文
- handoff payload 全文
- 原始 JSON 数据
- 复制按钮

#### 底部：通信关系视图

显示 Agent 间通信关系图，重点是 handoff：

- `orchestrator -> recon`
- `recon -> scan`
- `scan -> triage`
- `recon -> finding`
- `triage/finding -> verification`

点击某条连接时，右栏跳转到对应 handoff 事件详情。

## 后端设计

### 设计原则

1. 流程调试为显式功能，不与普通活动日志混为一谈
2. 调试数据结构统一，前后端共享明确协议
3. 普通任务也应保留基础事件，但完整 prompt / handoff / observation 采集由调试模式控制
4. 模型测试链路和真实 Agent 运行链路使用同一套调试事件结构，保证可比性

### 新增调试模式

在 Agent 审计任务创建或执行上下文中增加：

- `debug_mode: bool`

用途：

- 控制是否采集完整提示词、完整响应、完整 handoff、完整工具输入输出
- 避免默认日志量和敏感内容暴涨

### 新增调试事件协议

新增统一事件类型，作为调试视图的数据基础：

- `agent_start`
- `prompt_system`
- `prompt_user`
- `prompt_assistant`
- `react_thought`
- `react_action`
- `react_observation`
- `tool_call_start`
- `tool_call_input`
- `tool_call_output`
- `tool_call_end`
- `handoff_out`
- `handoff_in`
- `model_response_raw`
- `agent_complete`

### 统一事件字段

每条调试事件至少包含：

- `task_id`
- `agent_name`
- `agent_type`
- `event_type`
- `sequence`
- `timestamp`
- `iteration`
- `provider`
- `model`
- `payload`
- `debug_group_id`

其中：

- `payload` 保存完整正文
- `debug_group_id` 用于把一次 ReAct 循环、一组工具调用、或一对 handoff 关联起来

### 后端主要改造点

#### 1. EventManager 扩展

文件：

- `backend/app/services/agent/event_manager.py`

新增能力：

- 新增调试事件方法
- 支持完整 prompt 和完整 payload 入库/入流
- 保证 sequence 连续可排序

#### 2. Agent 运行链路补齐

涉及：

- `backend/app/services/agent/agents/recon.py`
- `backend/app/services/agent/agents/analysis_workflow.py`
- `backend/app/services/agent/agents/orchestrator.py`
- `backend/app/services/agent/agents/verification.py`

新增采集点：

- Agent 开始执行时记录 `agent_start`
- 构造 system prompt 后记录 `prompt_system`
- 构造首轮 user 消息后记录 `prompt_user`
- 进入每轮 ReAct 解析时记录 `react_thought`
- 识别到动作时记录 `react_action`
- 工具执行后记录 `tool_call_input` / `tool_call_output`
- 生成 observation 后记录 `react_observation`
- Agent 之间 handoff 时记录 `handoff_out` 和 `handoff_in`
- 最终输出前记录 `model_response_raw`
- 完成时记录 `agent_complete`

#### 3. Agent 间 handoff 显式事件化

重点文件：

- `backend/app/services/agent/agents/orchestrator.py`
- `backend/app/services/agent/agents/recon.py`
- `backend/app/services/agent/agents/scan.py`
- `backend/app/services/agent/agents/verification.py`

要求：

- handoff 不只存在于结果对象里
- 必须作为调试事件完整记录 payload
- 支持后续前端按连接线查看完整通信内容

#### 4. 调试查询 API

新增接口：

- `GET /api/v1/agent-tasks/{task_id}/debug-trace`
- `GET /api/v1/agent-tasks/debug-tasks`

建议返回：

- 任务列表（供页面选择）
- 指定任务的完整调试事件流
- 已聚合的 Agent 节点信息
- handoff edge 信息

### 模型测试链路对齐

文件：

- `backend/app/api/v1/endpoints/config.py`

要求：

- `/api/v1/config/test-agent-model` 的调试事件结构应与真实 Agent 启动流程一致
- 测试链路也记录：
  - `prompt_system`
  - `prompt_user`
  - `model_response_raw`
  - Skills 元数据注入结果

### 数据安全

完整提示词和工具输出可能包含敏感内容，因此：

- 普通 UI 默认不展示
- 仅流程调试页读取
- 后续如需要，可增加角色控制或脱敏配置

## 前端设计

### 新页面

新增页面：

- `frontend/src/pages/FlowDebugger.tsx`

页面风格延续当前浅色审计控制台风格，但更加偏向“证据台 / 调试台”。

视觉方向：

- 暖白背景
- 浅砂色边框
- 焦橙高亮当前事件
- 冷灰蓝标识系统 / prompt / tool / handoff 等不同类别
- 细颗粒网格背景，强调调试和审查感

### 核心组件建议

- `DebugTaskSelector`
- `DebugTimeline`
- `DebugEventList`
- `DebugDetailPanel`
- `DebugFlowGraph`

### 页面交互

1. 进入页面默认加载最近任务列表
2. 选择任务后拉取调试轨迹
3. 左侧按 Agent 分组显示时间线
4. 点击任意事件，右侧显示完整详情
5. 切换筛选：
   - 只看 prompts
   - 只看 tools
   - 只看 handoffs
   - 只看某个 Agent

### 与当前 AgentAudit 页的边界

AgentAudit 页面继续承担：

- 运行监控
- 活动日志
- Agent 树
- 导出报告

流程调试页承担：

- 完整 prompt 可视化
- 完整 ReAct 轨迹
- 完整 tool I/O
- 完整 handoff 通信
- 原始 JSON 审查

## 推荐实现顺序

### 第一阶段：打通调试数据链路

1. 新增 `debug_mode`
2. 扩展 `EventManager`
3. 在 orchestrator / recon / analysis_workflow / verification 中埋点
4. 新增 `debug-trace` API

### 第二阶段：上线独立调试页

1. 新增路由和菜单
2. 实现任务选择器
3. 实现左栏时间线
4. 实现右栏详情面板

### 第三阶段：增强可视化和可用性

1. 增加通信关系图
2. 增加 JSON 视图
3. 增加复制和筛选
4. 补充模型测试链路对齐

## 验收标准

改造完成后，以下能力必须满足：

1. 菜单栏存在独立的 `流程调试`
2. 进入页面后可以先选择任务
3. 任务加载后能按 Agent 查看完整调试时间线
4. 可以看到每个 Agent 的完整 `system` / `user` 提示词
5. 可以看到完整 ReAct 思考、动作、观察
6. 可以看到工具调用输入和输出全文
7. 可以看到 Agent 间 handoff 的完整 payload
8. 可以看到 provider / model / iteration 等上下文
9. 模型测试链路与真实审计链路在调试事件结构上保持一致
10. 普通 AgentAudit 页面不被调试视图污染

## 不在本次范围内

本次不包含：

- 调试轨迹跨任务差异对比
- 提示词版本管理
- 自动回放 Agent 执行过程
- 调试事件导出为外部文件格式

这些能力可以后续在该调试台上继续扩展。
