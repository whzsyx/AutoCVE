import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.one_click_cve.task_queue import (
    ONE_CLICK_CVE_BATCH_JOB_NAME,
    OneClickCveBatchQueue,
    should_use_one_click_cve_worker_queue,
)


class _FakeArqPool:
    def __init__(self):
        self.jobs = []
        self.closed = False

    async def enqueue_job(self, function, *args, **kwargs):
        self.jobs.append((function, args, kwargs))
        return object()

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_one_click_cve_batch_queue_enqueues_batch_payload():
    pool = _FakeArqPool()
    queue = OneClickCveBatchQueue(arq_pool=pool, queue_name="one-click:q")

    await queue.enqueue("batch-1")

    assert pool.jobs == [
        (
            ONE_CLICK_CVE_BATCH_JOB_NAME,
            ("batch-1",),
            {"_job_id": "one-click-cve:batch-1", "_queue_name": "one-click:q"},
        )
    ]


def test_should_use_one_click_cve_worker_queue_only_for_worker_mode(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ONE_CLICK_CVE_EXECUTION_MODE", "worker")
    assert should_use_one_click_cve_worker_queue() is True

    monkeypatch.setattr(settings, "ONE_CLICK_CVE_EXECUTION_MODE", "inline")
    assert should_use_one_click_cve_worker_queue() is False
