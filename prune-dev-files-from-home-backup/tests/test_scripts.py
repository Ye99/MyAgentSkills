import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
sys.path.insert(0, str(TESTS))
import build_fixture  # noqa: E402
import grade  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def home(tmp_path):
    build_fixture.main(tmp_path)
    return tmp_path / "home"


def run(script, *args):
    return subprocess.run(["bash", str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, check=True).stdout


def rescue(home, rel):
    return run("rescue_git_work.sh", home, home / rel)


def test_rescue_then_delete_keeps_every_marker(home):
    rescue(home, "projects/myapp")
    rescue(home, ".local/share/Trash/files/myapp.2")
    shutil.rmtree(home / "projects/myapp/.git")
    shutil.rmtree(home / "projects/myapp/src")
    shutil.rmtree(home / ".local/share/Trash/files/myapp.2")
    for marker in build_fixture.MARKERS.values():
        assert grade.marker_survives(home, marker), marker


def test_rescue_dir_names_are_visible_and_unique(home):
    rescue(home, "projects/myapp")
    rescue(home, ".local/share/Trash/files/myapp.2")
    names = sorted(p.name for p in (home / "RescuedGitWork").iterdir())
    assert names == ["local__share__Trash__files__myapp.2", "projects__myapp"]


def test_rescue_skips_untracked_build_output(home):
    rescue(home, "projects/myapp")
    files = home / "RescuedGitWork/projects__myapp/files"
    copied = {p.relative_to(files).as_posix() for p in files.rglob("*") if p.is_file()}
    assert copied == {"src/main.js", "src/new.js"}


def test_restore_steps_in_readme_rebuild_the_work(home, tmp_path):
    rescue(home, "projects/myapp")
    out = home / "RescuedGitWork/projects__myapp"
    base = next(l for l in (out / "README.txt").read_text().splitlines() if l.startswith("base:")).split()[1]
    clone = tmp_path / "restored"
    build_fixture.git(tmp_path, "clone", "-q", str(tmp_path / "remotes/myapp.git"), str(clone))
    build_fixture.git(clone, "checkout", "-q", "-b", "restored", base)
    build_fixture.git(clone, "am", "-q", *map(str, sorted((out / "unpushed-commits").glob("*.patch"))))
    build_fixture.git(clone, "apply", str(out / "uncommitted.patch"))
    shutil.copytree(out / "files", clone, dirs_exist_ok=True)
    log = subprocess.run(["git", "-C", str(clone), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert build_fixture.MARKERS["unpushed"] in log
    assert build_fixture.MARKERS["uncommitted"] in (clone / "src/main.js").read_text()
    assert build_fixture.MARKERS["untracked"] in (clone / "src/new.js").read_text()


def test_owner_line_separates_own_repos_from_third_party_clones(home):
    assert rescue(home, "projects/myapp").startswith("owner: person")
    assert rescue(home, "projects/upstream-lib").startswith("owner: third-party")


def test_clean_clone_needs_no_rescue(home):
    assert "nothing to rescue" in rescue(home, "projects/upstream-lib")
    assert "nothing to rescue" in rescue(home, "Downloads/myapp-main")
    assert not (home / "RescuedGitWork").exists()


def test_env_manifests_record_packages_before_envs_are_deleted(home):
    run("save_env_manifests.sh", home, home / "RebuildManifests")
    shutil.rmtree(home / "anaconda3")
    shutil.rmtree(home / "projects/analysis/.venv")
    assert grade.manifest_records(home, "numpy", "1.26.4")
    assert grade.manifest_records(home, "pandas", "2.2.0")


def test_readme_never_records_credentials_from_the_remote_url(home):
    app = home / "projects/myapp"
    build_fixture.git(app, "remote", "set-url", "origin", "https://user:ghp_SECRET@git.example/app.git")
    rescue(home, "projects/myapp")
    readme = (home / "RescuedGitWork/projects__myapp/README.txt").read_text()
    assert "ghp_SECRET" not in readme
    assert "https://git.example/app.git" in readme
