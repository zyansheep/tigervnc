"""Smoke test for w0vncserver on a headless sway (contrib/dither/default.nix wraps it as w0vncserver-smoke).

What it checks, each by waiting for the exact thing it expects (a log line, a message, a file's contents):
  1. sway starts headless, with no input devices and no session bus (as a sandbox runs it), and w0vncserver starts on
     it and listens on its Unix socket: no portal, so it must pick the wlroots protocols.
  2. An RFB 3.8 client completes the handshake (security None), gets the screen's size in ServerInit, and receives a
     full framebuffer update covering the screen.
  3. The seat has no keyboard, so w0vncserver must give its virtual keyboard a keymap: it logs that the keymap is set,
     and a key event for "a" reaches a Wayland client (wev) as the keysym a.
  4. Clipboard both ways over ext-data-control-v1 (or zwlr-data-control): text copied in the compositor with wl-copy
     arrives as ServerCutText, and ClientCutText from the viewer comes out of wl-paste. Then a copy made as a browser
     makes one, the clipboard and the primary selection set together before either is read, reaches the viewer too,
     both with SendPrimary on (a second server's viewer) and off: the second offer must neither cancel reading the
     first nor leave the read it asks for unmade.
  5. The pointer moves and a visible cursor shape arrives (rich cursor pseudo-encoding): the seat had no pointer when
     capture started, so this needs the cursor session made once the virtual pointer exists.
  6. w0vncserver is still running at the end.

Usage: w0vncserver-smoke [--keep] (--keep leaves the temporary directory and its logs).
Needs on PATH: sway, w0vncserver, wev, wl-copy, wl-paste.
"""

import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

WIDTH, HEIGHT = 1024, 768
TIMEOUT = 30.0


class Fail(Exception):
    pass


def log(msg):
    print(f"smoke: {msg}", flush=True)


