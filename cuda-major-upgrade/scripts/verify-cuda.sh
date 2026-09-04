#!/bin/bash
# One-shot post-reboot CUDA + container GPU verification.
#
# Installed by the cuda-major-upgrade skill as a systemd oneshot unit so the checks
# still run when the reboot kills the session that started the upgrade. Writes
# everything to $LOG and disables its own unit on the way out.
#
# Optional env:
#   CUDA_TEST_IMAGE   base image for the container test (default: CUDA 13 base)
#   CUDA_TEST_BIN     statically linked CUDA binary to run inside containers
#   LOG               output path

LOG=${LOG:-/var/log/cuda-verify.log}
CUDA_TEST_IMAGE=${CUDA_TEST_IMAGE:-nvidia/cuda:13.0.1-base-ubuntu24.04}
CUDA_TEST_BIN=${CUDA_TEST_BIN:-}
UNIT=${UNIT:-cuda-verify.service}

exec >"$LOG" 2>&1

echo "=== CUDA post-reboot verification: $(date) ==="
echo

echo "--- kernel module ---"
cat /proc/driver/nvidia/version
echo

echo "--- nvidia-smi (host) ---"
nvidia-smi
echo

echo "--- nvcc ---"
/usr/local/cuda/bin/nvcc --version | tail -2
echo

echo "--- GPU enumeration ---"
nvidia-smi -L
echo

echo "--- docker default runtime ---"
docker info --format '{{.DefaultRuntime}}'
echo

echo "--- container GPU test: $CUDA_TEST_IMAGE via default runtime ---"
docker run --rm "$CUDA_TEST_IMAGE" nvidia-smi
echo "exit=$?"
echo

echo "--- container GPU test: --gpus all on plain ubuntu ---"
docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L
echo "exit=$?"
echo

if [ -n "$CUDA_TEST_BIN" ] && [ -x "$CUDA_TEST_BIN" ]; then
    d=$(dirname "$CUDA_TEST_BIN"); b=$(basename "$CUDA_TEST_BIN")
    echo "--- real CUDA compute inside container ($b) ---"
    docker run --rm -v "$d":/t:ro "$CUDA_TEST_IMAGE" "/t/$b"
    echo "exit=$?"
    echo
fi

echo "=== done ==="
systemctl disable "$UNIT"
