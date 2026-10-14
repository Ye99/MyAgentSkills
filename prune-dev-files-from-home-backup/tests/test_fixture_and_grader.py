import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import build_fixture  # noqa: E402
import grade  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def home(tmp_path):
    build_fixture.main(tmp_path)
    return tmp_path / "home"


def test_untouched_fixture_keeps_everything_and_fails_every_deletion(home, capsys):
    assert grade.main(home) == 1
    out = capsys.readouterr().out
    assert "FAIL keep" not in out
    assert "FAIL rescued" not in out
    assert "PASS gone" not in out


def test_app_repo_has_unpushed_uncommitted_and_untracked_work(home):
    app = home / "projects/myapp"
    unpushed = subprocess.run(["git", "-C", str(app), "log", "--branches", "--not", "--remotes", "--oneline"],
                              capture_output=True, text=True, check=True).stdout
    status = subprocess.run(["git", "-C", str(app), "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout
    assert unpushed.strip()
    assert " M src/main.js" in status
    assert "?? src/new.js" in status


def test_deleting_a_repo_without_rescue_loses_its_markers(home):
    shutil.rmtree(home / "projects/myapp")
    shutil.rmtree(home / ".local/share/Trash/files/myapp.2")
    for marker in build_fixture.MARKERS.values():
        assert not grade.marker_survives(home, marker)


def test_deleting_git_dir_without_rescue_loses_the_unpushed_commit(home):
    shutil.rmtree(home / "projects/myapp/.git")
    assert not grade.marker_survives(home, build_fixture.MARKERS["unpushed"])
