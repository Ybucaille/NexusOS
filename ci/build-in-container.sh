#!/usr/bin/env bash
# Runs inside the archlinux container started by build-repo.sh.
set -euo pipefail

# Hand the repository back to the host user instead of root, even on failure.
trap 'chown -R "$OWNER" /repo' EXIT

# Pin every Arch repository to the snapshot date; -Syuu also downgrades
# whatever the image ships that is newer than the snapshot.
echo "Server = https://archive.archlinux.org/repos/$SNAPSHOT/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
cat >> /etc/pacman.conf <<'EOF'

# The container trusts what it builds itself; a signed database would otherwise
# need the key in pacman's keyring.
[nexusos]
SigLevel = Never
Server = file:///repo
EOF
[[ -f /repo/nexusos.db.tar.zst ]] || repo-add -q /repo/nexusos.db.tar.zst
pacman -Syuu --noconfirm --needed base-devel git >/dev/null

# The signing subkey stays with root: upstream build code runs as builder and
# never sees it.
sign=()
if [[ -n ${NEXUSOS_SIGNING_KEY:-} ]]; then
  export GNUPGHOME=/root/signing
  mkdir -m 700 "$GNUPGHOME"
  gpg --batch --quiet --import <<<"$NEXUSOS_SIGNING_KEY"
  unset NEXUSOS_SIGNING_KEY
  key=$(gpg -K --with-colons | awk -F: '/^ssb/{s = ($12 ~ /s/)} /^fpr/ && s {k = $10; s = 0} END {print k}')
  sign=(-s -k "$key")
  echo "signing with subkey $key"
fi

# Unsigned additions to a signed repository (a fetched release) would leave
# the old database signatures behind, and pacman would reject the database.
[[ -n ${key:-} ]] || rm -f /repo/nexusos.db*.sig /repo/nexusos.files*.sig

# makepkg refuses to run as root.
useradd -m builder
echo 'builder ALL=(ALL) NOPASSWD: /usr/bin/pacman' > /etc/sudoers.d/builder

# Packages that only copy files: their runtime dependencies are not needed to
# build them, and nexusos-desktop's would pull the whole desktop in.
nodeps=" caelestia-dots nexusos-desktop "

for pkg in $PACKAGES; do
  echo "==> $pkg"
  cp -r "/pkgs/$pkg" "/home/builder/$pkg"
  chown -R builder: "/home/builder/$pkg"
  deps=-s
  [[ $nodeps == *" $pkg "* ]] && deps=-d
  (cd "/home/builder/$pkg" && sudo -u builder PKGDEST=/home/builder/out makepkg "$deps" --noconfirm --noprogressbar)
  # A split recipe produces several packages; publish all of them.
  for file in /home/builder/out/*.pkg.tar.zst; do
    cp "$file" /repo/
    file="/repo/$(basename "$file")"
    # repo-add records the package signature, so it must exist first.
    [[ -z ${key:-} ]] || gpg --batch --yes --detach-sign -u "$key!" "$file"
    repo-add -q -R "${sign[@]}" /repo/nexusos.db.tar.zst "$file"
  done
  rm -f /home/builder/out/*
  pacman -Sy --noconfirm >/dev/null
done

# The release file ties the database, and through its checksums every package,
# to the Arch snapshot it was built and tested against. nexusos-update and
# fetch-repo.sh check its signature, then the database against it.
cat > /repo/nexusos-release <<END
snapshot=$SNAPSHOT
database=$(sha256sum < /repo/nexusos.db.tar.zst | cut -d' ' -f1)
END
rm -f /repo/nexusos-release.sig
[[ -z ${key:-} ]] || gpg --batch --yes --detach-sign -u "$key!" /repo/nexusos-release
