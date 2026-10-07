# Host environment for NVIDIA Jetson Linux (L4T) flashing tools.
# The L4T tools support Ubuntu 22.04/24.04 x86_64 hosts only; this image lets
# any Linux host with Docker run them.
# Package list mirrors Linux_for_Tegra/tools/l4t_flash_prerequisites.sh (r39.2.1).
# Re-check that script when moving to a new L4T release.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      abootimg binfmt-support binutils cpio cpp \
      device-tree-compiler dosfstools e2fsprogs file gdisk \
      iproute2 iputils-ping lbzip2 libxml2-utils \
      netcat-openbsd nfs-kernel-server openssl \
      parted python3-yaml qemu-user-static rsync sshpass \
      udev usbutils uuid-runtime whois xmlstarlet xxd zstd zlib1g python3-usb \
      lz4 python-is-python3 \
      sudo ca-certificates bzip2 xz-utils tar kmod openssh-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
