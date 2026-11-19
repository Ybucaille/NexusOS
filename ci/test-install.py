#!/usr/bin/env python3
"""Install NexusOS from the ISO onto a blank disk with the shipped installer,
unattended, then boot the disk and check the installed system. Runs inside the
container started by test-install.sh.

Usage: test-install.py ISO uefi|bios CACHE OUTDIR [RELEASE]

CACHE is a pacman package cache served to the guest as a CacheServer, so a
run downloads only what earlier builds did not. RELEASE, a release directory
as ci/fetch-repo.sh leaves it, is then served as the update source:
nexusos-update moves the installed system to it, and the system is checked
again after a reboot.
"""
import functools
import http.server
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time

from vm import VM, Checks

iso, firmware, cache, outdir = sys.argv[1:5]
release = sys.argv[5] if len(sys.argv) > 5 else None
os.makedirs(outdir, exist_ok=True)
check = Checks(f"install-{firmware}")
# QEMU's user network reaches the container at this address.
host = "http://10.0.2.2:8080"

disk = f"/tmp/disk-{firmware}.qcow2"
subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", disk, "40G"], check=True)
drive = ["-drive", f"file={disk},if=virtio,format=qcow2"]

# Test credentials, made up for this run only.
password = secrets.token_urlsafe(12)
hashed = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password, capture_output=True,
                        text=True, check=True).stdout.strip()
creds = {"users": [{"username": "tester", "enc_password": hashed, "sudo": True}], "root_enc_password": hashed}


def size(value, unit):
    return {"value": value, "unit": unit, "sector_size": {"value": 512, "unit": "B"}}


# archinstall's default layout, which the menus would propose: a FAT /boot
# (the ESP on UEFI) and Btrfs with its default subvolumes and snapper. GPT on
# UEFI, MBR on BIOS, as archinstall picks.
boot_flags = ["boot", "esp"] if firmware == "uefi" else ["boot"]
extra = {
    "disk_config": {
        "config_type": "default_layout",
        "device_modifications": [{
            "device": "/dev/vda",
            "wipe": True,
            "partitions": [
                {"obj_id": "boot", "status": "create", "type": "primary", "start": size(1, "MiB"),
                 "size": size(1, "GiB"), "fs_type": "fat32", "mountpoint": "/boot", "mount_options": [],
                 "flags": boot_flags, "btrfs": [], "dev_path": None},
                {"obj_id": "root", "status": "create", "type": "primary", "start": size(1025, "MiB"),
                 "size": size(38, "GiB"), "fs_type": "btrfs", "mountpoint": None,
                 "mount_options": ["compress=zstd"], "flags": [], "dev_path": None,
                 "btrfs": [{"name": "@", "mountpoint": "/"}, {"name": "@home", "mountpoint": "/home"},
                           {"name": "@log", "mountpoint": "/var/log"},
                           {"name": "@pkg", "mountpoint": "/var/cache/pacman/pkg"}]},
            ],
        }],
        "btrfs_options": {"snapshot_config": {"type": "Snapper"}},
    },
    # pacstrap copied the live mirrorlist, cache server included.
    "custom_commands": ["sed -i '/^CacheServer/d' /etc/pacman.d/mirrorlist"],
}

serve = tempfile.mkdtemp()
for name, data in (("extra.json", extra), ("creds.json", creds)):
    with open(os.path.join(serve, name), "w") as f:
        json.dump(data, f)
os.symlink(cache, os.path.join(serve, "pkg"))
if release:
    os.symlink(release, os.path.join(serve, "update"))
    with open(os.path.join(release, "nexusos-release")) as f:
        target = next(line.split("=", 1)[1].strip() for line in f if line.startswith("snapshot="))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("0.0.0.0", 8080), functools.partial(Quiet, directory=serve))
threading.Thread(target=server.serve_forever, daemon=True).start()


