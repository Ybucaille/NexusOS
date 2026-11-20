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
import base64
import functools
import http.server
import json
import os
import re
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
    # Both channels, so that the source is a channel whatever the test picks.
    for channel in ("stable", "testing"):
        os.symlink(release, os.path.join(serve, channel))
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


def installed_since(vm, pkg, version):
    """Whether the package installed (in the booted system) is this version or newer."""
    status, _ = vm.run(f"test \"$(vercmp \"$(pacman -Q {pkg} | cut -d' ' -f2)\" {version})\" -ge 0")
    return status == 0


def base_since(vm, version):
    """Whether the nexusos-base installed (in the booted system) is this version or newer."""
    return installed_since(vm, "nexusos-base", version)


def shell_ipc(vm, args, timeout=60):
    """An IPC call to the desktop shell, from the root console, as the user whose session runs it.

    The console has no display, so the instance is not filtered by one (--any-display)."""
    return vm.run("runuser -u tester -- env XDG_RUNTIME_DIR=/run/user/$(id -u tester)"
                  f" timeout {timeout - 5} qs -c caelestia ipc --any-display call {args}", timeout)


def polkit_test_rule(vm):
    """pkexec needs an authentication agent, which nobody answers here: let tester through."""
    rule = ("// Only in the test VM: no agent to ask in the test session.\n"
            "polkit.addRule(function(action, subject) {\n"
            "    if ((action.id == \"org.nexusos.update\" || action.id == \"org.nexusos.rollback\")\n"
            "            && subject.user == \"tester\")\n"
            "        return polkit.Result.YES;\n"
            "});\n")
    vm.run(f"echo {base64.b64encode(rule.encode()).decode()} | base64 -d > /etc/polkit-1/rules.d/00-nexusos-test.rules")


def wait_source(vm, channel, timeout=30):
    """Whether the source line of update.conf ends with the channel before the timeout (the shell acts asynchronously)."""
    _, out = vm.run(f"for i in $(seq {timeout}); do grep -q '^source.*/{channel}$' /etc/nexusos/update.conf && break; sleep 1; done;"
                    " grep '^source' /etc/nexusos/update.conf", timeout + 30)
    return out.endswith(f"/{channel}"), out


