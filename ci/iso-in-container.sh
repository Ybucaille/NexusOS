#!/usr/bin/env bash
# Runs inside the archlinux container started by build-iso.sh.
set -euo pipefail

trap 'chown -R "$OWNER" /out /var/cache/pacman/pkg' EXIT

echo "Server = https://archive.archlinux.org/repos/$SNAPSHOT/\$repo/os/\$arch" > /etc/pacman.d/mirrorlist
pacman-retry -Syuu --noconfirm --needed archiso >/dev/null

# Signatures are checked through the repository's own keyring, as on a
# machine. Signed packages can sit in an unsigned database (local builds added
# to a fetched release), so the keyring goes in whenever there is one.
siglevel='Optional TrustAll'
if compgen -G "/repo/nexusos-keyring-*.pkg.tar.zst" >/dev/null; then
  # The image ships no local secret key, and --populate needs one to certify
  # the NexusOS key.
  pacman-key --init >/dev/null 2>&1
  # Bootstrap: the keyring cannot be checked before it is installed.
  cp /repo/nexusos-keyring-*.pkg.tar.zst /tmp/
  pacman -U --noconfirm /tmp/nexusos-keyring-*.pkg.tar.zst >/dev/null
fi
[[ ! -f /repo/nexusos.db.sig ]] || siglevel=Required
# A local build and a release can give the same file name to different bytes:
# a cached copy would fail the checksum of the repository, which wins.
for f in /repo/*.pkg.tar.zst; do rm -f "/var/cache/pacman/pkg/${f##*/}" "/var/cache/pacman/pkg/${f##*/}.sig"; done
echo "nexusos repository: SigLevel = $siglevel"

profile=/profile
cp -a /usr/share/archiso/configs/releng "$profile"
cp -a /iso/airootfs/. "$profile/airootfs/"
cat /iso/packages.x86_64 >> "$profile/packages.x86_64"
cat /iso/profiledef.sh >> "$profile/profiledef.sh"
printf '\n[nexusos]\nSigLevel = %s\nServer = file:///repo\n' "$siglevel" >> "$profile/pacman.conf"

# The live system, and the systems installed from it, keep the repository in
# /var/lib/nexusos/repo and follow the snapshot of this build (nexusos-repos.sh,
# in place of releng's live mirrors).
rm "$profile/airootfs/etc/pacman.d/hooks/uncomment-mirrors.hook"
sed -i -e "s|@SNAPSHOT@|$SNAPSHOT|" -e "s|@SIGLEVEL@|$siglevel|" "$profile/airootfs/etc/pacman.d/hooks/nexusos-repos.sh"
install -d "$profile/airootfs/var/lib/nexusos"
cp -a /repo "$profile/airootfs/var/lib/nexusos/repo"
rm -f "$profile/airootfs/var/lib/nexusos/repo/"*.old

# The installer: archinstall with the NexusOS profile and defaults.
install -Dm644 /installer/nexusos.py /installer/config.json -t "$profile/airootfs/usr/share/nexusos/installer"
install -Dm755 /installer/nexusos-install -t "$profile/airootfs/usr/local/bin"

# greetd owns tty1 instead of releng's root autologin.
rm -r "$profile/airootfs/etc/systemd/system/getty@tty1.service.d"

# A derivative must not present itself as Arch Linux: no Arch name or logo in
# the boot menus.
rm "$profile/syslinux/splash.png"
sed -i -e 's/Arch Linux install medium/NexusOS live medium/g' -e 's/MENU TITLE Arch Linux/MENU TITLE NexusOS/' \
  -e '/MENU BACKGROUND/d' "$profile"/efiboot/loader/entries/*.conf "$profile"/syslinux/*.cfg "$profile"/grub/*.cfg

# pacstrap takes its packages from this cache and gives up on a stalled
# download: fill it first, from an empty database so every dependency comes.
mkdir /tmp/isodb
pacman-retry -Syw --noconfirm --config "$profile/pacman.conf" --dbpath /tmp/isodb \
  $(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$profile/packages.x86_64") >/dev/null

# Dated like the packages, so the same snapshot gives the same version.
SOURCE_DATE_EPOCH=$(date -u -d "$SNAPSHOT" +%s) mkarchiso -w /work -o /out "$profile"

# nexusos-archinstall.hook patches archinstall: a newer archinstall that no
# longer needs it, or reads its configuration otherwise, must not pass unseen.
grep -q "mods.append(cls(Path(entry\['name'\]), mountpoint))" \
  /work/x86_64/airootfs/usr/lib/python3*/site-packages/archinstall/lib/models/device.py ||
  { echo "archinstall was not patched (nexusos-archinstall.hook)" >&2; exit 1; }
