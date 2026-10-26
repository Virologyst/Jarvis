#include "config.h"
#include "protocol.h"

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "config";
static const char *NS = "node";

node_config_t g_cfg = {
    .node_id = 0,
    .mode = MODE_BEARING,
    .spacing_m = 0.0508f,       // 20 breadboard rows
    .sound_speed = 343.0f,      // ~20 C
    .band_lo_hz = 1500.0f,
    .band_hi_hz = 3000.0f,
    .level_thresh_dbfs = -75.0f,
    .coh_thresh = 0.35f,
};

void config_load(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs init failed: %s", esp_err_to_name(err));
        return;
    }

    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) != ESP_OK) {
        ESP_LOGI(TAG, "no stored config, using defaults");
        return;
    }
    uint8_t id;
    uint32_t spacing_um, c_mm;
    if (nvs_get_u8(h, "id", &id) == ESP_OK) {
        g_cfg.node_id = id;
    }
    if (nvs_get_u32(h, "spacing_um", &spacing_um) == ESP_OK) {
        g_cfg.spacing_m = spacing_um * 1e-6f;
    }
    if (nvs_get_u32(h, "c_mm", &c_mm) == ESP_OK) {
        g_cfg.sound_speed = c_mm * 1e-3f;
    }
    nvs_close(h);
}

void config_save(void)
{
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "nvs open failed");
        return;
    }
    nvs_set_u8(h, "id", g_cfg.node_id);
    nvs_set_u32(h, "spacing_um", (uint32_t)(g_cfg.spacing_m * 1e6f + 0.5f));
    nvs_set_u32(h, "c_mm", (uint32_t)(g_cfg.sound_speed * 1e3f + 0.5f));
    nvs_commit(h);
    nvs_close(h);
}
