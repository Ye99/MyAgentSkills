"""Opencode servers start only when an inference is needed, and stop after."""
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import rename_folder_by_ai_itinerary as mod


def _handle(worker_id: int) -> mod.OpencodeServerHandle:
    return mod.OpencodeServerHandle(worker_id=worker_id, port=4100 + worker_id,
                                    url=f"http://127.0.0.1:{4100 + worker_id}", process=None)


def _cluster() -> mod.LocationCluster:
    from datetime import datetime
    point = mod.MediaPoint("a.jpg", 63.5, -19.5, datetime(2025, 7, 24, 9, 0))
    return mod.LocationCluster(points=[point])


def _scheduler(factory) -> mod.SharedInferenceScheduler:
    return mod.SharedInferenceScheduler(
        None, server_factory=factory, opencode_timeout_sec=1, opencode_retries=1,
        opencode_backoff_sec=0.0, opencode_model=None,
    )


def test_tree_run_starts_no_server_when_no_folder_needs_inference():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "2025" / "2025_07_22").mkdir(parents=True)
        (root / "2025" / "2025_07_23").mkdir(parents=True)
        start = MagicMock(side_effect=_handle)
        with patch.object(mod, "start_opencode_server", start), \
             patch.object(mod, "rename_folder_from_itinerary",
                          side_effect=lambda folder, **_: {"folder_path": str(folder),
                                                           "status": "skipped-no-landmark"}):
            summary = mod.process_folder_tree(root, apply=False, inference_workers=3)
    assert start.call_count == 0
    assert summary["status"] == "completed"


def test_scheduler_starts_servers_on_first_submit_and_stops_them_on_shutdown():
    servers = [_handle(0), _handle(1)]
    factory = MagicMock(return_value=servers)
    scheduler = _scheduler(factory)
    assert factory.call_count == 0
    with patch.object(mod, "infer_landmark_info",
                      return_value={"landmark": "Skogafoss", "country": "ISL"}), \
         patch.object(mod, "stop_opencode_server") as stop:
        future = scheduler.submit((63.5, -19.5), _cluster())
        assert future.result(timeout=5).info["landmark"] == "Skogafoss"
        scheduler.submit((1.0, 1.0), _cluster()).result(timeout=5)
        scheduler.shutdown()
    assert factory.call_count == 1
    assert stop.call_count == 2


def test_scheduler_server_start_failure_fails_every_request_without_retrying_start():
    factory = MagicMock(side_effect=RuntimeError("opencode not installed"))
    scheduler = _scheduler(factory)
    futures = [scheduler.submit((float(i), 0.0), _cluster()) for i in range(3)]
    for future in futures:
        with pytest.raises(mod.InferenceExhaustedError, match="server pool start failed"):
            future.result(timeout=5)
    scheduler.shutdown()
    assert factory.call_count == 1


def test_attach_url_works_without_the_opencode_cli():
    calls: list[dict[str, Any]] = []

    def fake_http(**kwargs: Any):
        calls.append(kwargs)
        return {"landmark_name": "Skogafoss", "country_name": "ISL"}, "ses1"

    with patch.object(mod.shutil, "which", return_value=None), \
         patch.object(mod, "_run_opencode_http_with_retry", side_effect=fake_http):
        info = mod.infer_landmark_info(63.5, -19.5, strict=True,
                                       opencode_attach_url="http://127.0.0.1:4100")
    assert info == {"landmark": "Skogafoss", "country": "ISL"}
    assert calls and calls[0]["server_url"] == "http://127.0.0.1:4100"


def test_opencode_availability_is_not_cached_across_tests_a():
    with patch.object(mod.shutil, "which", return_value=None):
        assert mod.has_opencode_command() is False


def test_opencode_availability_is_not_cached_across_tests_b():
    with patch.object(mod.shutil, "which", return_value="/usr/bin/opencode"):
        assert mod.has_opencode_command() is True
