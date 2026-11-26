#!/bin/bash
# One-shot post-reboot CUDA + container GPU verification.
#
# Installed by the cuda-major-upgrade skill as a systemd oneshot unit so the checks
# still run when the reboot kills the session that started the upgrade. Writes
# everything to $LOG, ends it with one RESULT line, and disables its own unit on the
# way out. Exits non-zero when any check failed.
#
# Optional env:
#   CUDA_TEST_IMAGE   base image for the container test (default: CUDA 13 base)
#   CUDA_TEST_BIN     statically linked CUDA binary to run inside containers
#   LOG               output path
#   NVCC              nvcc path (default /usr/local/cuda/bin/nvcc)
#   NVIDIA_VERSION_FILE  kernel module version file (default /proc/driver/nvidia/version)

LOG=${LOG:-/var/log/cuda-verify.log}
CUDA_TEST_IMAGE=${CUDA_TEST_IMAGE:-nvidia/cuda:13.0.1-base-ubuntu24.04}
CUDA_TEST_BIN=${CUDA_TEST_BIN:-}
UNIT=${UNIT:-cuda-verify.service}
NVCC=${NVCC:-/usr/local/cuda/bin/nvcc}
NVIDIA_VERSION_FILE=${NVIDIA_VERSION_FILE:-/proc/driver/nvidia/version}

exec >"$LOG" 2>&1

failed=()

# check NAME CMD...: run one check, print its exit code, remember it if it failed.
check() {
    local name=$1; shift
    echo "--- $name ---"
    "$@"
    local rc=$?
    echo "exit=$rc"
    echo
    [ "$rc" -eq 0 ] || failed+=("$name")
}

nvcc_version() { "$NVCC" --version | tail -2; return "${PIPESTATUS[0]}"; }

echo "=== CUDA post-reboot verification: $(date) ==="
echo

check "kernel module" cat "$NVIDIA_VERSION_FILE"
check "nvidia-smi (host)" nvidia-smi
check "nvcc" nvcc_version
check "GPU enumeration" nvidia-smi -L
check "docker default runtime" docker info --format '{{.DefaultRuntime}}'
check "container GPU test: $CUDA_TEST_IMAGE via default runtime" docker run --rm "$CUDA_TEST_IMAGE" nvidia-smi
check "container GPU test: --gpus all on plain ubuntu" docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L

if [ -n "$CUDA_TEST_BIN" ] && [ -x "$CUDA_TEST_BIN" ]; then
    d=$(dirname "$CUDA_TEST_BIN"); b=$(basename "$CUDA_TEST_BIN")
    check "real CUDA compute inside container ($b)" docker run --rm -v "$d":/t:ro "$CUDA_TEST_IMAGE" "/t/$b"
fi

if [ "${#failed[@]}" -eq 0 ]; then
    echo "RESULT: PASS"
else
    echo "RESULT: FAIL (${#failed[@]} checks failed: $(IFS=';'; echo "${failed[*]}"))"
fi
echo "=== done ==="
systemctl disable "$UNIT"
[ "${#failed[@]}" -eq 0 ]
