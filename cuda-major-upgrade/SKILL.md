---
name: cuda-major-upgrade
description: Upgrade a Linux host across a CUDA major version (e.g. 12 to 13) by purging the old toolkit, moving the NVIDIA driver to the required branch, keeping Docker GPU support intact, cleaning orphans, and verifying with real compute on host and in containers.
---

# CUDA Major Version Upgrade

## Purpose

Move a workstation from one CUDA **major** version to the next (12 -> 13, and the same
shape for 13 -> 14) using NVIDIA's apt repo, then prove the result works with real
kernels rather than just `nvidia-smi` output.

A major bump is not an `apt upgrade`. It requires a **new driver branch**, and apt will
not perform the branch swap on its own. Most of this skill is about the traps in that
transaction.

## When To Use

- `nvcc --version` shows an older major than you want, or `nvcc` is missing entirely.
- The host must keep working GPU access for Docker containers after the upgrade.
- A distro release upgrade left the CUDA apt source disabled (see Step 1).

## When Not To Use

- Minor bumps inside a major (13.1 -> 13.3): plain `apt upgrade` handles those, no
  driver branch change needed.
- Datacenter hosts on a pinned LTS driver branch — check the vendor support matrix
  first, since the new major may demand a branch your workload is not certified on.

## Inputs To Confirm

1. Target CUDA major (e.g. `13`) and the exact toolkit package (e.g. `cuda-toolkit-13-3`).
2. Ubuntu codename for the repo path (`ubuntu2404`, `ubuntu2604`, ...).
3. Whether the GPUs are supported by the new major. **CUDA 13 dropped Maxwell, Pascal
   and Volta.** Check compute capability before starting; if the host has an
   unsupported GPU, stop and report rather than bricking its CUDA support.
4. Whether the user accepts a reboot (mandatory — see Step 7).

---

## Step 0 — Survey and snapshot

```bash
lsb_release -a; uname -r
nvidia-smi
nvcc --version 2>&1 | tail -2
ls -ld /usr/local/cuda*
dpkg -l | grep -Ei 'cuda|nvidia|cudnn' | awk '{print $1,$2,$3}'
nvidia-ctk --version; docker --version; cat /etc/docker/daemon.json
mokutil --sb-state            # Secure Boot changes DKMS signing requirements
df -h /  /home
```

Save a rollback reference before touching anything. Keep it outside `/tmp` so it
survives the reboot in Step 7:

```bash
WORK=~/cuda-upgrade-$(date +%Y%m%d); mkdir -p "$WORK"
dpkg -l > "$WORK/dpkg-before.txt"
cp /etc/docker/daemon.json "$WORK/daemon.json.bak"
```

Record the current **compute capability** (`nvidia-smi --query-gpu=compute_cap
--format=csv`) — you need it for the `-arch=sm_XX` test compile later.

## Step 1 — Fix the apt sources

After an Ubuntu release upgrade, `do-release-upgrade` renames third-party sources to
`*.list.distUpgrade`, which silently disables them. A host can therefore sit on an old
CUDA for years with no upgrade ever offered. Check for this first:

```bash
ls /etc/apt/sources.list.d/ | grep -Ei 'cuda|nvidia'
```

Install the keyring for the **current** distro release and delete the stale file:

```bash
curl -fsSLO https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo rm -f /etc/apt/sources.list.d/cuda-ubuntu2204-x86_64.list.distUpgrade
```

Re-enable the container toolkit repo in its modern form:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
echo 'deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list.distUpgrade
sudo apt-get update
```

## Step 2 — Pick the toolkit and driver branch

```bash
apt-cache policy cuda-toolkit-13-3 cuda-drivers-580 nvidia-container-toolkit
```

Two rules:

- **Each CUDA major has a driver floor.** CUDA 13.x requires **r580 or newer**. Install
  `cuda-drivers-<branch>` explicitly and pick the branch deliberately; do not install
  the bare `cuda-drivers` metapackage, which tracks the newest branch in the repo
  (branches far newer than the toolkit, e.g. 610, are often present).
- **`cuda-runtime-13-x` no longer depends on any driver package.** In the CUDA 12 era
  installing the toolkit dragged a driver along. It does not any more, so the driver
  must be named on the command line or you will end up on CUDA 13 with an old driver.

Minor-version compatibility means a 13.3 toolkit runs fine on an r580 driver that
reports "CUDA Version: 13.0". That mismatch in `nvidia-smi` is expected, not a defect.

## Step 3 — Purge the old toolkit

Do this **before** the driver swap so the two transactions stay debuggable.

```bash
dpkg -l | awk '/^ii/{print $2}' | grep -E '(-12-3$|^cuda-12-3$)' \
  | xargs sudo DEBIAN_FRONTEND=noninteractive apt-get purge -y
