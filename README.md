# AuditAI

AuditAI 是一个面向代码安全审计场景的 AI 协作平台，提供项目导入、规则管理、Agent 审计、报告模板和技能库管理等能力，适合用于代码安全分析、漏洞研判与报告产出。

## 快速开始

### 方式一：Docker Compose

1. 准备后端配置文件：

```bash
cp backend/env.example backend/.env
```

2. 在 `backend/.env` 中至少补充以下配置：

```env
LLM_PROVIDER=openai
LLM_API_KEY=your-api-key
LLM_MODEL=gpt-4o-mini
```

3. 启动服务：

```bash
docker compose up -d --build
```

4. 打开服务：

- 前端：`http://localhost:3000`
- 后端 API：`http://localhost:8000/api/v1`
- Adminer：`http://localhost:8080`

### 方式二：本地开发

前端：

```bash
cd frontend
npm install
npm run dev
```

后端：

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 目录结构

```text
AuditAI/
├─ backend/                  后端服务与 API
├─ frontend/                 前端界面
├─ docker/                   Docker 相关配置
├─ skill_library/            技能库
├─ report_template_library/  报告模板库
├─ rules/                    审计规则
└─ docs/                     项目文档
```

## 常用配置

- `backend/.env`：后端核心配置
- `frontend/.env.example`：前端环境变量示例
- `HOST_PROJECT_ROOT`：可选，用于前端展示宿主机上的技能库/模板绝对路径
- `VITE_HOST_PROJECT_ROOT`：可选，与上面的宿主机路径展示能力配套
- `SANDBOX_IMAGE`：默认使用 `auditai-sandbox:latest`

## GitLab 发布建议

推送到 GitLab 前，建议先完成以下操作：

1. 修改远程仓库地址为你的 GitLab 仓库。
2. 检查 `backend/.env` 中是否存在真实密钥，避免提交敏感信息。
3. 首次部署优先使用 `docker compose up -d --build`，确保本地构建出 `auditai-sandbox:latest`。

## 说明

当前仓库默认以 `AuditAI` 品牌运行与展示；如果你需要继续扩展品牌物料、部署脚本或 GitLab CI，我可以在下一步继续帮你整理。
