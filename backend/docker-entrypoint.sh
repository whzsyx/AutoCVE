#!/bin/bash
set -e

echo "🚀 AuditAI 后端启动中..."

# 等待 PostgreSQL 就绪
echo "⏳ 等待数据库连接..."
max_retries=30
retry_count=0

while [ $retry_count -lt $max_retries ]; do
    if .venv/bin/python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
import os

async def check_db():
    engine = create_async_engine(os.environ.get('DATABASE_URL', ''))
    try:
        async with engine.connect() as conn:
            await conn.execute(text('SELECT 1'))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()

from sqlalchemy import text
exit(0 if asyncio.run(check_db()) else 1)
" 2>/dev/null; then
        echo "✅ 数据库连接成功"
        break
    fi

    retry_count=$((retry_count + 1))
    echo "   重试 $retry_count/$max_retries..."
    sleep 2
done

if [ $retry_count -eq $max_retries ]; then
    echo "❌ 无法连接到数据库，请检查 DATABASE_URL 配置"
    exit 1
fi

# 运行数据库迁移
echo "📦 执行数据库迁移..."
.venv/bin/alembic upgrade head

echo "✅ 数据库迁移完成"

# 启动 uvicorn
echo "🌐 启动 API 服务..."
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
