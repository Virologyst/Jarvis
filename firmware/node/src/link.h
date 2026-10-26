// Framed binary link to the host over the ESP32-S3's native USB (USB-Serial-JTAG, the "USB" port).
// ESP_LOG output is redirected into MSG_LOG frames so text never corrupts the stream.
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef void (*link_cmd_handler_t)(char *line);

void link_init(link_cmd_handler_t handler);

// Send one frame, waiting briefly for buffer space; dropped and counted if the host isn't reading.
void link_send(uint8_t type, const void *payload, uint16_t len);

// Two-part payload (header + body). If `droppable` and the TX buffer can't take the whole
// frame right now, the frame is dropped and counted instead of blocking the caller.
bool link_send2(uint8_t type, const void *hdr, uint16_t hdr_len,
                const void *body, uint16_t body_len, bool droppable);

uint32_t link_tx_dropped(void);
