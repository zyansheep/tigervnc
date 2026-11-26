/* Copyright 2025 Adam Halim for Cendio AB
 *
 * This is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This software is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this software; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307,
 * USA.
 */

#ifdef HAVE_CONFIG_H
#include <config.h>
#endif

#include <assert.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include <stdexcept>

#include <virtual-keyboard-unstable-v1.h>
#include <xkbcommon/xkbcommon.h>
#include <xkbcommon/xkbcommon-keysyms.h>

#include <core/LogWriter.h>
#include <rfb/KeysymStr.h>
#include <rfb/ledStates.h>

#include "../../w0vncserver.h"
#include "../../parameters.h"
#include "Display.h"
#include "Seat.h"
#include "Keyboard.h"
#include "VirtualKeyboard.h"

using namespace wayland;

static core::LogWriter vlog("WaylandVirtualKeyboard");

// A seat with no keyboard (a headless compositor has no input devices)
// has no keymap to copy. Give the virtual keyboard libxkbcommon's default
// one (the XKB_DEFAULT_* variables, else "us"): the compositor then adds
// it to the seat, the seat gets a keyboard whose keymap is this one, and
// keymapUpdated() takes it from there.
static void sendDefaultKeymap(zwp_virtual_keyboard_v1* keyboard)
{
  xkb_context* ctx;
  xkb_keymap* map;
  char* str;
  size_t size, done;
  int fd;

  ctx = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
  if (!ctx) {
    vlog.error("Failed to create xkb context for a default keymap");
    return;
  }
  map = xkb_keymap_new_from_names(ctx, nullptr, XKB_KEYMAP_COMPILE_NO_FLAGS);
  if (!map) {
    vlog.error("Failed to compile a default keymap");
    xkb_context_unref(ctx);
    return;
  }
  str = xkb_keymap_get_as_string(map, XKB_KEYMAP_FORMAT_TEXT_V1);
  xkb_keymap_unref(map);
  xkb_context_unref(ctx);
  if (!str) {
    vlog.error("Failed to write out a default keymap");
    return;
  }

  size = strlen(str) + 1;
  fd = memfd_create("w0vncserver-keymap", MFD_CLOEXEC);
  for (done = 0; fd >= 0 && done < size; ) {
    ssize_t n = write(fd, str + done, size - done);
    if (n <= 0) {
      close(fd);
      fd = -1;
    } else {
      done += n;
    }
  }
  free(str);
  if (fd < 0) {
    vlog.error("Failed to pass a default keymap");
    return;
  }

  vlog.debug("Seat has no keyboard - sending a default keymap");
  zwp_virtual_keyboard_v1_keymap(keyboard, XKB_KEYMAP_FORMAT_TEXT_V1, fd,
                                 size);
  close(fd);
}

VirtualKeyboard::VirtualKeyboard(Display* display, Seat* seat_)
  : Object(display, "zwp_virtual_keyboard_manager_v1",
           &zwp_virtual_keyboard_manager_v1_interface),
    manager(nullptr), keyboard(nullptr), seat(seat_),
    keymapSerial(0)
{
  manager = (zwp_virtual_keyboard_manager_v1*) boundObject;

  keyboard = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(manager,
                                                                     seat->getSeat());
  if (!keyboard)
    throw std::runtime_error("Failed to create virtual keyboard");

  if (!seat->getKeyboard())
    sendDefaultKeymap(keyboard);
  else if (seat->getKeyboard()->hasKeymap())
    setupKeyboard();
  else
    vlog.debug("Keyboard keymap is not set - keyboard will not work until it is set");
}

VirtualKeyboard::~VirtualKeyboard()
{
  if (manager)
    zwp_virtual_keyboard_manager_v1_destroy(manager);
  if (keyboard)
    zwp_virtual_keyboard_v1_destroy(keyboard);
}

unsigned int VirtualKeyboard::getLEDState()
{
  if (!keymapSerial || !seat->getKeyboard())
    return rfb::ledUnknown;

  if (keymapUpdated())
    setupKeyboard();

  return seat->getKeyboard()->getLEDState();
}

void VirtualKeyboard::key(uint32_t keysym, uint32_t keycode, bool down)
{
  timespec ts;
  uint32_t time;
  bool updated;
  uint32_t key;
  Keyboard* wKeyboard;
  uint32_t modsDepressed;
  uint32_t modsLatched;
  uint32_t modsLocked;
  uint32_t group;

  if (keymapUpdated())
    setupKeyboard();

  if (!keymapSerial)
    return;

  wKeyboard = seat->getKeyboard();
  if (!wKeyboard)
    return;

  key = XKB_KEYCODE_INVALID;

  if (rawKeyboard)
    key = wKeyboard->rfbcodeToKeycode(keycode);

  if (key == XKB_KEYCODE_INVALID) {
    key = wKeyboard->keysymToKeycode(keysym);
    if ((unsigned int)key == XKB_KEYCODE_INVALID) {
      vlog.debug("Unable to map keysym XK_%s (0x%04x), ignoring key press",
                 KeySymName(keysym), keysym);
      return;
    }
  }

  clock_gettime(CLOCK_MONOTONIC, &ts);
  time = (static_cast<uint64_t>(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000) &
         0xFFFFFFFF;

  // The Wayland documentation states that:
  //   "clients must add 8 to the key event keycode"
  // when format xkb_v1 is used. Subtract 8 to get the correct keycode.
  if (wKeyboard->getFormat() == XKB_KEYMAP_FORMAT_TEXT_V1)
    zwp_virtual_keyboard_v1_key(keyboard, time, key - 8, down);
  else
    zwp_virtual_keyboard_v1_key(keyboard, time, key, down);

  updated = wKeyboard->updateState(key, down, &modsDepressed,
                                   &modsLatched, &modsLocked, &group);
  if (updated) {
    zwp_virtual_keyboard_v1_modifiers(keyboard, modsDepressed,
                                      modsLatched, modsLocked, group);
  }
}

bool VirtualKeyboard::keymapUpdated()
{
  Keyboard* wKeyboard = seat->getKeyboard();

  // A keyboard without a keymap (none yet, or it failed to load) has
  // nothing to set up from
  if (!wKeyboard || !wKeyboard->hasKeymap())
    return false;

  return keymapSerial != wKeyboard->getKeymapSerial();
}

void VirtualKeyboard::setupKeyboard()
{
  assert(seat->getKeyboard());

  if (!keymapSerial)
    vlog.debug("Keymap set - keyboard will now work");

  zwp_virtual_keyboard_v1_keymap(keyboard,
                                 seat->getKeyboard()->getFormat(),
                                 seat->getKeyboard()->getFd(),
                                 seat->getKeyboard()->getSize());

  keymapSerial = seat->getKeyboard()->getKeymapSerial();
}
