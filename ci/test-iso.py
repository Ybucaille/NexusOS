#!/usr/bin/env python3
"""Boot the NexusOS ISO in QEMU and check the live system from a root shell
on the serial console. Runs inside the container started by test-iso.sh.

Usage: test-iso.py ISO uefi|bios OUTDIR
"""
import os
import sys
import time

from vm import VM, Checks

iso, firmware, outdir = sys.argv[1:4]
os.makedirs(outdir, exist_ok=True)

vm = VM(f"iso-{firmware}", firmware, ["-cdrom", iso, "-boot", "d"], os.path.join(outdir, f"serial-{firmware}.log"))
check = Checks(firmware)
run = vm.run

try:
    vm.login()
    check("boot to a login prompt", True)

    _, state = run("systemctl is-system-running --wait", 300)
    # Known: on UEFI, systemd tries to attach the CD as a loop device and finds
    # no partition table on it. Any other failed unit fails the test.
    _, failed = run("systemctl --failed --no-legend --plain | grep -v '^systemd-loop@'")
    check("system started", state in ("running", "degraded") and not failed, state + (": " + failed if failed else ""))
    status, osid = run(". /etc/os-release && echo $ID")
    check("NexusOS identity", status == 0 and osid.strip() == "nexusos", osid.strip())
    status, repos = run("pacman-conf --repo-list && grep -h '^Server' /etc/pacman.d/mirrorlist && pacman-conf --repo nexusos Server")
    check("pacman follows the snapshot and the nexusos repository",
          status == 0 and repos.split("\n")[0] == "nexusos" and "archive.archlinux.org/repos/" in repos
          and repos.endswith("file:///var/lib/nexusos/repo"), repos.replace("\n", " | "))
    # Signatures checked through nexusos-keyring; nothing newer than the image.
    status, updates = run("pacman -Sy --noprogressbar >/dev/null && ! pacman -Qu", 300)
    check("repositories sync, nothing to upgrade", status == 0, updates)
    status, _ = run("systemctl is-active greetd")
    check("greetd active", status == 0)
    status, sessions = run(
        "for i in $(seq 120); do loginctl list-sessions --no-legend | grep -q nexus && break; sleep 1; done;"
        " loginctl list-sessions --no-legend | grep nexus", 150)
    check("live user logged in", status == 0, sessions)
    status, _ = run("for i in $(seq 300); do pgrep -x Hyprland >/dev/null && break; sleep 1; done; pgrep -x Hyprland", 330)
    check("Hyprland running", status == 0)
    status, shell = run("for i in $(seq 120); do pgrep -f 'qs .*caelestia' >/dev/null && break; sleep 1; done; pgrep -af 'qs .*caelestia'", 150)
    check("Caelestia shell running", status == 0, shell)
    # Portals start before Hyprland on the first login; they must answer once
    # the session runs (file pickers, dark mode).
    user = "sudo -u nexus env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"
    status, scheme = run(f"{user} busctl --user call org.freedesktop.portal.Desktop /org/freedesktop/portal/desktop"
                         " org.freedesktop.portal.Settings ReadOne ss org.freedesktop.appearance color-scheme", 60)
    check("settings portal answers", status == 0, scheme)
    _, deprecated = run(f"{user} hyprctl -i 0 deprecated-config 2>&1 | head -n 20")
    print(f"[{firmware}] hyprctl deprecated-config:\n{deprecated}", flush=True)
    _, packages = run("pacman -Q nexusos-base nexusos-desktop nexus-shell quickshell-git hyprland 2>/dev/null")
    print(packages, flush=True)
    _, errors = run("journalctl -b -p err --no-pager -o cat | tail -n 40")
    _, user = run("journalctl -b _UID=1000 -p warning --no-pager -o short-monotonic | tail -n 80")
    with open(os.path.join(outdir, f"errors-{firmware}.log"), "w") as f:
        f.write(errors + "\n\n== live user, warnings and above\n" + user + "\n")
    time.sleep(10)
    screen = os.path.join(outdir, f"screen-{firmware}.png")
    reply = vm.screendump(screen)
    print(f"[{firmware}] screenshot: {screen} {reply}", flush=True)
except TimeoutError as error:
    check("serial console", False, str(error))
finally:
    vm.kill()

sys.exit(0 if check.passed() else 1)
