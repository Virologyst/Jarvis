// I2S capture: one controller, master, stereo Philips, 32-bit slots (INMP441 24-bit data).
#pragma once

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t audio_init(uint32_t sample_rate);

// Blocks until `frames` stereo frames are read. buf holds 2 * frames raw I2S words
// interleaved [M1, M2, ...]; shift right by 8 for the signed 24-bit sample.
size_t audio_read(int32_t *buf, size_t frames);

uint32_t audio_overruns(void);
