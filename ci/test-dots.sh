#!/usr/bin/env bash
# Check the pinned dots deployment in a throwaway container: first install,
# the sh fast path, then an update from an older revision.
#
# Usage: ci/test-dots.sh [YYYY/MM/DD]   (snapshot of an existing out/repo build)
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
snapshot=${1:-$(date -u -d yesterday +%Y/%m/%d)}
repo="$root/out/repo/${snapshot//\//-}"
[[ -f $repo/nexusos.db ]] || { echo "no repository at $repo, run ci/build-repo.sh first" >&2; exit 1; }

docker run --rm -e SNAPSHOT="$snapshot" -v "$repo:/repo:ro" archlinux:latest bash -euo pipefail -c '
echo "Server = https://archive.archlinux.org/repos/$SNAPSHOT/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
printf "\n[nexusos]\nSigLevel = Optional TrustAll\nServer = file:///repo\n" >> /etc/pacman.conf
# Signatures verify through the keyring package of the repository. Signed
# packages can sit in an unsigned database (local builds added to a fetched
# release), so the keyring goes in whenever there is one.
if compgen -G "/repo/nexusos-keyring-*.pkg.tar.zst" >/dev/null; then
  pacman-key --init >/dev/null 2>&1
  # Bootstrap: the keyring cannot be checked before it is installed.
  cp /repo/nexusos-keyring-*.pkg.tar.zst /tmp/
  pacman -U --noconfirm /tmp/nexusos-keyring-*.pkg.tar.zst >/dev/null
fi
if [[ -f /repo/nexusos.db.sig ]]; then
  sed -i "s/^SigLevel = Optional TrustAll$/SigLevel = Required/" /etc/pacman.conf
  echo "== signatures required"
fi
pacman -Syuu --noconfirm >/dev/null
# The engine needs the CLI and git; the desktop packages themselves are not needed.
pacman -S --noconfirm --needed caelestia-cli caelestia-dots git xdg-user-dirs >/dev/null
pacman -Sdd --noconfirm nexusos-desktop >/dev/null
useradd -m tester

as_tester() { su tester -c "$1"; }
state=/home/tester/.local/state/caelestia/dots-state.json

echo "== first install"
as_tester "/usr/lib/nexusos/dots install" >/tmp/install.log 2>&1 || { cat /tmp/install.log; exit 1; }
test -f "$state"
test -f /home/tester/.config/hypr/hyprland.lua || test -d /home/tester/.config/hypr
grep -q "\"applied_rev\": \"$(cat /usr/share/caelestia-dots/REV)\"" "$state"
echo "deployed files: $(python -c "import json;print(len(json.load(open(\"$state\"))[\"deployed_files\"]))")"

echo "== fast path: nothing pending"
if as_tester /usr/lib/nexusos/dots-pending; then echo "dots-pending should fail when up to date"; exit 1; fi

echo "== update from an older revision"
old=$(git -C /usr/share/caelestia-dots/caelestia.git rev-parse main~10)
as_tester "sed -i \"s/\\\"applied_rev\\\": \\\"[0-9a-f]*\\\"/\\\"applied_rev\\\": \\\"$old\\\"/\" $state"
as_tester /usr/lib/nexusos/dots-pending
as_tester "/usr/lib/nexusos/dots update" >/tmp/update.log 2>&1 || { cat /tmp/update.log; exit 1; }
grep -q "\"applied_rev\": \"$(cat /usr/share/caelestia-dots/REV)\"" "$state"
if as_tester /usr/lib/nexusos/dots-pending; then echo "dots-pending should fail after update"; exit 1; fi
tail -n 5 /tmp/update.log

echo "== ok"
'
