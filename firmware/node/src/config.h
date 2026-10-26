// Runtime configuration. node_id, spacing and speed of sound persist in NVS.
#pragma once

#include <stdint.h>

#define FW_VERSION      "0.1.0"

#define SAMPLE_RATE_HZ  16000
#define FFT_SIZE        1024
#define HOP_SIZE        512
#define MIC_COUNT       2

#define PIN_I2S_SCK     4
#define PIN_I2S_WS      5
#define PIN_I2S_SD      6


typedef struct {
    uint8_t node_id;
    uint8_t mode;
    float   spacing_m;
    float   sound_speed;
    float   band_lo_hz;
    float   band_hi_hz;
    float   level_thresh_dbfs;
    float   coh_thresh;
} node_config_t;

extern node_config_t g_cfg;

void config_load(void);
void config_save(void);
