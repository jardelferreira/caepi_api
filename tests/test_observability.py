from types import SimpleNamespace
from unittest.mock import patch

from app.workers import celery_app as celery_module


def test_celery_task_events_are_enabled():
    assert celery_module.celery_app.conf.task_send_sent_event is True
    assert celery_module.celery_app.conf.worker_send_task_events is True


def test_task_context_extracts_ca_number_from_positional_args():
    assert celery_module._task_context(("498",), {}) == {"ca_number": "498"}


def test_task_context_extracts_ca_number_from_kwargs():
    assert celery_module._task_context((), {"ca_number": "498"}) == {"ca_number": "498"}


def test_task_context_does_not_log_full_payload():
    assert celery_module._task_context(("9" * 100,), {}) == {"ca_number": "9" * 32}
