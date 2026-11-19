#!/usr/bin/env bash
# Install the newest NexusOS ISO onto a blank QEMU disk with its installer,
# unattended, then boot the disk and check the installed system, inside a
# throwaway container. Serial logs, errors and screenshots land in
# out/iso/test/.
#
# Usage: [UPDATE=YYYY/MM/DD] ci/test-install.sh [YYYY/MM/DD] [uefi|bios ...]
#   (snapshot for QEMU and OVMF, defaults to yesterday; both firmwares by default)
#   UPDATE: then move the installed system to the release in out/repo/<date>
#   (ci/fetch-repo.sh) with nexusos-update, reboot and check it again.
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
update=()
if [[ -n ${UPDATE:-} ]]; then
  release="$root/out/repo/${UPDATE//\//-}"
  [[ -f $release/nexusos-release ]] || { echo "no release in $release, run ci/fetch-repo.sh $UPDATE first" >&2; exit 1; }
  update=(-v "$release:/update:ro")
  echo "then updating to $UPDATE"
fi

# The guest has 4 GiB. Capped, and the first to go when memory runs short,
# so a test never takes the desktop down with it.
docker run --rm --device /dev/kvm --memory "${NEXUSOS_TEST_MEMORY:-6g}" --oom-score-adj 500 -e SNAPSHOT="$snapshot" -e ISO="$(basename "$iso")" -e FIRMWARES="$firmwares" \
  -e OWNER="$(id -u):$(id -g)" -v "$root/out/iso:/iso" -v "$root/ci:/ci:ro" -v "$cache:/cache:ro" \
  -v "$root/pkgs/nexusos-base/pacman-retry:/usr/local/bin/pacman-retry:ro" \
  "${update[@]}" archlinux:latest bash -euo pipefail -c '
trap "chown -R \$OWNER /iso/test 2>/dev/null || true" EXIT
echo "Server = https://archive.archlinux.org/repos/$SNAPSHOT/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
pacman-retry -Syuu --noconfirm --needed qemu-system-x86 qemu-img qemu-hw-display-virtio-vga qemu-hw-display-virtio-gpu edk2-ovmf python openssl >/dev/null
rc=0
for firmware in $FIRMWARES; do python /ci/test-install.py "/iso/$ISO" "$firmware" /cache /iso/test $([[ -d /update ]] && echo /update) || rc=1; done
exit $rc
'
