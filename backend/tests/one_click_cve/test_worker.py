import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.one_click_cve.task_queue import ONE_CLICK_CVE_BATCH_JOB_NAME
from app.services.finding_runtime.resume_queue import AUDIT_SESSION_RESUME_JOB_NAME
from app.worker.one_click_cve_worker import WorkerSettings, decode_batch_payload


def test_decode_batch_payload_reads_json_batch_id():
    assert decode_batch_payload('{"batch_id": "batch-1"}') == "batch-1"


@pytest.mark.parametrize("payload", ["", "{}", "{bad json", '{"batch_id": ""}'])
def test_decode_batch_payload_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        decode_batch_payload(payload)


def test_one_click_cve_worker_settings_use_arq_queue():
    from app.core.config import settings

    assert WorkerSettings.queue_name == settings.ONE_CLICK_CVE_QUEUE_NAME
    assert WorkerSettings.max_jobs == settings.ONE_CLICK_CVE_WORKER_CONCURRENCY
    assert WorkerSettings.job_timeout == settings.ONE_CLICK_CVE_WORKER_JOB_TIMEOUT_SECONDS
    assert WorkerSettings.max_tries == settings.ONE_CLICK_CVE_WORKER_MAX_TRIES
    assert WorkerSettings.functions[0].name == ONE_CLICK_CVE_BATCH_JOB_NAME
    assert WorkerSettings.functions[1].name == AUDIT_SESSION_RESUME_JOB_NAME
