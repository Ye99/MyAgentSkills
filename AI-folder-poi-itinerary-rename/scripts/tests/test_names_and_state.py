"""Folder names fit on disk, and an unexpected error keeps resumable state."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import rename_folder_by_ai_itinerary as mod


def test_landmark_token_is_capped():
    token = mod.normalize_landmark_token(" ".join(["Supercalifragilistic"] * 10))
    assert 0 < len(token) <= mod.MAX_LANDMARK_TOKEN_CHARS


def test_folder_name_drops_whole_landmarks_to_fit_the_byte_limit():
    landmarks = [f"Landmark{i}" + "X" * 31 for i in range(8)]  # 40 chars each
    name = mod.build_target_folder_name("2025_07_24", landmarks)
    assert len(name.encode("utf-8")) <= mod.MAX_FOLDER_NAME_BYTES
    assert name.startswith("2025_07_24_" + landmarks[0])
    assert all(part in landmarks for part in name[len("2025_07_24_"):].split(","))


def test_short_names_are_unchanged():
    assert mod.build_target_folder_name("2025_07_24", ["A", "B"]) == "2025_07_24_A,B"


def test_unexpected_error_keeps_completed_inferences_in_state():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / "2025" / "2025_07_24"
        folder.mkdir(parents=True)
        state_file = mod.default_state_file(folder)
        previous = {
            "folder_path": str(folder),
            "status": "failed-inference",
            "config": {"ratio": 1.0},
            "input_fingerprint": "abc",
            "completed_cluster_infos_by_index": {"0": {"landmark": "Skogafoss", "country": "ISL"}},
        }
        state_file.write_text(json.dumps(previous))
        with patch.object(mod, "rename_folder_from_itinerary", side_effect=RuntimeError("disk full")):
            mod.process_folder_tree(root, apply=False, inference_workers=1)
        state = json.loads(state_file.read_text())
    assert state["status"] == "failed-exception"
    assert state["error"]["message"] == "disk full"
    assert state["completed_cluster_infos_by_index"] == previous["completed_cluster_infos_by_index"]
    assert state["config"] == previous["config"]
    assert state["input_fingerprint"] == "abc"
