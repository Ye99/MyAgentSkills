"""Resume reuses finished inferences unless a setting that changes the result changed."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import rename_folder_by_ai_itinerary as mod


def _run(folder: Path, points, **kwargs) -> int:
    calls = []

    def fake_infer(*args, **kw):
        calls.append(args)
        return {"landmark": "Skogafoss", "country": "ISL"}

    defaults = dict(apply=False, ratio=1.0, inference_workers=1, home_gps=(10.0, 10.0))
    defaults.update(kwargs)
    def fake_server(worker_id, startup_timeout_sec=30.0, cancel=None):
        return mod.OpencodeServerHandle(worker_id=worker_id, port=1, url="http://127.0.0.1:1", process=None)

    with patch.object(mod, "extract_media_points", return_value=(points, [])), \
         patch.object(mod, "start_opencode_server", side_effect=fake_server), \
         patch.object(mod, "infer_landmark_info", side_effect=fake_infer):
        mod.rename_folder_from_itinerary(folder, **defaults)
    return len(calls)


def _points(folder: Path):
    return [mod.MediaPoint(str(folder / "a.jpg"), 63.5, -19.5, datetime(2025, 7, 24, 9, 0))]


def test_runtime_settings_do_not_discard_finished_inferences():
    with TemporaryDirectory() as tmp:
        folder = Path(tmp) / "2025_07_24"
        folder.mkdir()
        assert _run(folder, _points(folder)) == 1
        # Sanity: the parallel path does call inference when nothing is saved.
        assert _run(folder, _points(folder), inference_workers=2, resume=False) == 1
        assert _run(folder, _points(folder), opencode_timeout_sec=999, opencode_retries=9,
                    opencode_backoff_sec=9.0, inference_workers=2) == 0


def test_changed_home_gps_redoes_inference():
    with TemporaryDirectory() as tmp:
        folder = Path(tmp) / "2025_07_24"
        folder.mkdir()
        assert _run(folder, _points(folder)) == 1
        assert _run(folder, _points(folder), home_gps=(11.0, 11.0)) == 1


def test_changed_model_redoes_inference():
    with TemporaryDirectory() as tmp:
        folder = Path(tmp) / "2025_07_24"
        folder.mkdir()
        assert _run(folder, _points(folder)) == 1
        assert _run(folder, _points(folder), opencode_model="other/model") == 1
