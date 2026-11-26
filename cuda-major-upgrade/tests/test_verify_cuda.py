"""verify-cuda.sh with nvidia-smi, nvcc, docker and systemctl stubbed; no GPU needed."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-cuda.sh"


def _stub(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "calls.log"
    rec = f'echo "$(basename "$0") $*" >> "{calls}"'
    _stub(bin_ / "nvidia-smi", rec + '\necho "GPU 0: Fake"; exit ${FAKE_SMI_RC:-0}')
    _stub(bin_ / "docker", rec + '\n[ "$1" = info ] && echo nvidia\nexit ${FAKE_DOCKER_RC:-0}')
    _stub(bin_ / "systemctl", rec)
    nvcc = tmp_path / "nvcc"
    _stub(nvcc, 'echo "Cuda compilation tools, release 13.0"; echo "Build cuda_13.0"')
    version = tmp_path / "version"
    version.write_text("NVRM version: 580.00\n")
    return dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", LOG=str(tmp_path / "verify.log"),
                NVCC=str(nvcc), NVIDIA_VERSION_FILE=str(version)), calls, tmp_path


def run(env, **extra):
    return subprocess.run(["bash", str(SCRIPT)], env={**env, **extra}, capture_output=True,
                          text=True, timeout=30)


def test_all_checks_pass(env):
    e, calls, tmp = env
    r = run(e)
    log = (tmp / "verify.log").read_text()
    assert r.returncode == 0, log
    assert "RESULT: PASS" in log
    assert "release 13.0" in log and "NVRM version" in log
    assert "systemctl disable cuda-verify.service" in calls.read_text()


def test_failed_check_gives_fail_verdict_and_still_disables_unit(env):
    e, calls, tmp = env
    r = run(e, FAKE_DOCKER_RC="125")
    log = (tmp / "verify.log").read_text()
    assert r.returncode != 0
    assert "RESULT: FAIL" in log and "via default runtime" in log.split("RESULT: FAIL")[1]
    assert "systemctl disable cuda-verify.service" in calls.read_text()


def test_missing_kernel_module_fails(env):
    e, calls, tmp = env
    r = run(e, NVIDIA_VERSION_FILE=str(tmp / "absent"))
    assert r.returncode != 0
    assert "kernel module" in (tmp / "verify.log").read_text().split("RESULT: FAIL")[1]


def test_compute_binary_runs_only_when_given(env):
    e, calls, tmp = env
    run(e)
    assert "/t/" not in calls.read_text()
    binary = tmp / "kernels" / "saxpy"
    binary.parent.mkdir()
    _stub(binary, "true")
    r = run(e, CUDA_TEST_BIN=str(binary))
    assert r.returncode == 0
    assert f"-v {binary.parent}:/t:ro" in calls.read_text() and "/t/saxpy" in calls.read_text()
