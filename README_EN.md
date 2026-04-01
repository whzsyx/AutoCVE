# AuditAI

AuditAI is an AI-assisted code security auditing platform for project analysis, agent-driven review, rule management, skill management, and report generation.

## Quick Start

```bash
cp backend/env.example backend/.env
docker compose up -d --build
```

Frontend: `http://localhost:3000`  
Backend API: `http://localhost:8000/api/v1`

## Key Paths

- `backend/` backend service
- `frontend/` web UI
- `docker/` container assets
- `skill_library/` local skills
- `report_template_library/` report templates

## Notes

- Configure your LLM settings in `backend/.env`
- `HOST_PROJECT_ROOT` and `VITE_HOST_PROJECT_ROOT` are optional
- The default sandbox image is `auditai-sandbox:latest`
