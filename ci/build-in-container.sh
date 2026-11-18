#!/usr/bin/env bash
# Runs inside the archlinux container started by build-repo.sh.
set -euo pipefail

# Pin every Arch repository to the snapshot date; -Syuu also downgrades
# whatever the image ships that is newer than the snapshot.
echo "Server = https://archive.archlinux.org/repos/$SNAPSHOT/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
cat >> /etc/pacman.conf <<'EOF'

[nexusos]
SigLevel = Optional TrustAll
Server = file:///repo
EOF
[[ -f /repo/nexusos.db.tar.zst ]] || repo-add -q /repo/nexusos.db.tar.zst
pacman -Syuu --noconfirm --needed base-devel git >/dev/null

# makepkg refuses to run as root.
useradd -m builder
echo 'builder ALL=(ALL) NOPASSWD: /usr/bin/pacman' > /etc/sudoers.d/builder

for pkg in $PACKAGES; do
  echo "==> $pkg"
  cp -r "/pkgs/$pkg" "/home/builder/$pkg"
  chown -R builder: "/home/builder/$pkg"
  (cd "/home/builder/$pkg" && sudo -u builder PKGDEST=/home/builder/out makepkg -s --noconfirm --noprogressbar)
  for file in /home/builder/out/"$pkg"-*.pkg.tar.zst; do
    cp "$file" /repo/
    repo-add -q -R /repo/nexusos.db.tar.zst "/repo/$(basename "$file")"
  done
  rm -f /home/builder/out/*
  pacman -Sy --noconfirm >/dev/null
done
