# Live ISO

The NexusOS live medium is archiso's `releng` profile, taken from the archiso
package of the same Arch snapshot, with this directory laid over it:

| File | Change |
|---|---|
| `packages.x86_64` | appended: `nexusos-keyring`, `nexusos-desktop`, `pipewire-jack` |
| `profiledef.sh` | appended: NexusOS name and label, zstd squashfs, permissions |
| `airootfs/` | copied over releng's: live user, greetd, graphical target |

The build also removes releng's root autologin on tty1 (greetd owns it), and
the Arch name and logo from the boot menus.

## Live session

At every boot `nexusos-live.service` creates the passwordless user `nexus`
(wheel, sudo without password) and points greetd's `initial_session` at
`nexusos-session`: the desktop opens without a login. After a logout, greetd
shows tuigreet as on an installed system. root keeps releng's empty password on
the other TTYs.

## Building and testing

```bash
ci/build-repo.sh 2026/11/17     # or download the repo-2026-11-17 release into out/repo/2026-11-17
ci/build-iso.sh 2026/11/17      # out/iso/nexusos-2026.11.17-x86_64.iso
ci/test-iso.sh 2026/11/17       # boots it in QEMU, UEFI then BIOS
```

The build runs in a throwaway container with `CAP_SYS_ADMIN` (pacstrap mounts
`/proc`, `/sys` and `/dev`), never fully privileged. A signed repository is
checked through `nexusos-keyring`, as on a machine. Downloaded packages stay in
`out/cache/pacman/` for the next build.

The test boots the ISO with KVM and passes `getty.ttys.serial=ttyS0` as an
SMBIOS credential: systemd opens a root shell on the serial port without any
test-only change to the image. It checks that the system starts, greetd runs,
the live user is logged in, Hyprland and the Caelestia shell run and the
settings portal answers, then saves the serial log, the journal's errors and a
screenshot of each boot in `out/iso/test/`.

A desktop running in QEMU proves the image works, not that it is fluid: that
is measured on hardware (see `bench/`).

## Known issues

- UEFI only: `systemd-loop@…sr0` fails, systemd finding no partition table on
  the CD. Harmless; the test tolerates this unit and no other.
- `xdg-desktop-portal-gtk` fails a few times on the first login, started while
  the dots deploy, before Hyprland provides a display. The portals answer once
  the session runs.
- The 2026/11/17 snapshot brings Hyprland 0.57.2, while the dots were
  validated with 0.56.2: Hyprland warns that `group.lock_active()` is
  deprecated (now `group.lock()`). Hyprland is not pinned by NexusOS yet.
- The shell shows the Arch logo, read from `os-release`: branding comes with
  `nexusos-base`.
