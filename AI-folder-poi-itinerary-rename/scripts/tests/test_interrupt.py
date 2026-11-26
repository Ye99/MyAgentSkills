"""Ctrl-C stops a tree run promptly and no folder is renamed after it."""
import threading
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import pytest

import rename_folder_by_ai_itinerary as mod


def test_interrupt_returns_without_waiting_for_running_folders():
    release = threading.Event()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for day in ("2025_07_22", "2025_07_23", "2025_07_24"):
            (root / "2025" / day).mkdir(parents=True)

        def fake_rename(folder: Path, **_: Any) -> dict[str, Any]:
            if folder.name == "2025_07_22":
                raise KeyboardInterrupt
            release.wait(10)
            return {"folder_path": str(folder), "status": "planned-rename", "target_name": folder.name}

        started = time.monotonic()
        try:
            with patch.object(mod, "rename_folder_from_itinerary", side_effect=fake_rename):
                with pytest.raises(KeyboardInterrupt):
                    mod.process_folder_tree(root, apply=True, inference_workers=3)
            elapsed = time.monotonic() - started
        finally:
            release.set()
    assert elapsed < 5, f"waited {elapsed:.1f}s for running folders after Ctrl-C"
    assert mod._STOP_REQUESTED.is_set()


def test_no_rename_once_stop_is_requested():
    with TemporaryDirectory() as tmp:
        folder = Path(tmp) / "2025_07_24"
        folder.mkdir()
        points = [mod.MediaPoint(str(folder / "a.jpg"), 63.5, -19.5, datetime(2025, 7, 24, 9, 0))]
        mod._STOP_REQUESTED.set()
        try:
            with patch.object(mod, "extract_media_points", return_value=(points, [])), \
                 patch.object(mod, "infer_landmark_info",
                              return_value={"landmark": "Skogafoss", "country": "ISL"}):
                result = mod.rename_folder_from_itinerary(folder, apply=True, ratio=1.0,
                                                          inference_workers=1)
        finally:
            mod._STOP_REQUESTED.clear()
        assert folder.exists()
        assert result["status"] != "renamed"


def test_a_new_tree_run_clears_an_old_stop_request():
    mod._STOP_REQUESTED.set()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "2025" / "2025_07_22").mkdir(parents=True)
        with patch.object(mod, "rename_folder_from_itinerary",
                          side_effect=lambda folder, **_: {"folder_path": str(folder),
                                                           "status": "skipped-no-landmark"}):
            summary = mod.process_folder_tree(root, apply=False, inference_workers=1)
    assert summary["skipped_folder_count"] == 1
    assert not mod._STOP_REQUESTED.is_set()
