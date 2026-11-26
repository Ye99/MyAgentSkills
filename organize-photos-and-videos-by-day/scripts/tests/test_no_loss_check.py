"""The no-loss check counts files already at the destination and never skips silently."""
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import organize_media_by_local_date as mod  # noqa: E402


def test_identical_files_already_at_destination_are_not_missed():
    sources = [Path("/s/a.jpg"), Path("/s/b.jpg"), Path("/s/c.jpg")]
    copied = [
        {"source_path": "/s/a.jpg", "status": "copied"},
        {"source_path": "/s/b.jpg", "status": "skipped-already-exists"},
    ]
    assert mod.unaccounted_sources(sources, copied) == ["/s/c.jpg"]


def test_verification_refuses_to_pass_when_the_checker_is_missing(tmp_path):
    with mock.patch.object(mod, "_load_find_missing_module", return_value=None):
        with pytest.raises(mod.VerificationUnavailable):
            mod.verify_with_find_missing([tmp_path / "a.jpg"], tmp_path, workers=1, verbose=False)
