import pytest

from app.services.finding_runtime.resume_queue import (
    AUDIT_SESSION_RESUME_JOB_NAME,
    AuditSessionResumeQueue,
)


class FakeArqPool:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, function, *args, **kwargs):
        self.jobs.append((function, args, kwargs))
        return object()


@pytest.mark.asyncio
async def test_resume_queue_uses_session_and_token_idempotency_key(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ONE_CLICK_CVE_QUEUE_NAME", "resume:q")
    pool = FakeArqPool()
    queue = AuditSessionResumeQueue(arq_pool=pool)

    await queue.enqueue("session-1", "token-1")

    assert pool.jobs == [
        (
            AUDIT_SESSION_RESUME_JOB_NAME,
            ("session-1", "token-1"),
            {
                "_job_id": "audit-session-resume:session-1:token-1",
                "_queue_name": "resume:q",
            },
        )
    ]