def snapshot_list(vm):
    """/var/lib/nexusos/snapshots as the user tester reads it: its lines, split at the tabs."""
    status, out = vm.run("runuser -u tester -- cat /var/lib/nexusos/snapshots")
    return status, [line.split("\t") for line in out.split("\n")]


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
    status, root = run("findmnt -rno FSTYPE,OPTIONS /")
    check("Btrfs root subvolume", status == 0 and root.startswith("btrfs ") and "subvol=/@" in root, root)
    # From the configuration file, with the archinstall fix of the ISO
    # (nexusos-archinstall.hook).
    status, snapper = run("snapper --csvout list-configs --columns config,subvolume | tail -n +2 | sort | tr '\\n' ' ';"
                          " systemctl is-enabled snapper-timeline.timer snapper-cleanup.timer | tr '\\n' ' '")
    check("snapper configs for / and /home, timers enabled",
          status == 0 and snapper.split() == ["home,/home", "root,/", "enabled", "enabled"], snapper)
    repositories(vm)
    status, _ = run("for i in $(seq 60); do pgrep -x tuigreet >/dev/null && break; sleep 1; done; pgrep -x tuigreet", 90)
    check("greetd shows tuigreet", status == 0)
    time.sleep(5)
    print(f"[install-{firmware}] screenshot: {vm.screendump(os.path.join(outdir, f'greeter-{firmware}.png'))}", flush=True)

    desktop(vm, f"installed-{firmware}")

    if release:
        # The newer release, from the test server in place of the published one.
        run(f"sed -i 's|^source *=.*|source = {host}/stable|' /etc/nexusos/update.conf")
        _, before = run("sed -n 's/^snapshot=//p' /var/lib/nexusos/repo/nexusos-release")
        # A system older than boot-snapshots gets it with this very update,
        # after its pre snapshot: no menu entry for that one.
        entries, _ = run("test -x /usr/lib/nexusos/boot-snapshots")
        start = time.monotonic()
        status, _ = run("nexusos-update --yes >/root/update.log 2>&1", 1800)
        _, log = run("tail -n 30 /root/update.log")
        check(f"nexusos-update to {target}", status == 0 and f"now on snapshot {target}" in log,
              f"{time.monotonic() - start:.0f}s")
        print(log, flush=True)
        status, pair = run(f"snapper --csvout -c root list --columns type,description | grep -cE '^(pre|post),.*nexusos-update {target}'")
        check("snapper pre and post snapshots of the update", status == 0 and pair == "2", pair)
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
        # As the desktop shell asks, without root.
        status, out = run("runuser -u tester -- nexusos-update --check", 120)
        check("nexusos-update --check as a user", status == 2, out)
        if base_since(vm, "0.7.0"):
            # What the desktop shell reads without root.
            status, rows = snapshot_list(vm)
            pair = [r for r in rows if len(r) == 5 and r[3] in ("yes", "no") and r[4] == f"nexusos-update {target}"]
            check("snapshot list readable by a user: current 0, the pre and post of the update",
                  status == 0 and ["current", "0"] in rows and sorted(r[2] for r in pair) == ["post", "pre"]
                  and (entries != 0 or next(r for r in pair if r[2] == "pre")[3] == "yes"),
                  " | ".join("/".join(r) for r in rows))
            status, out = run("runuser -u tester -- nexusos-update --channel")
            check("nexusos-update --channel as a user", status == 0 and out == "stable", out)
            status, out = run("pkaction --action-id org.nexusos.rollback --verbose")
            check("polkit action org.nexusos.rollback asks an administrator",
                  status == 0 and re.search(r"implicit active:\s+auth_admin$", out, re.M) is not None
                  and re.search(r"implicit any:\s+no$", out, re.M) is not None, out.split("\n")[0])
            # Moving the source, from root; the other lines of update.conf stay.
            status, out = run("nexusos-update --channel testing; grep -c '^# ' /etc/nexusos/update.conf; grep '^source' /etc/nexusos/update.conf")
            check("nexusos-update --channel testing", status == 0 and out.split("\n")[0] == "testing"
                  and out.endswith("/testing") and out.split("\n")[1] != "0", out.replace("\n", " | "))
            status, out = run("nexusos-update --channel stable; nexusos-update --channel; grep '^source' /etc/nexusos/update.conf")
            check("nexusos-update --channel stable puts it back", status == 0 and out.split("\n")[:2] == ["stable", "stable"]
                  and out.endswith("/stable"), out.replace("\n", " | "))
        # A release that brings nexus-shell replaces caelestia-shell.
        if any(n.startswith("nexus-shell-") and n.endswith(".pkg.tar.zst") for n in os.listdir(release)):
            # By name: pacman -Q also answers for what a package provides.
            status, out = run("pacman -Q nexus-shell && ! pacman -Qq | grep -qx caelestia-shell")
            check("nexus-shell replaces caelestia-shell", status == 0, out)
        desktop(vm, f"updated-{firmware}")

        if installed_since(vm, "nexus-shell", "0.3.0"):
            # The Updates page of the shell, driven by IPC. Its effects are checked
            # in the system, not in what the calls print, whose format is the shell's.
            polkit_test_rule(vm)
            _, pre_n = run(f"snapper --csvout -c root list --columns number,type,description"
                           f" | sed -n 's|^\\([0-9]*\\),pre,nexusos-update {target}$|\\1|p'")
            found, out = False, ""
            for _ in range(15):
                _, out = shell_ipc(vm, "updates snapshots")
                found = bool(pre_n) and re.search(rf"(?<!\d){pre_n}(?!\d)", out) is not None
                if found:
                    break
                time.sleep(2)
            check(f"desktop shell lists snapshot {pre_n or '?'}, the pre of the update", found, out.replace("\n", " | "))
            shell_ipc(vm, "updates setChannel testing")
            ok, out = wait_source(vm, "testing")
            check("desktop shell moves the source to testing", ok, out)
            shell_ipc(vm, "updates setChannel stable")
            ok, out = wait_source(vm, "stable")
            check("desktop shell puts the source back to stable", ok, out)

        if installed_since(vm, "nexus-shell", "0.4.0"):
            # An app started from the launcher outlives a restart of the shell
            # (caelestia-dots/shell#1843). No IPC call starts one: the name is
            # typed into the launcher on the VM keyboard.
            shell_ipc(vm, "drawers toggle launcher")
            time.sleep(2)
            vm.qmp(*({"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": key}]}}
                     for key in ("f", "o", "o", "t", "ret")))
            _, app = run("for i in $(seq 30); do pgrep -nu tester '^foot' && break; sleep 1; done")
            _, shell = run("pgrep -of 'qs .*caelestia'")
            _, cgroups = run(f"cat /proc/{app or 0}/cgroup /proc/{shell or 0}/cgroup")
            unit = cgroups.split("\n")[-1].rsplit("/", 1)[-1]
            # As the session would: its user unit when the shell has one, else the process alone.
            if unit.endswith(".service"):
                run(f"systemctl --user -M tester@ restart {unit}")
            else:
                run(f"kill {shell or 0}")
            time.sleep(5)
            status, _ = run(f"kill -0 {app or 0}")
            check("an app started from the launcher outlives a restart of the shell",
                  bool(app) and status == 0, cgroups.replace("\n", " | "))
            print(f"[install-{firmware}] screenshot: {vm.screendump(os.path.join(outdir, f'launcher-{firmware}.png'))}", flush=True)

        status, _ = run("lsinitcpio /boot/initramfs-linux.img | grep -q 'hooks/nexusos-snapshot$'")
        check("initramfs carries the nexusos-snapshot hook", status == 0)
        if entries != 0:
            print(f"[install-{firmware}] no boot-snapshots before the update: no menu entry for its pre snapshot", flush=True)
        else:
            # The boot menu offers the system as it was before the update: the
            # pre snapshot of nexusos-update (boot-snapshots).
            _, pre = run(f"snapper --csvout -c root list --columns number,type,description"
                         f" | sed -n 's|^\\([0-9]*\\),pre,nexusos-update {target}$|\\1|p'")
            _, conf = run("find /boot -name limine.conf")
            _, menu = run(f"cat {conf}")
            # Each sub-entry runs to the next one, the last to the end of the block.
            entry = next((e for e in menu.split("\n//")[1:] if re.search(rf" nexusos\.snapshot={pre}$", e, re.M)), None)
            entry = entry and "\n".join(line for line in entry.split("\n") if not line.startswith("#"))
            files = re.findall(r"^ +(?:path|module_path): [^:]+:/(.+)$", entry or "", re.M)
            status, _ = run("cd /boot && ls " + " ".join(files)) if files else (1, "")
            check(f"boot menu entry for snapshot {pre or '?'}, its kernel and initramfs on /boot",
                  entry is not None and len(files) == 2 and status == 0, (entry or menu).split("\n")[0])
            if entry is not None:
                # First in the menu for one boot, as if picked in the Limine menu.
                rest = "\n".join(line for line in menu.split("\n") if not line.startswith("timeout:"))
                test = f"timeout: 1\n\n/{entry}\n\n{rest}\n"
                run(f"cp {conf} {conf}.orig && echo {base64.b64encode(test.encode()).decode()} | base64 -d > {conf}")
                vm.send("systemctl reboot\n")
                vm.login(password)
                started(vm)
                status, state = run("tr ' ' '\\n' < /proc/cmdline | grep '^nexusos.snapshot='; findmnt -no FSTYPE /;"
                                    " sed -n 's/^snapshot=//p' /var/lib/nexusos/repo/nexusos-release")
                check(f"booted snapshot {pre}: overlay root, snapshot {before} as before the update",
                      state.split("\n") == [f"nexusos.snapshot={pre}", "overlay", before], state.replace("\n", " | "))
                if base_since(vm, "0.7.0"):
                    # The service lists again at this boot: a system older than that has none.
                    status, rows = snapshot_list(vm)
                    check(f"booted snapshot {pre}: the snapshot list says it is the current one",
                          status == 0 and ["current", pre] in rows, " | ".join("/".join(r) for r in rows))
                desktop(vm, f"snapshot-{firmware}")
                # On /boot: the snapshot has no copy made after it was taken.
                run(f"mv {conf}.orig {conf}")

                # For good this time, from the updated system: the pre snapshot
                # becomes the root subvolume (nexusos-rollback).
                vm.send("systemctl reboot\n")
                vm.login(password)
                started(vm)
                if installed_since(vm, "nexus-shell", "0.3.0"):
                    # Through the Updates page of the shell, as the user. The test rule
                    # sits on @, which the snapshot boots did not change.
                    status, _ = run("test -f /etc/polkit-1/rules.d/00-nexusos-test.rules")
                    check("polkit test rule still there on the updated system", status == 0)
                    if status != 0:
                        polkit_test_rule(vm)
                    desktop(vm, f"before-rollback-{firmware}")
                    _, out = shell_ipc(vm, f"updates rollback {pre}")
                    # The old root appears while the rollback runs: wait for it to end too.
                    status, _ = run("for i in $(seq 300); do btrfs subvolume list / | grep -q ' path @\\.old-[^/]*$'"
                                    " && ! pgrep -f 'nexusos-rollbac[k] ' >/dev/null && break; sleep 1; done;"
                                    " btrfs subvolume list / | grep -q ' path @\\.old-[^/]*$'", 330)
                    how = " through the desktop shell"
                else:
                    status, out = run(f"nexusos-rollback {pre} --yes", 300)
                    how = ""
                check(f"nexusos-rollback to snapshot {pre}{how}", status == 0, out.split("\n")[-1])
                print(out, flush=True)
                vm.send("systemctl reboot\n")
                vm.login(password)
                started(vm)
                status, state = run("findmnt -no FSTYPE,OPTIONS / | grep -o '^btrfs\\|subvol=/@$';"
                                    " sed -n 's/^snapshot=//p' /var/lib/nexusos/repo/nexusos-release;"
                                    f" snapper --csvout -c root list --columns type,description | grep -cE '^(pre|post),.*nexusos-update {target}';"
                                    " btrfs subvolume list / | grep -c ' path @\\.old-[^/]*$'")
                check(f"rolled back: root subvolume @, snapshot {before}, snapshots kept, old root kept",
                      state.split("\n") == ["btrfs", "subvol=/@", before, "2", "1"], state.replace("\n", " | "))
                status, out = run("nexusos-update --check", 120)
                check("nexusos-update offers the update again", status == 0, out)
                desktop(vm, f"rollback-{firmware}")
except TimeoutError as error:
    check("serial console", False, str(error))
finally:
    vm.kill()
    server.shutdown()

sys.exit(0 if check.passed() else 1)