def repositories(vm):
    """The pacman checks the live system passes too."""
    status, repos = vm.run("pacman-conf --repo-list && grep -h 'Server' /etc/pacman.d/mirrorlist && pacman-conf --repo nexusos Server")
    lines = repos.split("\n")
    check("pacman follows the snapshot and the nexusos repository",
          status == 0 and lines[0] == "nexusos" and "CacheServer" not in repos
          and "archive.archlinux.org/repos/" in repos and lines[-1] == "file:///var/lib/nexusos/repo",
          repos.replace("\n", " | "))
    status, updates = vm.run("pacman -Sy --noprogressbar >/dev/null && ! pacman -Qu", 300)
    check("repositories sync, nothing to upgrade", status == 0, updates)


def started(vm):
    _, state = vm.run("systemctl is-system-running --wait", 300)
    _, failed = vm.run("systemctl --failed --no-legend --plain")
    check("system started", state in ("running", "degraded") and not failed, state + (": " + failed if failed else ""))


def desktop(vm, name):
    """Open the session of the installed user and check the desktop starts.

    Logging in through tuigreet takes a keyboard: greetd opens the session
    itself instead, as the live medium does. Its run file marks the initial
    session as done for this boot."""
    run = vm.run
    run("cp /etc/greetd/config.toml /root/greetd.toml && printf '\\n[initial_session]\\ncommand = \"/usr/bin/nexusos-session\"\\n"
        "user = \"tester\"\\n' >> /etc/greetd/config.toml && rm -f /run/greetd.run && systemctl restart greetd")
    status, _ = run("for i in $(seq 300); do pgrep -x Hyprland >/dev/null && break; sleep 1; done; pgrep -x Hyprland", 330)
    check("Hyprland running", status == 0)
    status, shell = run("for i in $(seq 120); do pgrep -f 'qs .*caelestia' >/dev/null && break; sleep 1; done; pgrep -af 'qs .*caelestia'", 150)
    check("Caelestia shell running", status == 0, shell)
    run("cp /root/greetd.toml /etc/greetd/config.toml")
    _, packages = run("pacman -Q nexusos-base nexusos-desktop nexusos-keyring linux limine snapper 2>/dev/null")
    print(packages, flush=True)
    _, errors = run("journalctl -b -p err --no-pager -o cat | tail -n 40")
    with open(os.path.join(outdir, f"errors-{name}.log"), "w") as f:
        f.write(errors + "\n")
    time.sleep(10)
    print(f"[install-{firmware}] screenshot: {vm.screendump(os.path.join(outdir, f'desktop-{name}.png'))}", flush=True)


def install(last):
    """First boot: the live medium installs onto the disk. Returns whether it
    did, or None when a download from the archive failed and another try may
    pass: archinstall stops at the first failed pacstrap when unattended (its
    menus would offer to try again)."""
    vm = VM(f"live-{firmware}", firmware, drive + ["-cdrom", iso, "-boot", "d"],
            os.path.join(outdir, f"install-{firmware}.log"))
    try:
        vm.login()
        vm.run("systemctl is-system-running --wait", 300)
        status, _ = vm.run(f"curl -sfO {host}/extra.json && curl -sfO {host}/creds.json"
                           f" && sed -i '1i CacheServer = {host}/pkg' /etc/pacman.d/mirrorlist")
        check("test files fetched", status == 0)
        # The shipped defaults, with the disk and the cache cleanup on top.
        vm.run("""python -c 'import json; c = json.load(open("/usr/share/nexusos/installer/config.json"));"""
               """ c.update(json.load(open("extra.json"))); json.dump(c, open("test.json", "w"))'""")
        start = time.monotonic()
        status, _ = vm.run("nexusos-install --config test.json --creds creds.json --silent >/dev/null 2>&1", 5400)
        _, log = vm.run("tail -n 40 /var/log/archinstall/install.log")
        # archinstall can stop early and still exit 0.
        done = status == 0 and "Installation completed without any errors" in log
        stalled, _ = vm.run("grep -qE 'failed retrieving file|failed to retrieve some files' /var/log/archinstall/install.log")
        if not done and stalled == 0 and not last:
            print(f"[install-{firmware}] a download from the archive failed, installing again", flush=True)
            return None
        check("unattended installation", done, f"{time.monotonic() - start:.0f}s")
        if not done:
            print(log, flush=True)
        vm.send("systemctl poweroff\n")
        vm.wait_poweroff(120)
        return done
    except TimeoutError as error:
        check("live session", False, str(error))
        return False
    finally:
        vm.kill()


