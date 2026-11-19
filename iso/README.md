# Live ISO

The NexusOS live medium is archiso's `releng` profile, taken from the archiso
package of the same Arch snapshot, with this directory laid over it:

| File | Change |
|---|---|
| `packages.x86_64` | appended: `nexusos-keyring`, `nexusos-desktop`, `pipewire-jack` |
| `profiledef.sh` | appended: NexusOS name and label, zstd squashfs, permissions |
| `airootfs/` | copied over releng's: live user, greetd, graphical target, build hooks (package sources, archinstall fix) |

The build also removes releng's root autologin on tty1 (greetd owns it), and
the Arch name and logo from the boot menus, and adds the installer
([installer/](../installer/)) and the nexusos repository it was built from, in
`/var/lib/nexusos/repo`.

## Package sources

releng's live mirrors would install packages newer than the ones NexusOS was
tested with. A build-time pacman hook (`nexusos-repos.hook`, removed from the
image like releng's) writes instead:

- `/etc/pacman.d/mirrorlist`: the Arch Linux Archive snapshot of the build;
- `/etc/pacman.conf`: `[nexusos]` above `[core]`, from `/var/lib/nexusos/repo`,
  with the build's signature level (`Required` for a signed release).

The installer copies both to the installed system.

A second build hook, `nexusos-archinstall.hook`, fixes archinstall 4.5, which
drops the snapper setting of a configuration file (subvolume names read as
strings, compared with `Path("@")`). The build fails if the image does not
carry the fix, so a newer archinstall is looked at before the patch goes.

## Live session

At every boot `nexusos-live.service` creates the passwordless user `nexus`
(wheel, sudo without password) and points greetd's `initial_session` at
`nexusos-session`: the desktop opens without a login. After a logout, greetd
shows tuigreet as on an installed system. root keeps releng's empty password on
the other TTYs.

## Building and testing

```bash
ci/fetch-repo.sh 2026/11/17     # the signed repo-2026-11-17 release, or ci/build-repo.sh for a local build
ci/build-iso.sh 2026/11/17      # out/iso/nexusos-2026.11.17-x86_64.iso
ci/test-iso.sh 2026/11/17       # boots it in QEMU, UEFI then BIOS
ci/test-install.sh 2026/11/17   # installs it onto a QEMU disk, then boots that (installer/)
```

`ci/fetch-repo.sh` checks every signature against the key committed in
`pkgs/nexusos-keyring`, not against the release's own keyring package, and
fetches only the packages the database lists.

The build runs in a throwaway container with `CAP_SYS_ADMIN` (pacstrap mounts
`/proc`, `/sys` and `/dev`), never fully privileged. A signed repository is
checked through `nexusos-keyring`, as on a machine. Downloaded packages stay in
`out/cache/pacman/` for the next build.

The test boots the ISO with KVM and passes `getty.ttys.serial=ttyS0` as an
SMBIOS credential: systemd opens a root shell on the serial port without any
test-only change to the image. It checks that the system starts and
identifies as NexusOS, pacman follows the snapshot and syncs with nothing to
upgrade, greetd runs, the live user is logged in, Hyprland and
the Caelestia shell run and the settings portal answers, then saves the serial
log, the journal's errors and a screenshot of each boot in `out/iso/test/`.

A desktop running in QEMU proves the image works, not that it is fluid: that
is measured on hardware (see `bench/`).

## Known issues

- UEFI only: `systemd-loop@…sr0` fails, systemd finding no partition table on
  the CD. Harmless; the test tolerates this unit and no other.
- `xdg-desktop-portal-gtk` fails a few times on the first login, started while
  the dots deploy, before Hyprland provides a display. The portals answer once
  the session runs.
- The 2026/11/17 snapshot brings Hyprland 0.57.2, while the dots were
  validated with 0.56.2. `caelestia-dots` 4a635c6-2 picks upstream `d4c24f8`
  for the deprecated `group.lock_active()`; Hyprland reports no deprecated
  option since.
