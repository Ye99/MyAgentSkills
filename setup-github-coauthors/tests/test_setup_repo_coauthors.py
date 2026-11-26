"""setup-repo-coauthors.sh with curl stubbed, so no request reaches GitHub."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup-repo-coauthors.sh"
IDS = {"opencode": 11, "claude": 22, "codex-cli": 33}


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    curl = bin_ / "curl"
    cases = "\n".join(f'  *users/{u}) echo \'{{"id": {i}}}\' ;;' for u, i in IDS.items())
    curl.write_text("#!/usr/bin/env bash\nurl=\"${@: -1}\"\ncase \"$url\" in\n"
                    + cases + "\n  *users/empty) echo '{}' ;;\n  *) exit 22 ;;\nesac\n")
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    home.mkdir()
    return dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", HOME=str(home),
                GIT_CONFIG_NOSYSTEM="1")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    return r


def run(env, *args, cwd=None):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=30)


def test_writes_template_with_noreply_addresses(env, repo):
    r = run(env, str(repo))
    assert r.returncode == 0, r.stderr
    text = (repo / ".gitmessage").read_text()
    assert "Co-authored-by: OpenCode <11+opencode@users.noreply.github.com>" in text
    assert "Co-authored-by: Claude <22+claude@users.noreply.github.com>" in text
    assert "Co-authored-by: Codex <33+codex-cli@users.noreply.github.com>" in text


def test_template_path_is_absolute_so_commits_from_subdirs_find_it(env, repo):
    r = run(env, "repo", cwd=repo.parent)
    assert r.returncode == 0, r.stderr
    value = subprocess.run(["git", "-C", str(repo), "config", "--local", "commit.template"],
                           capture_output=True, text=True, env=env).stdout.strip()
    assert Path(value).is_absolute()
    assert Path(value) == repo / ".gitmessage"


def test_unknown_user_fails_without_writing(env, repo):
    r = run(env, str(repo), "nobody")
    assert r.returncode != 0
    assert not (repo / ".gitmessage").exists()


def test_user_without_id_fails(env, repo):
    r = run(env, str(repo), "empty")
    assert r.returncode != 0
    assert "could not parse" in r.stderr


def test_not_a_repo(env, tmp_path):
    r = run(env, str(tmp_path))
    assert r.returncode == 1
    assert "not a git repository" in r.stderr