for attempt in range(3):
    installed = install(last=attempt == 2)
    if installed is not None:
        break
    # A blank machine again: new disk, firmware variables as shipped.
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", disk, "40G"], check=True)
    if os.path.exists(f"/tmp/vars-live-{firmware}.fd"):
        os.remove(f"/tmp/vars-live-{firmware}.fd")
if not installed:
    sys.exit(1)

# Second boot: the installed system, from the disk alone.
vm = VM(f"disk-{firmware}", firmware, drive, os.path.join(outdir, f"installed-{firmware}.log"),
        vars_path=f"/tmp/vars-live-{firmware}.fd")
run = vm.run
try:
    vm.login(password)
    check("boot from disk to a login prompt", True)
    started(vm)
    status, osid = run(". /etc/os-release && echo $ID")
    check("NexusOS identity", status == 0 and osid == "nexusos", osid)
    status, entries = run("grep -rh '^/' /boot --include limine.conf")
    check("boot menu names NexusOS", status == 0 and entries and "Arch" not in entries, entries.replace("\n", " | "))
    # Not snapper: archinstall 4.5 reads subvolume names from a configuration
    # file as strings, never equal to its Path("@"), and drops the snapshot
    # setting. Its menus build Paths, so an interactive install gets snapper.
    status, root = run("findmnt -rno FSTYPE,OPTIONS /")
    check("Btrfs root subvolume", status == 0 and root.startswith("btrfs ") and "subvol=/@" in root, root)
    repositories(vm)
    status, _ = run("for i in $(seq 60); do pgrep -x tuigreet >/dev/null && break; sleep 1; done; pgrep -x tuigreet", 90)
    check("greetd shows tuigreet", status == 0)
    time.sleep(5)
    print(f"[install-{firmware}] screenshot: {vm.screendump(os.path.join(outdir, f'greeter-{firmware}.png'))}", flush=True)

    desktop(vm, f"installed-{firmware}")

    if release:
        # The newer release, from the test server in place of the published one.
        run(f"sed -i 's|^source *=.*|source = {host}/update|' /etc/nexusos/update.conf")
        start = time.monotonic()
        status, _ = run("nexusos-update --yes >/root/update.log 2>&1", 1800)
        _, log = run("tail -n 30 /root/update.log")
        check(f"nexusos-update to {target}", status == 0 and f"now on snapshot {target}" in log,
              f"{time.monotonic() - start:.0f}s")
        print(log, flush=True)
        vm.send("systemctl reboot\n")
        vm.login(password)
        check("reboot after the update", True)
        started(vm)
        status, state = run("grep -h '^Server' /etc/pacman.d/mirrorlist; grep -h '^snapshot=' /var/lib/nexusos/repo/nexusos-release;"
                            " sed -n '/^\\[nexusos\\]$/,/^\\[/ s/^SigLevel.*/&/p' /etc/pacman.conf;"
                            " test ! -e /var/lib/nexusos/update-pending")
        check(f"snapshot {target}, signed nexusos repository, nothing pending",
              status == 0 and f"/repos/{target}/" in state and f"snapshot={target}" in state
              and "SigLevel = Required" in state, state.replace("\n", " | "))
        repositories(vm)
        status, out = run("nexusos-update --check", 120)
        check("nexusos-update finds nothing newer", status == 2, out)
        desktop(vm, f"updated-{firmware}")
except TimeoutError as error:
    check("serial console", False, str(error))
finally:
    vm.kill()
    server.shutdown()

sys.exit(0 if check.passed() else 1)
