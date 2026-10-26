// Acoustic node: 2 x INMP441 on one I2S line -> GCC-PHAT bearing -> USB serial to the Pi.
//
// Commands (one per line, from the host):
//   mode off|bearing|raw|both    what to stream (not persisted; boots in bearing mode)
//   id <n>                       node id 0-255 (persisted)
//   spacing <mm>                 measured M1-M2 port spacing (persisted)
//   c <m/s>                      speed of sound (persisted)
//   band <lo_hz> <hi_hz>         analysis band
//   thresh <level_dbfs> <coh>    detection gate
//   info                         heartbeat now + config log line
//   reboot
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "audio.h"
#include "config.h"
#include "doa.h"
#include "link.h"
#include "protocol.h"

static const char *TAG = "node";

#define STARTUP_DISCARD_FRAMES  (SAMPLE_RATE_HZ * 3 / 10)  // INMP441 outputs zeros for ~256 ms
#define FULL_SCALE              8388608.0f                 // 2^23

static portMUX_TYPE s_stats_lock = portMUX_INITIALIZER_UNLOCKED;
static float s_rms[MIC_COUNT];
static float s_peak[MIC_COUNT];

static float to_dbfs(float amplitude)
{
    return 20.0f * log10f(amplitude + 1e-12f);
}

static void send_heartbeat(void)
{
    msg_heartbeat_t hb = {
        .proto_version = PROTO_VERSION,
        .node_id = g_cfg.node_id,
        .mode = g_cfg.mode,
        .mic_count = MIC_COUNT,
        .uptime_ms = (uint32_t)(esp_timer_get_time() / 1000),
        .sample_rate = SAMPLE_RATE_HZ,
        .spacing_m = g_cfg.spacing_m,
        .i2s_overruns = audio_overruns(),
        .tx_dropped = link_tx_dropped(),
    };
    taskENTER_CRITICAL(&s_stats_lock);
    for (int ch = 0; ch < MIC_COUNT; ch++) {
        // RMS of a full-scale sine is 1/sqrt(2); report it as 0 dBFS like the datasheet
        hb.rms_dbfs[ch] = to_dbfs(s_rms[ch] * 1.41421356f);
        hb.peak_dbfs[ch] = to_dbfs(s_peak[ch]);
        s_peak[ch] = 0.0f;
    }
    taskEXIT_CRITICAL(&s_stats_lock);
    strncpy(hb.fw_version, FW_VERSION, sizeof(hb.fw_version) - 1);
    link_send(MSG_HEARTBEAT, &hb, sizeof(hb));
}

static void log_config(void)
{
    ESP_LOGI(TAG, "id=%u mode=%u spacing=%.1fmm c=%.1f band=%.0f-%.0fHz thresh=%.1fdBFS/%.2f",
             g_cfg.node_id, g_cfg.mode, g_cfg.spacing_m * 1000.0f, g_cfg.sound_speed,
             g_cfg.band_lo_hz, g_cfg.band_hi_hz, g_cfg.level_thresh_dbfs, g_cfg.coh_thresh);
}

static void handle_command(char *line)
{
    char *cmd = strtok(line, " ");
    char *a1 = strtok(NULL, " ");
    char *a2 = strtok(NULL, " ");
    if (!cmd) {
        return;
    }

    if (!strcmp(cmd, "mode") && a1) {
        if (!strcmp(a1, "off")) g_cfg.mode = MODE_OFF;
        else if (!strcmp(a1, "bearing")) g_cfg.mode = MODE_BEARING;
        else if (!strcmp(a1, "raw")) g_cfg.mode = MODE_RAW;
        else if (!strcmp(a1, "both")) g_cfg.mode = MODE_BOTH;
        else { ESP_LOGW(TAG, "err unknown mode '%s'", a1); return; }
    } else if (!strcmp(cmd, "id") && a1) {
        g_cfg.node_id = (uint8_t)atoi(a1);
        config_save();
    } else if (!strcmp(cmd, "spacing") && a1) {
        float mm = strtof(a1, NULL);
        if (mm < 5.0f || mm > 500.0f) { ESP_LOGW(TAG, "err spacing out of range"); return; }
        g_cfg.spacing_m = mm / 1000.0f;
        config_save();
    } else if (!strcmp(cmd, "c") && a1) {
        float c = strtof(a1, NULL);
        if (c < 300.0f || c > 380.0f) { ESP_LOGW(TAG, "err speed of sound out of range"); return; }
        g_cfg.sound_speed = c;
        config_save();
    } else if (!strcmp(cmd, "band") && a1 && a2) {
        float lo = strtof(a1, NULL), hi = strtof(a2, NULL);
        if (lo < 50.0f || hi <= lo || hi > SAMPLE_RATE_HZ / 2.0f) { ESP_LOGW(TAG, "err bad band"); return; }
        g_cfg.band_lo_hz = lo;
        g_cfg.band_hi_hz = hi;
    } else if (!strcmp(cmd, "thresh") && a1 && a2) {
        g_cfg.level_thresh_dbfs = strtof(a1, NULL);
        g_cfg.coh_thresh = strtof(a2, NULL);
    } else if (!strcmp(cmd, "info")) {
        send_heartbeat();
    } else if (!strcmp(cmd, "reboot")) {
        esp_restart();
    } else {
        ESP_LOGW(TAG, "err unknown command '%s'", cmd);
        return;
    }
    log_config();
}

