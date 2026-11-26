# What this fork could send upstream

Nothing here has been sent. These are drafts for whoever decides to.

The `dither` branch carries two kinds of commits on top of TigerVNC's latest stable tag:

1. **Backports.** These are w0vncserver fixes and features already merged to TigerVNC's master but not yet in a
   release. They include clipboard on wlroots (ext-data-control-v1), ext-image-copy-capture with its cursor session,
   backend detection that survives a missing portal, and error handling. Each one's commit message names its upstream
   PR. Nothing needs to be sent for these. A rebase onto the next stable release drops each one that release contains.
   If upstream makes a 1.16.3, backporting [#2159](https://github.com/TigerVNC/tigervnc/pull/2159) and
   [#2053](https://github.com/TigerVNC/tigervnc/pull/2053) to `1.16-branch` is what makes 1.16 run on a headless
   compositor at all.
2. **Our own four commits.** They are drafted below as one pull request. The same four commits rebased onto master
   are on the fork's branch
   [`upstream-w0vncserver-headless`](https://github.com/zyansheep/tigervnc/tree/upstream-w0vncserver-headless),
   with master's translated log messages kept.

---

## Draft PR: w0vncserver: work on a headless wlroots compositor

**Branch:** `zyansheep:upstream-w0vncserver-headless` → `TigerVNC:master`

### Summary

w0vncserver can share a headless wlroots compositor, such as sway with `WLR_BACKENDS=headless` and
`WLR_LIBINPUT_NO_DEVICES=1`, the way wayvnc does. Such a compositor has no input devices, so its seat starts with no
keyboard and no pointer. The only keyboard and pointer it ever gets are the virtual ones w0vncserver creates. Today
that leaves the keyboard dead and the cursor invisible. This PR fixes both, and fixes two bugs the fix runs into
along the way:

1. **Give a keyboardless seat a default keymap.** With no keyboard on the seat, `VirtualKeyboard` has no keymap to
   copy. In 1.16.2 the constructor dereferenced the missing keyboard and crashed when the first client connected (the
   backtrace is below). #2053 ("Ensure seat has keyboard in VirtualKeyboard") stops the crash. But no keymap ever
   arrives, so every key is still dropped. This commit uploads libxkbcommon's default keymap (from `XKB_DEFAULT_*`,
   else `us`) to the virtual keyboard. The compositor then adds that keyboard to the seat. The seat gains a keyboard
   whose keymap is the one we sent, and `keymapUpdated()` takes over from there as it does on a desktop.
2. **Keep the seat's keyboard and pointer across capability changes.** `wl_seat.capabilities` lists every capability
   the seat has now, and `Seat` replaced its `Keyboard` and `Pointer` on each event that listed them. When the virtual
   pointer and keyboard join one after the other, the first one's object is thrown away and remade. That loses the
   keymap, and any cursor session made from the old pointer. A capability that goes away also left its object behind.
   Now each object is made only when its capability appears and dropped when it goes.
3. **Capture the cursor once the seat has a pointer.** The ext-image-copy-capture cursor session needs a `wl_pointer`.
   `ImageCopyCaptureManager` only tried once, when capture started, which is before our virtual pointer exists. This
   is the `FIXME: Make it possible to re-enable this if a cursor is added in the future` in `createSession()`. The
   cursor session now follows the seat's pointer: it is made, remade or dropped when the seat gains or loses one.
4. **Don't leak keymap mappings and fds.** `Keyboard::handleKeyMap()` never closed the fd that `wl_keyboard.keymap`
   hands over. It also leaked the new mapping when the keymap was unchanged, and that is the common case: each
   `zwp_virtual_keyboard_v1_keymap()` comes back as a keymap event with the same keymap. On a failed parse,
   `clearKeyMap()` unmapped the new mapping with the old size. `VirtualKeyboard` spotted a new keymap by comparing fd
   numbers, which only worked because the old fds stayed open. The keyboard now carries a keymap serial that is
   unique across `Keyboard` objects, and that is what gets compared.

### How it was tested

The fork's smoke test (`contrib/dither/smoke.py`) runs a headless sway 1.12 (wlroots 0.20) with no input devices and no
session bus, plus w0vncserver, plus a minimal RFB 3.8 client. It checks the following:

- the handshake, and a full framebuffer update;
- a key event for `a`, which reaches a Wayland client (`wev`) as keysym `a`;
- clipboard both ways over ext-data-control-v1: `wl-copy` arrives as ServerCutText, and ClientCutText comes out of
  `wl-paste`;
- moving the pointer, after which a cursor shape with visible pixels arrives (16x23, rich cursor pseudo-encoding);
- w0vncserver is still running at the end.

Before commit 3, the test timed out waiting for a cursor shape, and w0vncserver logged that the cursor would be
invisible. Commit 1 is what makes w0vncserver log `Keymap updated` at all on this seat. Without it, no keyboard ever
joins the seat, so `VirtualKeyboard::key()` returns before sending anything.

### Backtrace (1.16.2, headless sway, first client connects)

1.16.2 also needs a fix to get this far: it exits at start when there is no portal. That fix is #2159 on master, and
the fork carries it.

```
Thread 1 "w0vncserver" received signal SIGSEGV, Segmentation fault.
0x0000555555586793 in wayland::VirtualKeyboard::VirtualKeyboard (this=this@entry=0x55555580d230, display=<optimized out>, seat_=<optimized out>) at unix/w0vncserver/wayland/objects/VirtualKeyboard.cxx:58
#0  wayland::VirtualKeyboard::VirtualKeyboard (...) at unix/w0vncserver/wayland/objects/VirtualKeyboard.cxx:58
#1  operator() (...) at unix/w0vncserver/wayland/WaylandDesktop.cxx:82
#2  std::__invoke_impl<void, WaylandDesktop::start()::<lambda()>&> (...)
#3  std::__invoke_r<void, WaylandDesktop::start()::<lambda()>&> (...)
#4  std::_Function_handler<void(), WaylandDesktop::start()::<lambda()> >::_M_invoke(...)
#5  std::function<void()>::operator() (...)
#6  WaylandPixelBuffer::captureFrameDone (this=0x55555565f100) at unix/w0vncserver/wayland/WaylandPixelBuffer.cxx:93
#7  ffi_call_unix64 () from libffi.so.8
...
#10 wl_closure_invoke () from libwayland-client.so.0
#11 dispatch_event () from libwayland-client.so.0
#12 wl_display_dispatch_queue_pending () from libwayland-client.so.0
#13 GWaylandSource::dispatch (this=0x55555580c920) at unix/w0vncserver/wayland/GWaylandSource.cxx:130
...
#18 g_main_loop_run () from libglib-2.0.so.0
#19 main (...) at unix/w0vncserver/w0vncserver.cxx:210
```

Line 58 is `if (seat->getKeyboard()->hasKeymap())`, and `getKeyboard()` is null there.

### Not included

- **Characters missing from the keymap** ([#1991](https://github.com/TigerVNC/tigervnc/issues/1991)). These are still
  dropped. On wlroots, w0vncserver owns the virtual keyboard's keymap, so it could add a missing keysym to a spare
  keycode and upload the changed keymap before the key, as wtype does. libxkbcommon has no API to edit a keymap, so
  that means rewriting the keymap text
  ([libxkbcommon#814](https://github.com/xkbcommon/libxkbcommon/issues/814)). Not attempted here.

---

## Notes on the backports carried

Upstream merged these to master after 1.16.2. The fork applies them to 1.16.2 in master's order:

| What | Upstream |
| --- | --- |
| utf-8/ascii helpers, D-Bus signal subscription, `PortalProxy::call()` errors, portal clipboard | [#2012](https://github.com/TigerVNC/tigervnc/pull/2012) |
| ext-data-control-v1 clipboard | [#2049](https://github.com/TigerVNC/tigervnc/pull/2049) |
| DataControl and portal clipboard fixes (leaks, use-after-free, large writes) | [#2078](https://github.com/TigerVNC/tigervnc/pull/2078), [#2120](https://github.com/TigerVNC/tigervnc/pull/2120) |
| null keyboard in `VirtualKeyboard`, error handling across the Wayland backend | [#2053](https://github.com/TigerVNC/tigervnc/pull/2053) |
| `WaylandPixelBuffer`/`ScreencopyManager` refactor | [#2048](https://github.com/TigerVNC/tigervnc/pull/2048) |
| ext-image-copy-capture with cursor session, transformed outputs | [#2065](https://github.com/TigerVNC/tigervnc/pull/2065) |
| event pump, pixel buffer and compositor error handling | [#2120](https://github.com/TigerVNC/tigervnc/pull/2120) |
| `restore_token` validation | [#2133](https://github.com/TigerVNC/tigervnc/pull/2133) |
| backend detection that survives a missing portal | [#2159](https://github.com/TigerVNC/tigervnc/pull/2159) |
| `GWaylandSource` flushing, null checks | [#2170](https://github.com/TigerVNC/tigervnc/pull/2170) |
| capture stream failures, buffer size validation | [#2165](https://github.com/TigerVNC/tigervnc/pull/2165) |
| whole frame when PipeWire reports no damage | [#2186](https://github.com/TigerVNC/tigervnc/pull/2186) |

Two of them needed small changes for 1.16. They have no translated log messages, so the `_()` wrappers were dropped
in #2159's and #2165's. Each commit says so in a `[Backported to 1.16: ...]` note.