```

> **Trap — zsh does not word-split.** `sudo apt-get purge -y $PKGS` works in bash but
> in zsh passes the whole newline-joined list as **one** argument, and apt fails with
> `E: Unable to locate package cuda-12-3\n   cuda-cccl-12-3\n   ...`. Always pipe
> through `xargs` (or use `${=PKGS}`) so this is shell-agnostic.

Then drop `rc` (removed-but-configured) leftovers from older driver branches — hosts
that have been upgraded a few times accumulate these:

```bash
dpkg -l | awk '/^rc/{print $2}' | grep -E 'nvidia|cuda' | xargs -r sudo apt-get purge -y
```

## Step 4 — Swap the driver branch (the hard part)

A naive install fails:

```
nvidia-kernel-source-560 : Conflicts: nvidia-kernel-source
nvidia-kernel-source-580 : Conflicts: nvidia-kernel-source
E: Error, pkgProblemResolver::Resolve generated breaks
```

apt will not choose to remove the old branch by itself. Name the removals in the same
transaction with the `pkg-` suffix, and **always dry-run first**:

```bash
sudo apt-get -s install cuda-toolkit-13-3 cuda-drivers-580 nvidia-container-toolkit \
     cuda-drivers- cuda-drivers-560- nvidia-driver-560-
```

Read the dry-run output before proceeding:

- `grep '^Remv'` — confirm nothing unexpected is being removed. `nvidia-prime` commonly
  goes; that is the hybrid-graphics switcher and is irrelevant on a desktop with only
  discrete GPUs, but say so in the report rather than letting it vanish silently.
- `grep '^Inst'` — confirm you are getting the intended variant (`nvidia-driver-580`
  proprietary vs `nvidia-driver-580-open`) and that the `:i386` GL libs survive if the
  host runs Steam/Wine. Newer branches legitimately drop some 32-bit **compute** libs
  while keeping 32-bit GL.

Then run it for real, logging to a file:

```bash
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  cuda-toolkit-13-3 cuda-drivers-580 nvidia-container-toolkit \
  cuda-drivers- cuda-drivers-560- nvidia-driver-560- > "$WORK/install.log" 2>&1
```

> **Trap — `libnvidia-extra-<old>` file conflict.** The install aborts mid-unpack with:
>
> ```
> trying to overwrite '/usr/lib/x86_64-linux-gnu/gbm/nvidia-drm_gbm.so',
> which is also in package libnvidia-extra-560:amd64
> ```
>
> `libnvidia-extra-<old>` is not scheduled for removal but owns a file the new
> `libnvidia-gl-<new>` ships. Force it out, then let apt finish:
>
> ```bash
> sudo dpkg --remove --force-depends libnvidia-extra-560
> sudo apt-get -f install -y
> sudo apt-get install -y cuda-toolkit-13-3 cuda-drivers-580 nvidia-container-toolkit
> ```
>
> Recovering with `apt-get -f install` is safe here; the transaction resumes and DKMS
> builds normally.

Confirm DKMS built for **every** installed kernel, not just the running one:

```bash
dkms status
```

## Step 5 — Clean up orphans

```bash
sudo apt-get -s autoremove          # inspect first
sudo apt-get autoremove --purge -y
sudo apt-get autoclean -y
```

> **Check the autoremove list before running it.** A CUDA 12 install pulls Java in
> through `nsight-systems`/`nsight-compute`, so the orphan list can include
> `default-jre`, `openjdk-*`, `ca-certificates-java` and friends. That is usually
> correct to remove, but verify it is genuinely unused first — no JDK, no reverse
> dependencies, no shell-history usage — and tell the user what went:
>
> ```bash
> which java javac; apt-cache rdepends --installed default-jre | grep -v '^ '
> grep -ohE '\bjava\b' ~/.zsh_history ~/.bash_history 2>/dev/null | head
> ```

Verify nothing from the old major survives:

```bash
dpkg -C && echo "no broken packages"
sudo apt-get check
dpkg -l | grep -E '12-3|560|545' | awk '{print $1,$2}'
```

## Step 6 — Restore equivalent function

The purge removes the old alternatives symlinks; confirm the new ones point where you
expect, and put `nvcc` on PATH (it frequently was **not** on PATH before, which is why
the host looked like it had no CUDA at all):

```bash
ls -ld /usr/local/cuda*
update-alternatives --display cuda
echo 'export PATH=/usr/local/cuda/bin${PATH:+:${PATH}}' | sudo tee /etc/profile.d/cuda.sh
sudo chmod 644 /etc/profile.d/cuda.sh
sudo ldconfig
```

Re-apply the container runtime config with the **new** `nvidia-ctk`, preserving whatever
default-runtime policy the host had:

```bash
sudo nvidia-ctk runtime configure --runtime=docker --set-as-default
sudo systemctl restart docker
cat /etc/docker/daemon.json          # confirm default-runtime survived
```

Pre-pull the test images now, while you still have a shell:

```bash
docker pull nvidia/cuda:13.0.1-base-ubuntu24.04
docker pull ubuntu:24.04
```

## Step 7 — Reboot, and survive it

The new kernel module cannot load while Xorg/Wayland holds the GPU:

```bash
sudo fuser -v /dev/nvidia*       # Xorg will be listed
systemctl is-active display-manager
```

Until reboot, `nvidia-smi` fails with `Failed to initialize NVML: Driver/library version
mismatch`. That is expected at this point and is **not** a failed upgrade.

> **If the agent is running on the host being upgraded, the reboot kills the agent's own
> session.** Module reload without a reboot is not possible while a display manager is
> running. Install a self-disabling one-shot unit so the verification still runs and is
> captured, then reboot:

```bash
sudo install -m755 scripts/verify-cuda.sh /usr/local/sbin/cuda-verify.sh
sudo tee /etc/systemd/system/cuda-verify.service >/dev/null <<'EOF'
[Unit]
Description=One-shot CUDA / container GPU verification
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/cuda-verify.sh
RemainAfterExit=no

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable cuda-verify.service
sudo shutdown -r +1 "CUDA upgrade: rebooting to activate new driver"
```

Use `+1` rather than `now` so the user can `sudo shutdown -c` if they need to save work.
Tell them the log path before the session dies. Remove the unit and script afterwards.

## Step 8 — Verify with real compute

`nvidia-smi` proves the driver loaded. It does **not** prove CUDA works. Compile and run
actual kernels, on the host and inside containers.

Build the two test programs in `scripts/` with the compute capability from Step 0:

```bash
export PATH=/usr/local/cuda/bin:$PATH
nvcc -arch=sm_89 -o /tmp/gputest scripts/gputest.cu && /tmp/gputest
nvcc -arch=sm_89 -o /tmp/blastest scripts/blastest.cu -lcublas && /tmp/blastest
```

`gputest.cu` runs a saxpy on **every** GPU and checks the result; `blastest.cu` proves
the math libraries link and compute correctly. Both must report max error `0.0`.

For containers, build a **statically linked** binary so it runs in a `base` image that
has no CUDA runtime of its own:

```bash
nvcc -arch=sm_89 -cudart static -o /tmp/gputest_static scripts/gputest.cu
```

Then exercise all four GPU access paths — they use different code paths in the toolkit
and a broken config can pass one while failing another:

```bash
# 1. default runtime, no flags (only if daemon.json sets default-runtime: nvidia)
docker run --rm -v /tmp:/t:ro nvidia/cuda:13.0.1-base-ubuntu24.04 /t/gputest_static

