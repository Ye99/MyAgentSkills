"""Work the rescue used to miss: detached HEAD commits, merges, and ignored files."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rescue_git_work.sh"
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def commit(repo, name, text, msg):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def setup(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\temail = me@example.com\n\tname = Me\n")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = home / "projects" / "app"
    repo.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "me@example.com")
    git(repo, "config", "user.name", "Me")
    git(repo, "checkout", "-q", "-b", "main")
    commit(repo, "a.txt", "a", "base")
    git(repo, "push", "-q", "-u", "origin", "main")
    return home, repo, tmp_path


def rescue(home, repo):
    return subprocess.run(["bash", str(SCRIPT), str(home), str(repo)],
                          capture_output=True, text=True, check=True).stdout


def out_dir(home):
    return home / "RescuedGitWork" / "projects__app"


def test_commits_on_a_detached_head_are_rescued(setup):
    home, repo, _ = setup
    git(repo, "checkout", "-q", "--detach")
    commit(repo, "b.txt", "b", "DETACHED-WORK")
    stdout = rescue(home, repo)
    assert "rescued:" in stdout
    patches = "".join(p.read_text() for p in (out_dir(home) / "unpushed-commits").glob("*.patch"))
    assert "DETACHED-WORK" in patches


def test_every_rescue_with_unpushed_commits_has_a_bundle_that_restores_them(setup):
    home, repo, tmp = setup
    git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "b.txt", "b", "side")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "MERGE-COMMIT", "feature")
    rescue(home, repo)
    bundle = out_dir(home) / "unpushed.bundle"
    assert bundle.exists()
    clone = tmp / "restored"
    subprocess.run(["git", "clone", "-q", str(tmp / "remote.git"), str(clone)], check=True, capture_output=True)
    git(clone, "fetch", "-q", str(bundle), "refs/heads/main:refs/heads/restored")
    log = git(clone, "log", "--format=%s", "restored")
    assert "MERGE-COMMIT" in log  # format-patch drops merges; the bundle keeps them
    assert "unpushed.bundle" in (out_dir(home) / "README.txt").read_text()


def test_ignored_files_that_cannot_be_regenerated_are_rescued(setup):
    home, repo, _ = setup
    commit(repo, ".gitignore", ".env\nnode_modules/\nsecrets/\n", "ignore")
    git(repo, "push", "-q")
    (repo / ".env").write_text("TOKEN=local-only")
    (repo / "secrets").mkdir()
    (repo / "secrets" / "key.pem").write_text("KEY")
    (repo / "node_modules" / "lib").mkdir(parents=True)
    (repo / "node_modules" / "lib" / "index.js").write_text("x")
    stdout = rescue(home, repo)
    assert "rescued:" in stdout
    files = out_dir(home) / "files"
    assert (files / ".env").read_text() == "TOKEN=local-only"
    assert (files / "secrets" / "key.pem").read_text() == "KEY"
    assert not (files / "node_modules").exists()


def test_only_ignored_build_output_needs_no_rescue(setup):
    home, repo, _ = setup
    commit(repo, ".gitignore", "node_modules/\n", "ignore")
    git(repo, "push", "-q")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x.js").write_text("x")
    assert "nothing to rescue" in rescue(home, repo)


def test_a_repo_with_no_commits_does_not_crash(tmp_path):
    home = tmp_path / "home"
    repo = home / "projects" / "app"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "notes.txt").write_text("draft")
    stdout = rescue(home, repo)
    assert "rescued:" in stdout
    assert (out_dir(home) / "files" / "notes.txt").read_text() == "draft"
