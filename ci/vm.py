"""A QEMU machine driven from a root shell on its serial console, for
test-iso.py and test-install.py."""
import json
import os
import re
import select
import shutil
import socket
import subprocess
import time
import uuid

OVMF_CODE = "/usr/share/edk2/x64/OVMF_CODE.4m.fd"
OVMF_VARS = "/usr/share/edk2/x64/OVMF_VARS.4m.fd"


class VM:
    def __init__(self, name, firmware, drives, log_path, vars_path=None):
        """drives: QEMU arguments for the boot media. UEFI variables live in
        vars_path, so a second boot of the same machine can keep them."""
        self.qmp_path = f"/tmp/qmp-{name}.sock"
        qemu = [
            "qemu-system-x86_64", "-machine", "q35,accel=kvm", "-cpu", "host", "-m", "4096", "-smp", "4",
            "-vga", "virtio", "-display", "none", "-monitor", "none",
            "-serial", "stdio", "-qmp", f"unix:{self.qmp_path},server=on,wait=off",
            "-nic", "user,model=virtio-net-pci",
            # systemd starts a getty on the serial port from this credential, so
            # the image needs no test-only change.
            "-smbios", "type=11,value=io.systemd.credential:getty.ttys.serial=ttyS0",
        ] + drives
        if firmware == "uefi":
            vars_path = vars_path or f"/tmp/vars-{name}.fd"
            if not os.path.exists(vars_path):
                shutil.copy(OVMF_VARS, vars_path)
            qemu += ["-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
                     "-drive", f"if=pflash,format=raw,file={vars_path}"]
        self.log = open(log_path, "wb")
        self.proc = subprocess.Popen(qemu, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.buffer = b""

    def expect(self, pattern, timeout):
        """Read serial output until pattern matches; return the match."""
        deadline = time.monotonic() + timeout
        regex = re.compile(pattern.encode())
        while True:
            match = regex.search(self.buffer)
            if match:
                self.buffer = self.buffer[match.end():]
                return match
            left = deadline - time.monotonic()
            if left <= 0 or self.proc.poll() is not None:
                raise TimeoutError(f"no {pattern!r} after {timeout}s")
            ready, _, _ = select.select([self.proc.stdout], [], [], min(left, 1))
            if ready:
                chunk = os.read(self.proc.stdout.fileno(), 4096)
                self.log.write(chunk)
                self.log.flush()
                self.buffer += chunk

    def send(self, text):
        self.proc.stdin.write(text.encode())
        self.proc.stdin.flush()

    def login(self, password=None, timeout=300):
        """Log in as root on the serial getty and leave a bare shell for run()."""
        self.expect(r"login: ", timeout)
        self.send("root\n")
        if password is not None:
            self.expect(r"Password: ", 30)
            self.send(password + "\n")
        time.sleep(3)
        self.send("exec bash --noprofile --norc\n")
        time.sleep(1)
        # No echo and no prompt: what comes before a marker is the output.
        self.run("stty -echo; PS1= PS2=; export SYSTEMD_PAGER= TERM=dumb")

    def run(self, command, timeout=60):
        """Run a shell command on the serial console; return (status, output)."""
        token = uuid.uuid4().hex[:8]
        self.send(f"{command}; echo __{token}_$?__\n")
        # An echoed command line would hold "$?", never a digit, so only the
        # real marker matches.
        match = self.expect(rf"__{token}_(\d+)__", timeout)
        output = match.string[:match.start()].decode(errors="replace")
        return int(match.group(1)), output.replace("\r", "").strip()

    def qmp(self, *commands):
        sock = socket.socket(socket.AF_UNIX)
        sock.connect(self.qmp_path)
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

    def screendump(self, path):
        return self.qmp({"execute": "screendump", "arguments": {"filename": path, "format": "png"}})

    def wait_poweroff(self, timeout):
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"still running {timeout}s after poweroff") from None
        finally:
            self.log.close()

    def kill(self):
        self.proc.kill()
        self.proc.wait()
        self.log.close()


class Checks:
    def __init__(self, label):
        self.label = label
        self.results = []

    def __call__(self, name, ok, detail=""):
        self.results.append(ok)
        print(f"[{self.label}] {'ok ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)
        return ok

    def passed(self):
        return bool(self.results) and all(self.results)
