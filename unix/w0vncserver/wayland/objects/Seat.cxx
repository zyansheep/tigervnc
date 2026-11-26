/* Copyright 2025-2026 Adam Halim for Cendio AB
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

#include <core/LogWriter.h>
#include <wayland-client-protocol.h>

#include "Display.h"
#include "Keyboard.h"
#include "Pointer.h"
#include "Seat.h"

using namespace wayland;

static core::LogWriter vlog("WSeat");

const wl_seat_listener Seat::listener = {
  .capabilities = [](void* data, wl_seat*,
                     uint32_t capabilities) {
    ((Seat*)data)->seatCapabilities(capabilities);
  },
  .name = [](void*, wl_seat*, const char*) {},
};

Seat::Seat(Display* display_, std::function<void(unsigned int)> setLEDstate_)
  : Object(display_, "wl_seat", &wl_seat_interface),
    seat(nullptr), display(display_), keyboard(nullptr),
    pointer(nullptr), pointerSerial(0), setLEDstate(setLEDstate_)
{
  seat = (wl_seat*)boundObject;

  wl_seat_add_listener(seat, &listener, this);
  display->roundtrip();
}

Seat::~Seat()
{
  if (seat)
    wl_seat_destroy(seat);

  delete keyboard;
  delete pointer;
}

void Seat::seatCapabilities(uint32_t capabilities)
{
  // The event lists every capability the seat has now, so a keyboard or
  // pointer we already have stays: replacing it would drop its keymap
  // (or the cursor session made from it) each time the other one comes
  // or goes, as a virtual keyboard or pointer does.

  if (capabilities & WL_SEAT_CAPABILITY_KEYBOARD) {
    if (!keyboard) {
      vlog.debug("Keyboard detected");
      keyboard = new Keyboard(display, this, setLEDstate);
    }
  } else if (keyboard) {
    vlog.debug("Keyboard removed");
    delete keyboard;
    keyboard = nullptr;
  }

  if (capabilities & WL_SEAT_CAPABILITY_POINTER) {
    if (!pointer) {
      vlog.debug("Pointer detected");
      pointer = new Pointer(display, this);
      pointerSerial++;
      pointerChanged();
    }
  } else if (pointer) {
    vlog.debug("Pointer removed");
    delete pointer;
    pointer = nullptr;
    pointerChanged();
  }
}

void Seat::addPointerListener(void* owner, std::function<void()> cb)
{
  pointerListeners[owner] = cb;
}

void Seat::removePointerListener(void* owner)
{
  pointerListeners.erase(owner);
}

void Seat::pointerChanged()
{
  // A copy: a listener may remove itself
  std::map<void*, std::function<void()>> listeners = pointerListeners;

  for (auto& entry : listeners)
    entry.second();
}