# 2. explicit --gpus all on a plain image
docker run --rm --gpus all -v /tmp:/t:ro ubuntu:24.04 /t/gputest_static

# 3. device isolation — must report exactly 1 device
docker run --rm --gpus '"device=1"' -v /tmp:/t:ro ubuntu:24.04 /t/gputest_static

# 4. env-var path used by many prebuilt images
docker run --rm -e NVIDIA_VISIBLE_DEVICES=0 -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  -v /tmp:/t:ro ubuntu:24.04 /t/gputest_static
```

Also confirm the two things users depend on most after a major bump:

```bash
# docker compose GPU reservation syntax
docker compose -f scripts/compose-gpu.yml up --abort-on-container-exit

# an OLD-major container image still runs on the new driver (backward compatibility)
docker run --rm nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi -L
```

Finally re-check the host stack and any GPU services:

```bash
zsh -lic 'which nvcc'; bash -lc 'which nvcc'     # PATH in fresh login shells
nvidia-container-cli info
systemctl is-active ollama
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```

## Expected Outcome

| Check | Expectation |
| --- | --- |
| `dkms status` | new branch built for **every** installed kernel |
| `nvidia-smi` | new driver, all GPUs present |
| `nvcc --version` | new major, resolvable in fresh login shells |
| saxpy on each GPU | max error `0.0` |
| cuBLAS SGEMM | max error `0.0` |
| 4 container paths | real kernels PASS; `device=N` isolates correctly |
| old-major image | still runs (drivers are backward compatible) |
| `dpkg -C`, `apt-get check` | clean, zero packages from the old major |

## Notes and Gotchas

- **Do not mass-upgrade PyTorch to match.** Drivers are backward compatible, so existing
  `cu118`/`cu126` wheels keep working on the new driver. Pinned envs (unsloth, older
  Python) often break if forced forward. Ask before touching envs; if the user does want
  them gone, deleting and recreating later is cleaner than in-place upgrades. New envs
  should use the matching index, e.g. `--index-url https://download.pytorch.org/whl/cu130`.
- **Conda space accounting is misleading.** `du` on `envs/` double-counts hardlinks into
  `pkgs/`. Check the parent (`du -sh ~/miniforge3`) and remember `conda clean -a` is often
  the larger win. Also confirm which filesystem actually freed space — `/home` is
  frequently a separate LV from `/`.
- **cuDNN is separate.** It is not part of `cuda-toolkit-*`. If the host had a system
  cuDNN, reinstall the matching `libcudnn9-cuda-13`; pip torch bundles its own and needs
  nothing.
- Keep every apt log (`install.log`, `fix.log`, `autoremove.log`) plus `dpkg-before.txt`
  until the user confirms the host is healthy — they make a rollback tractable.
