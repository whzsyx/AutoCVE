# AIAudit

AIAudit 是一个面向代码安全审计与 CVE 挖掘的 Agent 化审计平台。它将项目管理、模型配置、Agent 工作流、工具调用、Skills、审计会话和漏洞管理整合到一个可视化产品中，帮助安全研究员从“上传项目”快速走到“发现、验证、整理漏洞”。


## 产品亮点

- **Agent 化审计工作流**：内置 Recon、Scan、Triage、Finding、Verification 等审计节点，按固定工作流推进项目分析。
- **可视化审计过程**：实时展示 Agent 阶段、活动日志、工具调用、思考过程、漏洞发现和执行树。
- **Finding Agent 深度分析**：基于 ReAct Runtime、工具编排、终止校验和 nudge 机制进行代码级漏洞挖掘。
- **项目上传与仓库导入**：支持上传 ZIP、导入仓库、查看文件树和读取源码内容。
- **Skills 能力扩展**：支持为不同 Agent 绑定不同 Skill，并在审计过程中按需加载审计方法和参考资料。
- **漏洞结果管理**：统一管理漏洞、人工复核状态、CVE 状态和报告内容。
- **模型方案配置**：支持全局模型配置，也支持为不同 Agent 使用不同模型方案。


## 快速开始

```bash
git clone <你的仓库地址>
cd AIAudit
docker compose up -d --build
```

启动完成后访问：

- 前端：`http://localhost:3000`
- 后端 API：`http://localhost:8000/api/v1`
- API 文档：`http://localhost:8000/docs`

首次进入系统后，可在「系统设置」中配置模型方案和各 Agent 使用的模型。

## 文档

- [用户使用手册](./docs/USER_GUIDE.md)
- [产品架构设计](./docs/ARCHITECTURE_DESIGN.md)
- [接口文档](./docs/API_DOCUMENTATION.md)

## 功能模块

| 模块 | 说明 |
| --- | --- |
| 仪表盘 | 查看项目、任务、漏洞和审计概览 |
| 项目管理 | 上传 ZIP、导入仓库、查看项目文件 |
| Agent 审计 | 创建审计任务，查看 Agent 工作流和实时活动日志 |
| 审计会话 | 基于审计上下文继续追问和拓展分析 |
| 漏洞管理 | 查看漏洞详情、人工复核、维护 CVE 状态 |
| 一键 CVE | 批量创建 CVE 挖掘任务并跟踪候选结果 |
| Skills 管理 | 导入、编辑、绑定不同 Agent 的审计技能 |
| 系统设置 | 配置模型方案、工作流开关和用户 Token |

## 项目结构

```text
AIAudit/
├─ backend/                  FastAPI 后端、Agent Runtime、业务服务
├─ frontend/                 React + Vite 前端
├─ docker/                   Docker 与 sandbox 配置
├─ docs/                     使用手册、架构设计、接口文档
├─ projects/                 上传或导入后的项目工作区
├─ skill_library/            Skills 库
├─ report_template_library/  报告模板库
└─ rules/                    审计规则
```

## 说明

本项目默认以 AIAudit 品牌展示。生产部署前请检查密钥、Token、数据库密码和模型配置，避免将真实敏感信息提交到仓库。
