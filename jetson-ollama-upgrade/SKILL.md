---
name: jetson-ollama-upgrade
description: Upgrade Ollama on Jetson Orin/Nano with jetson-containers, build CUDA-enabled image, and verify newer model pull support.
---

# Jetson Ollama Upgrade

## Purpose

Upgrade Ollama on NVIDIA Jetson devices where upstream generic binaries are not sufficient, using `jetson-containers` to produce a CUDA-enabled local image.

This workflow targets JetPack 6.x devices (for example Orin Nano) and fixes failures like:

```
HTTP 412 - requires a newer version of Ollama
```

## When To Use

- The current Ollama container is old (for example `0.6.8`) and cannot pull newer models.
- Device is Jetson (ARM64 + NVIDIA iGPU), so standard non-Jetson images are unreliable.
- You need a local build that matches L4T/JetPack/CUDA on the target board.

## Inputs To Confirm

1. SSH host (default: `jetsonorinwhitewifi`)
2. `jetson-containers` path on device (default: `/home/ye/p/jetson-containers`)
3. Target package (`ollama`)

## Core Workflow

1. Inspect Jetson runtime versions:

```bash
ssh "$HOST" "uname -m; cat /etc/nv_tegra_release; nvcc --version"
```

2. Update `jetson-containers`:

```bash
ssh "$HOST" "git -C /home/ye/p/jetson-containers pull --rebase origin master"
```

3. Confirm Ollama pin in package config:

```bash
ssh "$HOST" "python3 - <<'PY'
from pathlib import Path
p = Path('/home/ye/p/jetson-containers/packages/llm/ollama/config.py')
print(p.read_text())
PY"
```

4. Start build in tmux (long-running):

```bash
ssh "$HOST" "tmux new-session -d -s ollama-build 'cd /home/ye/p/jetson-containers && jetson-containers build ollama 2>&1 | tee /tmp/ollama-build.log'"
```

5. Monitor progress:

```bash
ssh "$HOST" "tmux capture-pane -t ollama-build -p | tail -20"
ssh "$HOST" "tail -20 /tmp/ollama-build.log"
```

6. Verify final image exists:

```bash
ssh "$HOST" "docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep 'ollama:r36.4.*-ollama'"
```

7. Verify autotag resolves to local build:

```bash
ssh "$HOST" "cd /home/ye/p/jetson-containers && jetson-containers autotag ollama"
```

8. Smoke test model pull:

```bash
ssh "$HOST" "docker run -d --name ollama-test --runtime nvidia --network host -v ollama-models:/root/.ollama ollama:r36.4.tegra-aarch64-cu126-22.04-ollama ollama serve"
ssh "$HOST" "docker exec ollama-test ollama pull ministral-3:latest"
ssh "$HOST" "docker stop ollama-test && docker rm ollama-test"
```

## Expected Outcome

- A local image like `ollama:r36.4.tegra-aarch64-cu126-22.04-ollama` exists.
- `autotag ollama` selects that local image.
- Pulling newer models begins normally (no 412 version error).

## Notes

- Build can take 1-2 hours on Orin Nano because CUDA kernels are compiled from source.
- `ollama --version` may show `0.0.0` if the version string was not injected at link time; rely on source pin + functional validation.
- Keep the build in tmux so SSH disconnects do not interrupt compilation.
