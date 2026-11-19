#!/usr/bin/env bash
# Boot the newest NexusOS ISO in QEMU (UEFI, then BIOS) inside a throwaway
# container and check the live desktop starts. Serial logs, errors and a
# screenshot of each boot land in out/iso/test/.
#
# Usage: ci/test-iso.sh [YYYY/MM/DD]   (snapshot for QEMU and OVMF, defaults to yesterday)
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
snapshot=${1:-$(date -u -d yesterday +%Y/%m/%d)}
iso=$(ls -t "$root"/out/iso/nexusos-*.iso 2>/dev/null | head -n1)
[[ -n $iso ]] || { echo "no ISO in $root/out/iso, run ci/build-iso.sh first" >&2; exit 1; }
echo "testing $(basename "$iso")"

# The guest has 4 GiB. Capped, and the first to go when memory runs short,
# so a test never takes the desktop down with it.
docker run --rm --device /dev/kvm --memory "${NEXUSOS_TEST_MEMORY:-6g}" --oom-score-adj 500 -e SNAPSHOT="$snapshot" -e ISO="$(basename "$iso")" -e OWNER="$(id -u):$(id -g)" \
  -v "$root/out/iso:/iso" -v "$root/ci:/ci:ro" -v "$root/pkgs/nexusos-base/pacman-retry:/usr/local/bin/pacman-retry:ro" archlinux:latest bash -euo pipefail -c '
trap "chown -R \$OWNER /iso/test 2>/dev/null || true" EXIT
echo "Server = https://archive.archlinux.org/repos/$SNAPSHOT/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
pacman-retry -Syuu --noconfirm --needed qemu-system-x86 qemu-hw-display-virtio-vga qemu-hw-display-virtio-gpu edk2-ovmf python >/dev/null
rc=0
for firmware in uefi bios; do python /ci/test-iso.py "/iso/$ISO" "$firmware" /iso/test || rc=1; done
exit $rc
'
