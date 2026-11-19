#!/usr/bin/env bash
# Install the newest NexusOS ISO onto a blank QEMU disk with its installer,
# unattended, then boot the disk and check the installed system, inside a
# throwaway container. Serial logs, errors and screenshots land in
# out/iso/test/.
#
# Usage: ci/test-install.sh [YYYY/MM/DD] [uefi|bios ...]
#   (snapshot for QEMU and OVMF, defaults to yesterday; both firmwares by default)
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
snapshot=${1:-$(date -u -d yesterday +%Y/%m/%d)}
shift || true
firmwares=${*:-uefi bios}
iso=$(ls -t "$root"/out/iso/nexusos-*.iso 2>/dev/null | head -n1)
[[ -n $iso ]] || { echo "no ISO in $root/out/iso, run ci/build-iso.sh first" >&2; exit 1; }
cache="$root/out/cache/pacman"
mkdir -p "$cache"
echo "installing $(basename "$iso")"

docker run --rm --device /dev/kvm -e SNAPSHOT="$snapshot" -e ISO="$(basename "$iso")" -e FIRMWARES="$firmwares" \
  -e OWNER="$(id -u):$(id -g)" -v "$root/out/iso:/iso" -v "$root/ci:/ci:ro" -v "$cache:/cache:ro" \
  archlinux:latest bash -euo pipefail -c '
trap "chown -R \$OWNER /iso/test 2>/dev/null || true" EXIT
echo "Server = https://archive.archlinux.org/repos/$SNAPSHOT/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
pacman -Syuu --noconfirm --needed qemu-system-x86 qemu-img qemu-hw-display-virtio-vga qemu-hw-display-virtio-gpu edk2-ovmf python openssl >/dev/null
rc=0
for firmware in $FIRMWARES; do python /ci/test-install.py "/iso/$ISO" "$firmware" /cache /iso/test || rc=1; done
exit $rc
'
