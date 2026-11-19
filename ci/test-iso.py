#!/usr/bin/env python3
"""Boot the NexusOS ISO in QEMU and check the live system from a root shell
on the serial console. Runs inside the container started by test-iso.sh.

Usage: test-iso.py ISO uefi|bios OUTDIR
"""
import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import time
import uuid

iso, firmware, outdir = sys.argv[1:4]
os.makedirs(outdir, exist_ok=True)
qmp_path = f"/tmp/qmp-{firmware}.sock"

qemu = [
    "qemu-system-x86_64", "-machine", "q35,accel=kvm", "-cpu", "host", "-m", "4096", "-smp", "4",
    "-cdrom", iso, "-boot", "d", "-vga", "virtio", "-display", "none", "-monitor", "none",
    "-serial", "stdio", "-qmp", f"unix:{qmp_path},server=on,wait=off",
    "-nic", "user,model=virtio-net-pci",
    # systemd starts a getty on the serial port from this credential, so the
    # image needs no test-only change.
    "-smbios", "type=11,value=io.systemd.credential:getty.ttys.serial=ttyS0",
]
if firmware == "uefi":
    shutil.copy("/usr/share/edk2/x64/OVMF_VARS.4m.fd", "/tmp/vars.fd")
    qemu += ["-drive", "if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd",
             "-drive", "if=pflash,format=raw,file=/tmp/vars.fd"]

log = open(os.path.join(outdir, f"serial-{firmware}.log"), "wb")
vm = subprocess.Popen(qemu, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
buffer = b""


def expect(pattern, timeout):
    """Read serial output until pattern matches; return the match."""
    global buffer
    deadline = time.monotonic() + timeout
    regex = re.compile(pattern.encode())
    while True:
        match = regex.search(buffer)
        if match:
            buffer = buffer[match.end():]
            return match
        left = deadline - time.monotonic()
        if left <= 0 or vm.poll() is not None:
            raise TimeoutError(f"no {pattern!r} after {timeout}s")
        ready, _, _ = select.select([vm.stdout], [], [], min(left, 1))
        if ready:
            chunk = os.read(vm.stdout.fileno(), 4096)
            log.write(chunk)
            log.flush()
            buffer += chunk


def send(text):
    vm.stdin.write(text.encode())
    vm.stdin.flush()


def run(command, timeout=60):
    """Run a shell command on the serial console; return (status, output)."""
    token = uuid.uuid4().hex[:8]
    send(f"{command}; echo __{token}_$?__\n")
    # An echoed command line would hold "$?", never a digit, so only the
    # real marker matches.
    match = expect(rf"__{token}_(\d+)__", timeout)
    output = match.string[:match.start()].decode(errors="replace")
    return int(match.group(1)), output.replace("\r", "").strip()


def qmp(*commands):
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(qmp_path)
    reader = sock.makefile()
    reader.readline()
    replies = []
    for command in ({"execute": "qmp_capabilities"},) + commands:
        sock.sendall(json.dumps(command).encode())
        while True:
            reply = json.loads(reader.readline())
            if "return" in reply or "error" in reply:
                replies.append(reply)
                break
    sock.close()
    return replies[1:]


checks = []


def check(name, ok, detail=""):
    checks.append(ok)
    print(f"[{firmware}] {'ok ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)


try:
    expect(r"login: ", 300)
    check("boot to a login prompt", True)
    send("root\n")
    time.sleep(3)
    send("exec bash --noprofile --norc\n")
    time.sleep(1)
    # No echo and no prompt: what comes before a marker is the output.
    run("stty -echo; PS1= PS2=; export SYSTEMD_PAGER= TERM=dumb")

    _, state = run("systemctl is-system-running --wait", 300)
    # Known: on UEFI, systemd tries to attach the CD as a loop device and finds
    # no partition table on it. Any other failed unit fails the test.
    _, failed = run("systemctl --failed --no-legend --plain | grep -v '^systemd-loop@'")
    check("system started", state in ("running", "degraded") and not failed, state + (": " + failed if failed else ""))
    status, osid = run(". /etc/os-release && echo $ID")
    check("NexusOS identity", status == 0 and osid.strip() == "nexusos", osid.strip())
    status, _ = run("systemctl is-active greetd")
    check("greetd active", status == 0)
    status, sessions = run(
        "for i in $(seq 120); do loginctl list-sessions --no-legend | grep -q nexus && break; sleep 1; done;"
        " loginctl list-sessions --no-legend | grep nexus", 150)
    check("live user logged in", status == 0, sessions)
    status, _ = run("for i in $(seq 300); do pgrep -x Hyprland >/dev/null && break; sleep 1; done; pgrep -x Hyprland", 330)
    check("Hyprland running", status == 0)
    status, shell = run("for i in $(seq 120); do pgrep -af 'qs .*caelestia' && break; sleep 1; done", 150)
    check("Caelestia shell running", status == 0, shell)
    # Portals start before Hyprland on the first login; they must answer once
    # the session runs (file pickers, dark mode).
    user = "sudo -u nexus env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"
    status, scheme = run(f"{user} busctl --user call org.freedesktop.portal.Desktop /org/freedesktop/portal/desktop"
                         " org.freedesktop.portal.Settings ReadOne ss org.freedesktop.appearance color-scheme", 60)
    check("settings portal answers", status == 0, scheme)
    _, deprecated = run(f"{user} hyprctl -i 0 deprecated-config 2>&1 | head -n 20")
    print(f"[{firmware}] hyprctl deprecated-config:\n{deprecated}", flush=True)
    _, packages = run("pacman -Q nexusos-base nexusos-desktop caelestia-shell quickshell-git hyprland 2>/dev/null")
    print(packages, flush=True)
    _, errors = run("journalctl -b -p err --no-pager -o cat | tail -n 40")
    _, user = run("journalctl -b _UID=1000 -p warning --no-pager -o short-monotonic | tail -n 80")
    with open(os.path.join(outdir, f"errors-{firmware}.log"), "w") as f:
        f.write(errors + "\n\n== live user, warnings and above\n" + user + "\n")
    time.sleep(10)
    screen = os.path.join(outdir, f"screen-{firmware}.png")
    reply = qmp({"execute": "screendump", "arguments": {"filename": screen, "format": "png"}})
    print(f"[{firmware}] screenshot: {screen} {reply}", flush=True)
except TimeoutError as error:
    check("serial console", False, str(error))
finally:
    vm.kill()
    vm.wait()
    log.close()

sys.exit(0 if all(checks) else 1)
