#include "audio.h"
#include "config.h"

#include "driver/i2s_std.h"
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"

static const char *TAG = "audio";

static i2s_chan_handle_t s_rx;
static volatile uint32_t s_overruns;

static bool IRAM_ATTR on_rx_overflow(i2s_chan_handle_t handle, i2s_event_data_t *event, void *ctx)
{
    s_overruns++;
    return false;
}

esp_err_t audio_init(uint32_t sample_rate)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 8;
    chan_cfg.dma_frame_num = 256;   // 8 x 256 frames = 128 ms of buffering at 16 kHz
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, NULL, &s_rx), TAG, "new channel");

    // 32-bit stereo slots give the 64 SCK per WS the INMP441 requires.
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(sample_rate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_I2S_SCK,
            .ws = PIN_I2S_WS,
            .dout = I2S_GPIO_UNUSED,
            .din = PIN_I2S_SD,
            .invert_flags = {0},
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_rx, &std_cfg), TAG, "init std");

    i2s_event_callbacks_t cbs = {
        .on_recv_q_ovf = on_rx_overflow,
    };
    ESP_RETURN_ON_ERROR(i2s_channel_register_event_callback(s_rx, &cbs, NULL), TAG, "callbacks");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_rx), TAG, "enable");

    ESP_LOGI(TAG, "I2S up: %lu Hz, SCK=%d WS=%d SD=%d", (unsigned long)sample_rate,
             PIN_I2S_SCK, PIN_I2S_WS, PIN_I2S_SD);
    return ESP_OK;
}

size_t audio_read(int32_t *buf, size_t frames)
{
    size_t got = 0;
    i2s_channel_read(s_rx, buf, frames * 2 * sizeof(int32_t), &got, portMAX_DELAY);
    return got / (2 * sizeof(int32_t));
}

uint32_t audio_overruns(void)
{
    return s_overruns;
}
