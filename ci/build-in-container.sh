#!/usr/bin/env bash
# Runs inside the archlinux container started by build-repo.sh.
set -euo pipefail

# Hand the repository back to the host user instead of root, even on failure.
trap 'chown -R "$OWNER" /repo' EXIT

# Pin every Arch repository to the snapshot date; -Syuu also downgrades
# whatever the image ships that is newer than the snapshot.
echo "Server = https://archive.archlinux.org/repos/$SNAPSHOT/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
# The build keys look versions up in the Arch repositories alone.
cp /etc/pacman.conf /etc/pacman-arch.conf
cat >> /etc/pacman.conf <<'EOF'

# The container trusts what it builds itself; a signed database would otherwise
# need the key in pacman's keyring.
[nexusos]
SigLevel = Never
Server = file:///repo
EOF
[[ -f /repo/nexusos.db.tar.zst ]] || repo-add -q /repo/nexusos.db.tar.zst
pacman-retry -Syuu --noconfirm --needed base-devel git >/dev/null

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

# makepkg refuses to run as root. It installs the build dependencies through
# pacman-retry too.
useradd -m builder
echo 'builder ALL=(ALL) NOPASSWD: /usr/bin/pacman, /usr/local/bin/pacman-retry' > /etc/sudoers.d/builder

# Packages that only copy files: their runtime dependencies are not needed to
# build them, and nexusos-desktop's would pull the whole desktop in.
nodeps=" caelestia-dots nexusos-desktop "

# The build key of a package changes when its recipe does, or when anything it
# depends on does: Qt, Python and the like have no stable ABI for what is built
# against them, and a rebuilt package forces the ones that depend on it. Those
# built earlier in this run are in keys; the other repository packages count
# with the version the snapshot offers (virtual names resolve through provides).
declare -A keys
build_key() {
  local pkg=$1 srcinfo recipe dep ver
  # Explicit returns: errexit does not reach into a command substitution.
  srcinfo=$(sudo -u builder bash -c 'cd "$1" && makepkg --printsrcinfo' _ "/home/builder/$pkg") || return 1
  recipe=$(cd "/pkgs/$pkg" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1) || return 1
  {
    echo "recipe $recipe"
    while read -r dep; do
      [[ -n $dep ]] || continue
      if [[ " $ORDER " == *" $dep "* ]]; then
        [[ -z ${keys[$dep]-} ]] || echo "package $dep ${keys[$dep]}"
      else
        ver=$(pacman --config /etc/pacman-arch.conf -Sddp --noconfirm --print-format '@@%n %v' "$dep" 2>/dev/null | sed -n 's/^.*@@//p' | head -n1 || true)
        echo "depend ${ver:-$dep}"
      fi
    done < <(sed -n -E 's/^[[:space:]]*(make)?depends(_[A-Za-z0-9_]+)? = //p' <<<"$srcinfo" | sed 's/[<>=].*//' | sort -u)
  } | sha256sum | cut -d' ' -f1
}

# Succeeds, with the file names in files, when the previous build holds this
# package under the same key and each of its files carries a valid signature of
# our key. An unsigned build never reuses anything.
files=()
reusable() {
  local pkg=$1 pkgkey=$2 line f
  local -a fields
  [[ -n ${REUSE:-} && -n ${key:-} && -f /reuse/nexusos-buildkeys ]] || return 1
  line=$(awk -v p="$pkg" '$1 == p {print; exit}' /reuse/nexusos-buildkeys)
  read -ra fields <<<"$line"
  (( ${#fields[@]} > 2 )) && [[ ${fields[1]} == "$pkgkey" ]] || return 1
  files=("${fields[@]:2}")
  for f in "${files[@]}"; do
    [[ $f =~ ^[A-Za-z0-9._-]+\.pkg\.tar\.zst$ && -f /reuse/$f && -f /reuse/$f.sig ]] || return 1
    gpg --batch --quiet --verify "/reuse/$f.sig" "/reuse/$f" 2>/dev/null || return 1
  done
}

# One line per package of this run, for the next build to decide what it can
# reuse. It needs no signature: each package taken from it is verified.
: > /repo/nexusos-buildkeys

for pkg in $PACKAGES; do
  cp -r "/pkgs/$pkg" "/home/builder/$pkg"
  chown -R builder: "/home/builder/$pkg"
  keys[$pkg]=$(build_key "$pkg")
  if reusable "$pkg" "${keys[$pkg]}"; then
    echo "==> $pkg (reused)"
    for file in "${files[@]}"; do cp "/reuse/$file" "/reuse/$file.sig" /repo/; done
  else
    echo "==> $pkg"
    files=()
    deps=-s
    [[ $nodeps == *" $pkg "* ]] && deps=-d
    (cd "/home/builder/$pkg" && sudo -u builder PKGDEST=/home/builder/out PACMAN=pacman-retry makepkg "$deps" --noconfirm --noprogressbar)
    # A split recipe produces several packages; publish all of them.
    for file in /home/builder/out/*.pkg.tar.zst; do
      cp "$file" /repo/
      files+=("$(basename "$file")")
      # repo-add records the package signature, so it must exist first.
      [[ -z ${key:-} ]] || gpg --batch --yes --detach-sign -u "$key!" "/repo/${files[-1]}"
    done
    rm -f /home/builder/out/*
  fi
  for file in "${files[@]}"; do repo-add -q -R "${sign[@]}" /repo/nexusos.db.tar.zst "/repo/$file"; done
  echo "$pkg ${keys[$pkg]} ${files[*]}" >> /repo/nexusos-buildkeys
  pacman-retry -Sy --noconfirm >/dev/null
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
