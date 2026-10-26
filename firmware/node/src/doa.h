// Direction of arrival for one mic pair: band-limited GCC-PHAT evaluated on a fine,
// continuous delay grid, so precision is not limited to whole samples.
#pragma once

#include <stdbool.h>

typedef struct {
    float spacing_m;
    float sound_speed;
    float band_lo_hz;
    float band_hi_hz;
} doa_params_t;

typedef struct {
    float tau_s;        // M2 delay relative to M1
    float theta_deg;    // acos(c * tau / d)
    float coherence;    // peak SRP / bins used, 0..1
    float level_dbfs;   // in-band level, averaged over both channels
    int   bins;         // bins used
} doa_result_t;

bool doa_init(int fft_size, float sample_rate);

// x1, x2: fft_size samples each, full scale = 1.0
void doa_process(const float *x1, const float *x2, const doa_params_t *p, doa_result_t *out);
