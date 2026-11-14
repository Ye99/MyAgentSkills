---
name: install-nvidia-docker-gpu
description: Use when freshly installing NVIDIA drivers and NVIDIA Container Toolkit for Docker GPU access on an Ubuntu x86_64 workstation or libvirt guest, including RTX 4060 Ti PCI passthrough. For CUDA major-version upgrades use cuda-major-upgrade; Jetson uses its own platform workflow.
---

# Fresh NVIDIA driver and Docker GPU setup

Install the guest/host GPU driver and container runtime without requiring a host CUDA development toolkit. Verify visibility and actual computation on every intended GPU.

## Survey and rollback

- Inspect OS, architecture, kernel, GPU PCI IDs, Secure Boot, installed NVIDIA/Docker packages, APT sources and candidates. Wait for existing apt/dpkg operations in both guests; never kill them or bypass a package-related shutdown inhibitor.
- For a VM, use virsh-vm-snapshots to create an offline external disk snapshot before changes. Save inactive domain XML, backing-chain inventory and writable UEFI NVRAM separately, and verify snapshot-info and domblklist. Preserve all backing files. This is a rollback point on the same storage, not an independent disaster-recovery backup. Virtiofs/shared host directories are outside the disk snapshot.
- Document restoration while the VM is stopped: preserve the current overlay, restore saved NVRAM, and define saved pre-change XML pointing to the preserved pre-change disk. Resolve external snapshot metadata deliberately; do not merge the overlay into the rollback disk or delete backing files.
- If taking GPUs from another VM, shut down that guest cleanly and verify it has released the devices. Copy PCI source addresses (GPU and audio functions), managed=yes, from its persistent XML. Remove copied guest address/alias elements so libvirt allocates unused guest addresses. Keep unrelated devices intact. When the user requests mutually exclusive VMs, retain assignments in both definitions; libvirt prevents concurrent device ownership. Do not remove the reference VM's assignments. Ensure conflicting guests do not autostart together.

## Install driver

Consult current [Ubuntu driver documentation](https://documentation.ubuntu.com/server/how-to/graphics/install-nvidia-drivers/) and inspect `ubuntu-drivers devices` after PCI passthrough is visible. Select a supported package and kernel-module variant from the guest's repositories. Check matching kernel headers/modules and Secure Boot signing. Do not blindly reuse historical driver versions, runfile installers, or repository codenames from a different Ubuntu release.

Dry-run the chosen package installation and inspect removals before installing. Save package state and logs. A full CUDA toolkit is optional for compilation, not a prerequisite for GPU-enabled containers. Reboot the guest after installation and require `nvidia-smi -L` to list every intended GPU before continuing. Reconnect from the hypervisor; do not reboot the hypervisor.

## Docker and NVIDIA Container Toolkit

1. Preserve an existing Docker installation and daemon configuration. For a fresh install follow [Docker's signed Ubuntu APT repository instructions](https://docs.docker.com/engine/install/ubuntu/), using the actual supported guest codename. If the release is unsupported, stop before repository changes; do not substitute another codename. Inspect conflicting docker.io/containerd/runc packages before replacing anything. Install docker-ce, docker-ce-cli, containerd.io, docker-buildx-plugin and docker-compose-plugin.
2. Follow the current [NVIDIA Container Toolkit APT instructions](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html): install the repository key into its own keyring, configure the signed stable repository, update APT and install nvidia-container-toolkit and its matching dependencies.
3. Save existing `/etc/docker/daemon.json`, run `sudo nvidia-ctk runtime configure --runtime=docker`, validate JSON and restart Docker after checking running workloads. Preserve existing default-runtime policy. A new installation can use explicit `--gpus all`; setting NVIDIA as default is optional. Rootless Docker needs the separate official rootless procedure.
4. Use sudo for Docker unless the user wants Docker-group membership; avoid silently granting it.

## Verify

- Record guest `lspci -nnk`, `nvidia-smi` (name, UUID, VRAM, driver), loaded module version, and any NVIDIA Xid errors. Require the expected GPU count.
- Run `sudo docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L` and compare GPU UUIDs with the guest. Test each GPU individually with `--gpus '"device=0"'` and corresponding indices; require one GPU per isolated test.
- Prove compute, not only NVML visibility: use a compatible CUDA development container to compile a small checked CUDA kernel, then run it on every GPU. Check all CUDA API return values, synchronize, copy output back and compare with CPU results. Alternatively use a compatible framework container and check a tensor operation on each device. Pin and record the image tag/digest. Choose an image supported by the installed driver.
- Reboot and recheck the final configuration. Compare failed services against the initial baseline and report pre-existing failures separately.
- Keep rollback files and installation/test logs. Report the exact snapshot and backup location, GPU counts/UUID match, package versions, test results, and the mutually exclusive VM startup requirement.

### Reproducible compute sample

Use [scripts/gpu-check.cu](scripts/gpu-check.cu); its optional argument is the expected GPU count. Choose a CUDA development image compatible with the installed driver and GPU architecture; inspect its manifest and record the pulled digest. For RTX 4060 Ti (sm_89), an example is `nvidia/cuda:12.8.0-devel-ubuntu24.04`:

```bash
# Place gpu-check.cu in a dedicated guest directory, e.g. /var/tmp/gpu-verify (stage after reboot, or keep a copy under the login user home).
sudo docker run --rm -v /var/tmp/gpu-verify:/work nvidia/cuda:12.8.0-devel-ubuntu24.04 nvcc -arch=sm_89 -cudart static -o /work/gpu-check /work/gpu-check.cu
/var/tmp/gpu-verify/gpu-check 2
sudo docker run --rm --gpus all -v /var/tmp/gpu-verify:/work:ro ubuntu:24.04 /work/gpu-check 2
sudo docker run --rm --gpus '"device=0"' -v /var/tmp/gpu-verify:/work:ro ubuntu:24.04 /work/gpu-check 1
sudo docker run --rm --gpus '"device=1"' -v /var/tmp/gpu-verify:/work:ro ubuntu:24.04 /work/gpu-check 1
```

Every invocation must exit zero and report PASS on each expected GPU. The development image is used only for compilation; host CUDA installation is unnecessary. Adjust architecture, count and image to the actual machine.
