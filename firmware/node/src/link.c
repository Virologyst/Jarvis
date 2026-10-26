#include "link.h"
#include "protocol.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define TX_BUF_SIZE    16384
#define RX_BUF_SIZE    1024
#define FRAME_MAX      4096
#define CMD_LINE_MAX   128
#define SEND_TIMEOUT   pdMS_TO_TICKS(20)   // never stall the node if the host stops reading

static SemaphoreHandle_t s_tx_lock;
static link_cmd_handler_t s_handler;
static volatile uint32_t s_dropped;
static volatile bool s_ready;
static volatile bool s_in_log;
static uint8_t s_frame[FRAME_MAX];

static uint16_t crc16_update(uint16_t crc, const uint8_t *data, size_t len)
{
    while (len--) {
        crc ^= (uint16_t)(*data++) << 8;
        for (int i = 0; i < 8; i++) {
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
        }
    }
    return crc;
}

bool link_send2(uint8_t type, const void *hdr, uint16_t hdr_len,
                const void *body, uint16_t body_len, bool droppable)
{
    const size_t len = hdr_len + body_len;
    const size_t total = 5 + len + 2;
    if (!s_ready || total > FRAME_MAX) {
        return false;
    }

    // Assemble the whole frame so it goes into the TX ring buffer in one all-or-nothing write
    xSemaphoreTakeRecursive(s_tx_lock, portMAX_DELAY);
    uint8_t *f = s_frame;
    f[0] = PROTO_MAGIC0;
    f[1] = PROTO_MAGIC1;
    f[2] = type;
    f[3] = len & 0xFF;
    f[4] = len >> 8;
    if (hdr_len) memcpy(f + 5, hdr, hdr_len);
    if (body_len) memcpy(f + 5 + hdr_len, body, body_len);
    const uint16_t crc = crc16_update(0xFFFF, f + 2, 3 + len);
    f[5 + len] = crc & 0xFF;
    f[6 + len] = crc >> 8;
    const int written = usb_serial_jtag_write_bytes(f, total, droppable ? 0 : SEND_TIMEOUT);
    xSemaphoreGiveRecursive(s_tx_lock);

    if (written != (int)total) {
        s_dropped++;
        return false;
    }
    return true;
}

void link_send(uint8_t type, const void *payload, uint16_t len)
{
    link_send2(type, payload, len, NULL, 0, false);
}

uint32_t link_tx_dropped(void)
{
    return s_dropped;
}

static int log_to_link(const char *fmt, va_list ap)
{
    char buf[200];
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    if (n <= 0 || s_in_log) {
        return n;
    }
    int len = n < (int)sizeof(buf) ? n : (int)sizeof(buf) - 1;
    while (len > 0 && (buf[len - 1] == '\n' || buf[len - 1] == '\r')) {
        len--;
    }
    s_in_log = true;
    link_send(MSG_LOG, buf, (uint16_t)len);
    s_in_log = false;
    return n;
}

static void rx_task(void *arg)
{
    char line[CMD_LINE_MAX];
    int pos = 0;
    uint8_t chunk[64];
    for (;;) {
        int n = usb_serial_jtag_read_bytes(chunk, sizeof(chunk), pdMS_TO_TICKS(100));
        for (int i = 0; i < n; i++) {
            char c = (char)chunk[i];
            if (c == '\n' || c == '\r') {
                if (pos > 0) {
                    line[pos] = '\0';
                    if (s_handler) {
                        s_handler(line);
                    }
                    pos = 0;
                }
            } else if (pos < CMD_LINE_MAX - 1) {
                line[pos++] = c;
            }
        }
    }
}

void link_init(link_cmd_handler_t handler)
{
    s_handler = handler;
    s_tx_lock = xSemaphoreCreateRecursiveMutex();

    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    cfg.tx_buffer_size = TX_BUF_SIZE;
    cfg.rx_buffer_size = RX_BUF_SIZE;
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));

    s_ready = true;
    esp_log_set_vprintf(log_to_link);
    xTaskCreatePinnedToCore(rx_task, "link_rx", 4096, NULL, 5, NULL, 0);
}