static void audio_task(void *arg)
{
    static int32_t raw[HOP_SIZE * MIC_COUNT];
    static float ring1[FFT_SIZE], ring2[FFT_SIZE];
    static int32_t pcm[HOP_SIZE * MIC_COUNT];
    static uint8_t packed[HOP_SIZE * MIC_COUNT * 3];   // 24-bit LE for the wire

    uint32_t sample_index = 0;
    uint32_t audio_seq = 0, bearing_seq = 0;
    int filled = 0;

    for (;;) {
        const size_t n = audio_read(raw, HOP_SIZE);
        if (n != HOP_SIZE) {
            continue;
        }
        const uint32_t first = sample_index;
        sample_index += n;

        // Convert, keep level stats, and slide the analysis window
        memmove(ring1, ring1 + HOP_SIZE, sizeof(float) * (FFT_SIZE - HOP_SIZE));
        memmove(ring2, ring2 + HOP_SIZE, sizeof(float) * (FFT_SIZE - HOP_SIZE));
        float sum[MIC_COUNT] = {0}, peak[MIC_COUNT] = {0};
        for (int i = 0; i < HOP_SIZE; i++) {
            for (int ch = 0; ch < MIC_COUNT; ch++) {
                const int32_t s = raw[i * MIC_COUNT + ch] >> 8;   // signed 24-bit
                pcm[i * MIC_COUNT + ch] = s;
                uint8_t *q = &packed[(i * MIC_COUNT + ch) * 3];
                q[0] = s & 0xFF;
                q[1] = (s >> 8) & 0xFF;
                q[2] = (s >> 16) & 0xFF;
                const float x = s / FULL_SCALE;
                sum[ch] += x * x;
                if (fabsf(x) > peak[ch]) peak[ch] = fabsf(x);
            }
            ring1[FFT_SIZE - HOP_SIZE + i] = pcm[i * MIC_COUNT] / FULL_SCALE;
            ring2[FFT_SIZE - HOP_SIZE + i] = pcm[i * MIC_COUNT + 1] / FULL_SCALE;
        }
        if (first < STARTUP_DISCARD_FRAMES) {
            continue;
        }
        taskENTER_CRITICAL(&s_stats_lock);
        for (int ch = 0; ch < MIC_COUNT; ch++) {
            s_rms[ch] = sqrtf(sum[ch] / HOP_SIZE);
            if (peak[ch] > s_peak[ch]) s_peak[ch] = peak[ch];
        }
        taskEXIT_CRITICAL(&s_stats_lock);
        if (filled < FFT_SIZE) {
            filled += HOP_SIZE;
        }

        const uint8_t mode = g_cfg.mode;

        if (mode & MODE_RAW) {
            msg_audio_hdr_t hdr = {
                .node_id = g_cfg.node_id,
                .channels = MIC_COUNT,
                .n_frames = HOP_SIZE,
                .seq = audio_seq++,
                .first_sample = first,
            };
            link_send2(MSG_AUDIO, &hdr, sizeof(hdr), packed, sizeof(packed), true);
        }

        if ((mode & MODE_BEARING) && filled >= FFT_SIZE) {
            const doa_params_t p = {
                .spacing_m = g_cfg.spacing_m,
                .sound_speed = g_cfg.sound_speed,
                .band_lo_hz = g_cfg.band_lo_hz,
                .band_hi_hz = g_cfg.band_hi_hz,
            };
            doa_result_t r;
            doa_process(ring1, ring2, &p, &r);

            msg_bearing_t b = {
                .node_id = g_cfg.node_id,
                .flags = BEARING_FLAG_LINEAR,
                .seq = bearing_seq++,
                .t_ms = (uint32_t)(esp_timer_get_time() / 1000),
                .theta_deg = r.theta_deg,
                .elev_deg = NAN,
                .coherence = r.coherence,
                .level_dbfs = r.level_dbfs,
                .tau_us = r.tau_s * 1e6f,
            };
            if (r.coherence >= g_cfg.coh_thresh && r.level_dbfs >= g_cfg.level_thresh_dbfs) {
                b.flags |= BEARING_FLAG_DETECTED;
            }
            link_send(MSG_BEARING, &b, sizeof(b));
        }
    }
}

void app_main(void)
{
    config_load();
    link_init(handle_command);
    ESP_LOGI(TAG, "acoustic node fw %s", FW_VERSION);
    log_config();

    if (!doa_init(FFT_SIZE, SAMPLE_RATE_HZ)) {
        ESP_LOGE(TAG, "doa init failed (out of memory)");
        return;
    }
    if (audio_init(SAMPLE_RATE_HZ) != ESP_OK) {
        ESP_LOGE(TAG, "audio init failed");
        return;
    }
    xTaskCreatePinnedToCore(audio_task, "audio", 6144, NULL, 10, NULL, 1);

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(500));
        send_heartbeat();
    }
}
