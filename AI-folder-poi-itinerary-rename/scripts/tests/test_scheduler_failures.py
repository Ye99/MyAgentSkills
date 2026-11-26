"""The shared scheduler isolates sessions, retries failed keys, and stops on a dead backend."""
from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest

import rename_folder_by_ai_itinerary as mod


def _cluster(lat: float = 63.5) -> mod.LocationCluster:
    return mod.LocationCluster(points=[mod.MediaPoint("a.jpg", lat, -19.5, datetime(2025, 7, 24, 9, 0))])


def _scheduler() -> mod.SharedInferenceScheduler:
    handle = mod.OpencodeServerHandle(worker_id=0, port=1, url="http://127.0.0.1:1", process=None)
    return mod.SharedInferenceScheduler(
        [handle], opencode_timeout_sec=1, opencode_retries=1, opencode_backoff_sec=0.0, opencode_model=None,
    )


def test_each_inference_gets_a_fresh_session():
    seen: list[Any] = []

    def fake_infer(*_args, opencode_session_id=None, diagnostics=None, **_kw):
        seen.append(opencode_session_id)
        diagnostics["opencode_session_id"] = f"ses{len(seen)}"
        return {"landmark": "A", "country": "ISL"}

    scheduler = _scheduler()
    with patch.object(mod, "infer_landmark_info", side_effect=fake_infer):
        scheduler.submit((1.0, 1.0), _cluster(1.0)).result(timeout=5)
        scheduler.submit((2.0, 2.0), _cluster(2.0)).result(timeout=5)
        scheduler.shutdown()
    assert seen == [None, None]


def test_a_failed_key_is_retried_by_a_later_submit():
    outcomes = [mod.InferenceExhaustedError("boom", attempt_count=1), {"landmark": "A", "country": "ISL"}]

    def fake_infer(*_args, **_kw):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    scheduler = _scheduler()
    with patch.object(mod, "infer_landmark_info", side_effect=fake_infer):
        with pytest.raises(mod.InferenceExhaustedError):
            scheduler.submit((1.0, 1.0), _cluster()).result(timeout=5)
        result = scheduler.submit((1.0, 1.0), _cluster()).result(timeout=5)
        scheduler.shutdown()
    assert result.info["landmark"] == "A"


def test_consecutive_failures_stop_further_inference():
    calls: list[int] = []

    def fake_infer(*_args, **_kw):
        calls.append(1)
        raise mod.InferenceExhaustedError("server down", attempt_count=1)

    scheduler = _scheduler()
    with patch.object(mod, "infer_landmark_info", side_effect=fake_infer):
        errors = []
        for i in range(6):
            with pytest.raises(mod.InferenceExhaustedError) as info:
                scheduler.submit((float(i), 0.0), _cluster(float(i))).result(timeout=5)
            errors.append(str(info.value))
        scheduler.shutdown()
    assert len(calls) == mod.MAX_CONSECUTIVE_INFERENCE_FAILURES
    assert "consecutive" in errors[-1]


def test_a_success_resets_the_failure_count():
    results = iter([False, False, True, False, False, True])

    def fake_infer(*_args, **_kw):
        if next(results):
            return {"landmark": "A", "country": "ISL"}
        raise mod.InferenceExhaustedError("flaky", attempt_count=1)

    scheduler = _scheduler()
    ok = 0
    with patch.object(mod, "infer_landmark_info", side_effect=fake_infer):
        for i in range(6):
            try:
                scheduler.submit((float(i), 0.0), _cluster(float(i))).result(timeout=5)
                ok += 1
            except mod.InferenceExhaustedError:
                pass
        scheduler.shutdown()
    assert ok == 2
