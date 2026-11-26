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

#ifndef __WAYLAND_DATA_CONTROL_H__
#define __WAYLAND_DATA_CONTROL_H__

struct ext_data_control_manager_v1;
struct ext_data_control_device_v1;
struct ext_data_control_source_v1;
struct ext_data_control_offer_v1;
struct ext_data_control_device_v1_listener;
struct ext_data_control_source_v1_listener;
struct ext_data_control_offer_v1_listener;

#include <functional>
#include <string>
#include <map>
#include <queue>
#include <set>

#include <gio/gio.h>

#include "Object.h"

struct PendingData;
struct ReadContext;

namespace wayland {
  class Seat;

  class DataControl : public Object {
  public:
    DataControl(Display* display, Seat* seat,
                std::function<void(bool available)> clipboardAnnounceCb,
                std::function<void()> clipboardRequestCb,
                std::function<void(const char* data)> sendClipboardDataCb);
    virtual ~DataControl();

    // ext_data_control_device_v1 functions
    void setSelection();
    void setPrimarySelection();

    void clearSelection();
    void clearPrimarySelection();

    // ext_data_control_offer_v1 functions
    void receive();

    // Write to all pending clipboard requests
    void writePending(const char* data);

  private:
    // ext_data_control_device_v1 handlers
    void handleDataOffer(ext_data_control_offer_v1* offer);
    void handleSelection(ext_data_control_offer_v1* offer);
    void handlePrimarySelection(ext_data_control_offer_v1* offer);
    void handleFinished();

    // ext_data_control_source_v1 handlers
    void handleSend(const char* mimeType, int32_t fd);
    void handleCancelled(ext_data_control_source_v1* source);

    // ext_data_control_offer_v1 handlers
    void handleOffer(ext_data_control_offer_v1* offer, const char* mimeType);

    // The offer viewers are told of and receive() reads: the
    // clipboard's or (with SendPrimary) the primary selection's,
    // whichever changed last
    void setCurrent(ext_data_control_offer_v1* offer);
    // Whether an offer is one of ours, and whether it has text
    bool isOwn(ext_data_control_offer_v1* offer);
    bool hasText(ext_data_control_offer_v1* offer);
    // Destroy the offers that are neither selection any more
    void pruneOffers();
    void cancelRead();

    void handleReadDataCallback(GAsyncResult* res);

    ext_data_control_source_v1* createDataSource();

    void clearPendingWrites();

  private:
    ext_data_control_manager_v1* manager;
    ext_data_control_device_v1* device;

    // Every offer the compositor has introduced and we have not
    // destroyed, with its mime types. The clipboard and the primary
    // selection each have their own: a new primary selection must
    // not take the place of the clipboard, or cancel reading it.
    std::map<ext_data_control_offer_v1*, std::set<std::string>> offers;
    ext_data_control_offer_v1* selectionOffer;
    ext_data_control_offer_v1* primaryOffer;
    ext_data_control_offer_v1* currentOffer;

    ext_data_control_source_v1* selectionSource;
    ext_data_control_source_v1* primarySource;

    ReadContext* readContext;
    const char* pendingReadMimeType;
    GInputStream* readStream;
    GCancellable* readCancellable;
    std::string readBuffer;
    // A read was asked for while a cancelled one had not finished:
    // it starts once that one has
    bool receiveQueued;
    std::queue<PendingData> pendingWrites;

    std::function<void(bool available)> clipboardAnnounceCb;
    std::function<void()> clipboardRequestCb;
    std::function<void(const char* data)> sendClipboardDataCb;

    static const ext_data_control_device_v1_listener deviceListener;
    static const ext_data_control_source_v1_listener sourceListener;
    static const ext_data_control_offer_v1_listener offerListener;
  };
};

#endif // __WAYLAND_DATA_CONTROL_H__
