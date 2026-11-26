"""opencode serve processes never outlive the run, and the exit code reports failure."""
import signal
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pytest
from conftest import REAL_START_OPENCODE_SERVER

import rename_folder_by_ai_itinerary as mod


def test_ctrl_c_while_waiting_for_a_server_terminates_it():
    process = MagicMock()
    process.poll.return_value = None
    with patch.object(mod.subprocess, "Popen", return_value=process), \
         patch.object(mod.urllib.request, "urlopen", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            REAL_START_OPENCODE_SERVER(worker_id=0, startup_timeout_sec=5)
    process.terminate.assert_called_once()


def test_ctrl_c_while_starting_the_pool_stops_servers_already_started():
    first = mod.OpencodeServerHandle(worker_id=0, port=1, url="http://127.0.0.1:1", process=None)
    with patch.object(mod, "start_opencode_server", side_effect=[first, KeyboardInterrupt]), \
         patch.object(mod, "stop_opencode_server") as stop:
        with pytest.raises(KeyboardInterrupt):
            mod.start_opencode_server_pool(3)
    stop.assert_called_once_with(first)


def test_sigterm_unwinds_like_ctrl_c():
    previous = signal.getsignal(signal.SIGTERM)
    try:
        mod._install_sigterm_handler()
        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.parametrize(
    "status,code",
    [("completed", 0), ("completed-with-failures", 1), ("failed-integrity", 1)],
)
def test_tree_exit_code_reports_failures(monkeypatch, status, code):
    monkeypatch.setenv("HOME_GPS", "0.001,0.001")
    with TemporaryDirectory() as tmp:
        (Path(tmp) / "2025_07_22").mkdir()
        monkeypatch.setattr(sys, "argv", ["prog", tmp])
        monkeypatch.setattr(mod, "_install_sigterm_handler", lambda: None)
        with patch.object(mod, "process_folder_tree", return_value={"status": status}):
            assert mod.main() == code


@pytest.mark.parametrize(
    "status,code",
    [("renamed", 0), ("planned-rename", 0), ("skipped-no-landmark", 0), ("failed-inference", 1)],
)
def test_single_folder_exit_code_reports_failures(monkeypatch, status, code):
    monkeypatch.setenv("HOME_GPS", "0.001,0.001")
    with TemporaryDirectory() as tmp:
        folder = Path(tmp) / "2025_07_22"
        folder.mkdir()
        monkeypatch.setattr(sys, "argv", ["prog", str(folder)])
        monkeypatch.setattr(mod, "_install_sigterm_handler", lambda: None)
        with patch.object(mod, "rename_folder_from_itinerary", return_value={"status": status}):
            assert mod.main() == code