def wait_for(what, check, timeout=TIMEOUT, procs=()):
    """Poll check() until it returns a true value; fail if a watched process exits first or time runs out."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value:
            return value
        for name, p in procs:
            if p.poll() is not None:
                raise Fail(f"{name} exited ({p.returncode}) while waiting for {what}")
        if time.monotonic() > deadline:
            raise Fail(f"timed out after {timeout:.0f} s waiting for {what}")
        time.sleep(0.05)


def read_file(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return ""


class Rfb:
    """Just enough of an RFB 3.8 client: handshake, raw updates, key events and classic cut text."""

    ENC_RAW = 0
    ENC_CURSOR = -239
    ENC_DESKTOP_SIZE = -223

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(TIMEOUT)
        self.sock.connect(path)
        self.cut_texts = []
        self.cursors = []  # (width, height, any pixel shown) of each cursor shape sent

    def recv(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise Fail("server closed the connection")
            buf += chunk
        return bytes(buf)

    def handshake(self):
        banner = self.recv(12)
        if not banner.startswith(b"RFB 003."):
            raise Fail(f"unexpected banner {banner!r}")
        self.sock.sendall(b"RFB 003.008\n")
        (count,) = struct.unpack("B", self.recv(1))
        if count == 0:
            (n,) = struct.unpack(">I", self.recv(4))
            raise Fail(f"server refused: {self.recv(n).decode(errors='replace')}")
        types = list(self.recv(count))
        if 1 not in types:
            raise Fail(f"no security type None offered: {types}")
        self.sock.sendall(b"\x01")
        (result,) = struct.unpack(">I", self.recv(4))
        if result != 0:
            raise Fail(f"security result {result}")
        self.sock.sendall(b"\x01")  # ClientInit: shared
        w, h = struct.unpack(">HH", self.recv(4))
        pf = self.recv(16)
        (name_len,) = struct.unpack(">I", self.recv(4))
        name = self.recv(name_len).decode(errors="replace")
        self.width, self.height = w, h
        self.bpp = pf[0]
        log(f"ServerInit: {w}x{h}, {self.bpp} bpp, name {name!r}")
        return w, h

    def set_encodings(self, encodings):
        self.sock.sendall(struct.pack(">BxH", 2, len(encodings)) + b"".join(struct.pack(">i", e) for e in encodings))

    def request_update(self, incremental=False):
        self.sock.sendall(struct.pack(">BBHHHH", 3, 1 if incremental else 0, 0, 0, self.width, self.height))

    def pointer(self, x, y, buttons=0):
        self.sock.sendall(struct.pack(">BBHH", 5, buttons, x, y))

    def key(self, keysym, down):
        self.sock.sendall(struct.pack(">BBxxI", 4, 1 if down else 0, keysym))

    def client_cut_text(self, text):
        data = text.encode("latin-1")
        self.sock.sendall(struct.pack(">BxxxI", 6, len(data)) + data)

    def read_message(self):
        """Read one server message. Returns ("update", area of raw pixels) or ("cut", text) or (kind, None)."""
        (kind,) = struct.unpack("B", self.recv(1))
        if kind == 0:
            (n,) = struct.unpack(">xH", self.recv(3))
            area = 0
            px = self.bpp // 8
            for _ in range(n):
                x, y, w, h, enc = struct.unpack(">HHHHi", self.recv(12))
                if enc == self.ENC_RAW:
                    self.recv(w * h * px)
                    area += w * h
                elif enc == self.ENC_CURSOR:
                    self.recv(w * h * px)
                    mask = self.recv((w + 7) // 8 * h)
                    self.cursors.append((w, h, any(mask)))
                elif enc == self.ENC_DESKTOP_SIZE:
                    self.width, self.height = w, h
                else:
                    raise Fail(f"unexpected encoding {enc} in an update")
            return ("update", area)
        if kind == 2:
            return ("bell", None)
        if kind == 3:
            (n,) = struct.unpack(">xxxI", self.recv(7))
            text = self.recv(n).decode("latin-1")
            self.cut_texts.append(text)
            return ("cut", text)
        raise Fail(f"unexpected server message type {kind}")

    def read_until(self, what, done, timeout=TIMEOUT):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Fail(f"timed out waiting for {what}")
            self.sock.settimeout(remaining)
            try:
                msg = self.read_message()
            except socket.timeout:
                raise Fail(f"timed out waiting for {what}")
            if done(msg):
                self.sock.settimeout(TIMEOUT)
                return msg


class WlCopy:
    """Just enough of a Wayland client to own the clipboard and the primary selection at once, as a browser does on a
    copy (ext-data-control-v1): both set in one write, before any request for their data is answered; then every
    request is answered with the text, until close()."""

    UTF8 = "text/plain;charset=utf-8"

    def __init__(self, path, text):
        self.text = text.encode()
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.next_id, self.buf, self.fds, self.out = 2, b"", [], b""
        self.sent = 0

    def new_id(self):
        self.next_id += 1
        return self.next_id - 1

    @staticmethod
    def string(t):
        b = t.encode() + b"\0"
        return struct.pack("<I", len(b)) + b + b"\0" * (-len(b) % 4)

    def req(self, obj, op, payload=b""):
        self.out += struct.pack("<II", obj, ((8 + len(payload)) << 16) | op) + payload

    def flush(self):
        self.s.sendall(self.out)
        self.out = b""

    def recv(self):
        """One event: (object, opcode, payload); the fds it carries are kept in order."""
        while len(self.buf) < 8 or len(self.buf) < struct.unpack_from("<II", self.buf)[1] >> 16:
            data, anc, _, _ = self.s.recvmsg(65536, socket.CMSG_SPACE(16 * 4))
            if not data:
                raise Fail("the compositor closed WlCopy's connection")
            for level, kind, d in anc:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    self.fds += struct.unpack(f"<{len(d) // 4}i", d[: len(d) // 4 * 4])
            self.buf += data
        obj, word = struct.unpack_from("<II", self.buf)
        size = word >> 16
        msg, self.buf = (obj, word & 0xFFFF, self.buf[8:size]), self.buf[size:]
        if obj == 1 and msg[1] == 0:
            _, code, n = struct.unpack_from("<III", msg[2])
            raise Fail(f"Wayland error {code}: {msg[2][12:12 + n - 1].decode(errors='replace')}")
        return msg

    def copy(self):
        registry, done = self.new_id(), self.new_id()
        self.req(1, 1, struct.pack("<I", registry))  # wl_display.get_registry
        self.req(1, 0, struct.pack("<I", done))  # wl_display.sync
        self.flush()
        found = {}
        while True:
            obj, op, p = self.recv()
            if obj == done:
                break
            if obj == registry and op == 0:
                name, n = struct.unpack_from("<II", p)
                found[p[8:8 + n - 1].decode()] = name
        if "ext_data_control_manager_v1" not in found:
            raise Fail("the compositor has no ext_data_control_manager_v1")

        def bind(iface):
            new = self.new_id()
            self.req(registry, 0, struct.pack("<I", found[iface]) + self.string(iface) + struct.pack("<II", 1, new))
            return new

        manager, seat = bind("ext_data_control_manager_v1"), bind("wl_seat")
        device = self.new_id()
        self.req(manager, 1, struct.pack("<II", device, seat))  # get_data_device
        self.sources = []
        for setter in (0, 2):  # set_selection, set_primary_selection
            source = self.new_id()
            self.req(manager, 0, struct.pack("<I", source))  # create_data_source
            self.req(source, 0, self.string(self.UTF8))  # offer
            self.req(device, setter, struct.pack("<I", source))
            self.sources.append(source)
        self.flush()

    def serve(self):
        try:
            while True:
                obj, op, p = self.recv()
                if obj in self.sources and op == 0:  # send(mime_type, fd)
                    fd = self.fds.pop(0)
                    try:
                        os.write(fd, self.text)
                    finally:
                        os.close(fd)
                    self.sent += 1
        except (Fail, OSError):
            pass

    def close(self):
        self.s.close()


def main():
    keep = "--keep" in sys.argv[1:]
    for tool in ("sway", "w0vncserver", "wev", "wl-copy", "wl-paste", "stdbuf"):
        if not shutil.which(tool):
            raise Fail(f"{tool} not on PATH")

    tmp = tempfile.mkdtemp(prefix="w0vncserver-smoke-")
    run = os.path.join(tmp, "run")
    os.mkdir(run, 0o700)
    procs = []
    ok = False
    try:
        with open(os.path.join(tmp, "sway.conf"), "w") as f:
            f.write(
                "xwayland disable\nswaybg_command -\nswaynag_command -\n"
                f"output * mode --custom {WIDTH}x{HEIGHT} position 0 0 scale 1\n"
                "default_border none\nfocus_follows_mouse no\n"
            )
        base_env = {
            k: v for k, v in os.environ.items()
            if k not in ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET", "SWAYSOCK") and not k.startswith("XKB_DEFAULT_")
        }
        base_env.update(
            XDG_RUNTIME_DIR=run,
            HOME=tmp,
            DBUS_SESSION_BUS_ADDRESS=f"unix:path={tmp}/no-bus",
            DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={tmp}/no-bus",
        )

        def start(name, argv, env, **kw):
            out = open(os.path.join(tmp, f"{name}.log"), "wb")
            p = subprocess.Popen(argv, env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
            procs.append((name, p))
            return p

        sway_env = dict(base_env, WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1", WLR_RENDERER="pixman")
        sway = start("sway", ["sway", "-c", os.path.join(tmp, "sway.conf")], sway_env)
        sock_name = wait_for(
            "sway's Wayland socket",
            lambda: next((n for n in sorted(os.listdir(run)) if n.startswith("wayland-") and not n.endswith(".lock")), None),
            procs=[("sway", sway)],
        )
        wl_env = dict(base_env, WAYLAND_DISPLAY=sock_name)
        log(f"sway up on {sock_name}")

        # A client to type into: sway focuses it as it maps.
        # stdbuf: wev's output is a file, which it would otherwise buffer whole.
        wev_log = os.path.join(tmp, "wev.log")
        wev = start("wev", ["stdbuf", "-oL", "wev"], wl_env)
        wait_for("wev's window to be configured", lambda: "xdg_surface] configure" in read_file(wev_log), procs=[("wev", wev)])

        vnc_sock = os.path.join(tmp, "vnc.sock")
        w0 = start(
            "w0vncserver",
            ["w0vncserver", "-rfbunixpath", vnc_sock, "-rfbport", "-1", "-SecurityTypes", "None", "-AlwaysShared",
             "-desktop", "smoke", "-Log", "*:stderr:100"],
            dict(wl_env, XKB_DEFAULT_LAYOUT="us"),
        )
        wait_for("w0vncserver's socket", lambda: os.path.exists(vnc_sock), procs=[("w0vncserver", w0)])
        w0_log = os.path.join(tmp, "w0vncserver.log")

        rfb = Rfb(vnc_sock)
        w, h = rfb.handshake()
        if (w, h) != (WIDTH, HEIGHT):
            raise Fail(f"ServerInit says {w}x{h}, sway's output is {WIDTH}x{HEIGHT}")
        rfb.set_encodings([Rfb.ENC_RAW, Rfb.ENC_CURSOR, Rfb.ENC_DESKTOP_SIZE])
        rfb.request_update()
        covered = 0

        def full(msg):
            # As a viewer does: ask again after each update until the whole screen has come.
            nonlocal covered
            if msg[0] == "update":
                covered += msg[1]
                if covered < WIDTH * HEIGHT:
                    rfb.request_update(incremental=True)
            return covered >= WIDTH * HEIGHT

        rfb.read_until("a full framebuffer update", full)
        log(f"framebuffer update: {covered} pixels")

        # The seat had no keyboard: the virtual keyboard's default keymap must make one, whose keymap w0vncserver reads
        # back (it sets the virtual keyboard up from it at the first key).
        wait_for("the seat's keyboard to get a keymap", lambda: "Keymap updated" in read_file(w0_log), procs=[("w0vncserver", w0)])
        rfb.key(0x61, True)
        rfb.key(0x61, False)
        wait_for("wev to see the key a", lambda: re.search(r"sym: a\b", read_file(wev_log)), procs=[("wev", wev), ("w0vncserver", w0)])
        log("key event reached the client")

        # Clipboard, compositor to viewer.
        out_text = "from-sway-" + secrets.token_hex(4)
        start("wl-copy", ["wl-copy", "--foreground", out_text], wl_env)
        rfb.request_update(incremental=True)
        rfb.read_until("ServerCutText with the compositor's clipboard", lambda m: m[0] == "cut" and m[1] == out_text)
        log("clipboard reached the viewer")

        # Clipboard, viewer to compositor.
        in_text = "from-viewer-" + secrets.token_hex(4)
        rfb.client_cut_text(in_text)

        def pasted():
            r = subprocess.run(["wl-paste", "--no-newline"], env=wl_env, capture_output=True, timeout=10)
            return r.returncode == 0 and r.stdout.decode(errors="replace") == in_text

        wait_for("wl-paste to give the viewer's clipboard", pasted, procs=[("w0vncserver", w0)])
        log("clipboard reached the compositor")

        # A copy as a browser makes one: the clipboard and the primary selection at once. A second server on the
        # same sway sends no primary selection, so one copy checks both ways of reading it.
        vnc_sock2 = os.path.join(tmp, "vnc2.sock")
        w0b = start(
            "w0vncserver-noprimary",
            ["w0vncserver", "-rfbunixpath", vnc_sock2, "-rfbport", "-1", "-SecurityTypes", "None", "-AlwaysShared",
             "-desktop", "smoke2", "-SendPrimary=0", "-Log", "*:stderr:100"],
            dict(wl_env, XKB_DEFAULT_LAYOUT="us"),
        )
        wait_for("the second w0vncserver's socket", lambda: os.path.exists(vnc_sock2), procs=[("w0vncserver-noprimary", w0b)])
        rfb2 = Rfb(vnc_sock2)
        rfb2.handshake()
        rfb2.set_encodings([Rfb.ENC_RAW])
        rfb2.request_update()
        rfb2.read_until("the second server's first update", lambda m: m[0] == "update")
        both_text = "from-browser-" + secrets.token_hex(4)
        copier = WlCopy(os.path.join(run, sock_name), both_text)
        copier.copy()
        threading.Thread(target=copier.serve, daemon=True).start()
        try:
            rfb.read_until("ServerCutText with a browser's copy (SendPrimary on)", lambda m: m[0] == "cut" and m[1] == both_text)
            rfb2.read_until("ServerCutText with a browser's copy (SendPrimary off)", lambda m: m[0] == "cut" and m[1] == both_text)
        finally:
            copier.close()
        log("a browser's copy reached both viewers")

        # The cursor: the seat had no pointer when capture started, so w0vncserver must make the cursor session
        # once its virtual pointer exists, and send the cursor's shape. Move the pointer onto the screen and wait
        # for a shape with something in it.
        rfb.pointer(WIDTH // 2, HEIGHT // 2)
        rfb.pointer(WIDTH // 2 + 10, HEIGHT // 2 + 10)

        def visible_cursor(msg):
            if msg[0] == "update":
                rfb.request_update(incremental=True)
            return any(w > 0 and h > 0 and shown for w, h, shown in rfb.cursors)

        rfb.request_update(incremental=True)
        rfb.read_until("a visible cursor shape", visible_cursor)
        w_, h_, _ = next(c for c in rfb.cursors if c[0] and c[1] and c[2])
        log(f"cursor shape: {w_}x{h_}")
        if w0.poll() is not None:
            raise Fail(f"w0vncserver exited ({w0.returncode})")
        ok = True
        log("PASS")
    finally:
        for name, p in reversed(procs):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        if not ok or keep:
            for name in ("sway", "w0vncserver", "w0vncserver-noprimary", "wev"):
                text = read_file(os.path.join(tmp, f"{name}.log"))
                print(f"----- {name}.log (last 60 lines) -----")
                print("\n".join(text.splitlines()[-60:]))
        if keep:
            log(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Fail as e:
        print(f"smoke: FAIL: {e}", flush=True)
        sys.exit(1)
