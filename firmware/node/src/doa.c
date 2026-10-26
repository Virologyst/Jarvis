// Pair DOA. Reference implementation and tests: host/nodelink/doa.py, host/tests/test_offline.py.
//
// Band-limited GCC-PHAT on a fine continuous delay grid, plus two things that make it work
// on rotor noise (a sparse set of harmonics):
//   * the cross-spectrum is exponentially averaged over a few hops (ALPHA), and
//   * only bins well above the band's median cross-power are used (harmonic prior),
//     so noise-only bins between harmonics don't swamp the PHAT sum.
#include "doa.h"
#include "fft.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define TAU_GRID   241    // delay hypotheses across [-d/c, +d/c]
#define ALPHA      0.7f   // cross-spectrum smoothing, ~3 hops of memory
#define BIN_RATIO  4.0f   // keep bins with |C| > BIN_RATIO * median |C|
#define MIN_BINS   6

static int s_n;
static float s_fs;
static float *s_win;
static float *s_re, *s_im;
static float *s_avg_r, *s_avg_i;  // smoothed cross-spectrum, band bins only
static float *s_wr, *s_wi;        // PHAT-normalised, zero for rejected bins
static float *s_mag, *s_sorted;
static float s_srp[TAU_GRID];
static float s_win_energy;        // sum of w^2
static int s_band_lo = -1, s_band_hi = -1;

bool doa_init(int fft_size, float sample_rate)
{
    s_n = fft_size;
    s_fs = sample_rate;
    const int half = s_n / 2;
    s_win = malloc(sizeof(float) * s_n);
    s_re = malloc(sizeof(float) * s_n);
    s_im = malloc(sizeof(float) * s_n);
    s_avg_r = calloc(half, sizeof(float));
    s_avg_i = calloc(half, sizeof(float));
    s_wr = malloc(sizeof(float) * half);
    s_wi = malloc(sizeof(float) * half);
    s_mag = malloc(sizeof(float) * half);
    s_sorted = malloc(sizeof(float) * half);
    if (!s_win || !s_re || !s_im || !s_avg_r || !s_avg_i || !s_wr || !s_wi || !s_mag || !s_sorted
        || !fft_init(s_n)) {
        return false;
    }
    s_win_energy = 0.0f;
    for (int i = 0; i < s_n; i++) {
        s_win[i] = 0.5f - 0.5f * cosf(2.0f * (float)M_PI * i / s_n);  // periodic Hann
        s_win_energy += s_win[i] * s_win[i];
    }
    return true;
}

static int cmp_float(const void *a, const void *b)
{
    const float x = *(const float *)a, y = *(const float *)b;
    return (x > y) - (x < y);
}

void doa_process(const float *x1, const float *x2, const doa_params_t *p, doa_result_t *out)
{
    const int n = s_n;

    // Two real signals through one complex FFT: z = x1 + j*x2
    for (int i = 0; i < n; i++) {
        s_re[i] = x1[i] * s_win[i];
        s_im[i] = x2[i] * s_win[i];
    }
    fft_forward(s_re, s_im);

    int k_lo = (int)ceilf(p->band_lo_hz * n / s_fs);
    int k_hi = (int)floorf(p->band_hi_hz * n / s_fs);
    if (k_lo < 1) k_lo = 1;
    if (k_hi > n / 2 - 1) k_hi = n / 2 - 1;
    const int nb = k_hi - k_lo + 1;
    if (k_lo != s_band_lo || k_hi != s_band_hi) {
        memset(s_avg_r, 0, sizeof(float) * n / 2);
        memset(s_avg_i, 0, sizeof(float) * n / 2);
        s_band_lo = k_lo;
        s_band_hi = k_hi;
    }

    // Separate X1, X2 and smooth C = X1 * conj(X2).
    // If M2 hears the sound tau later than M1, C ~ exp(+j*w*tau).
    float power = 0.0f;
    for (int b = 0; b < nb; b++) {
        const int k = k_lo + b;
        const float zr = s_re[k], zi = s_im[k];
        const float wr = s_re[n - k], wi = -s_im[n - k];   // conj(Z[n-k])
        const float x1r = 0.5f * (zr + wr), x1i = 0.5f * (zi + wi);
        const float x2r = 0.5f * (zi - wi), x2i = -0.5f * (zr - wr);
        power += x1r * x1r + x1i * x1i + x2r * x2r + x2i * x2i;
        const float cr = x1r * x2r + x1i * x2i;
        const float ci = x1i * x2r - x1r * x2i;
        s_avg_r[b] = ALPHA * s_avg_r[b] + (1.0f - ALPHA) * cr;
        s_avg_i[b] = ALPHA * s_avg_i[b] + (1.0f - ALPHA) * ci;
        s_mag[b] = sqrtf(s_avg_r[b] * s_avg_r[b] + s_avg_i[b] * s_avg_i[b]);
    }

    // Harmonic prior: keep bins well above the band median; fall back to the strongest few
    memcpy(s_sorted, s_mag, sizeof(float) * nb);
    qsort(s_sorted, nb, sizeof(float), cmp_float);
    const float median = s_sorted[nb / 2];
    float thresh = BIN_RATIO * median;
    int kept = 0;
    for (int b = 0; b < nb; b++) {
        kept += s_mag[b] > thresh;
    }
    if (kept < MIN_BINS) {
        thresh = s_sorted[nb > MIN_BINS ? nb - MIN_BINS - 1 : 0];
    }
    kept = 0;
    for (int b = 0; b < nb; b++) {
        if (s_mag[b] > thresh && s_mag[b] > 1e-30f) {
            s_wr[b] = s_avg_r[b] / s_mag[b];
            s_wi[b] = s_avg_i[b] / s_mag[b];
            kept++;
        } else {
            s_wr[b] = 0.0f;
            s_wi[b] = 0.0f;
        }
    }

    // Steered response over the delay grid. The phase advances linearly with bin
    // index, so each hypothesis needs one sincos and then a complex rotation per bin.
    const float tau_max = p->spacing_m / p->sound_speed;
    const float d_tau = 2.0f * tau_max / (TAU_GRID - 1);
    const float bin_w = 2.0f * (float)M_PI * s_fs / n;
    int best = 0;
    for (int g = 0; g < TAU_GRID; g++) {
        const float tau = -tau_max + g * d_tau;
        const float step = bin_w * tau;
        float rr = cosf(step * k_lo), ri = -sinf(step * k_lo);   // exp(-j*w_klo*tau)
        const float sr = cosf(step), si = -sinf(step);
        float acc = 0.0f;
        for (int b = 0; b < nb; b++) {
            acc += s_wr[b] * rr - s_wi[b] * ri;
            const float t = rr * sr - ri * si;
            ri = rr * si + ri * sr;
            rr = t;
        }
        s_srp[g] = acc;
        if (acc > s_srp[best]) {
            best = g;
        }
    }

    // Parabolic refinement around the peak
    float offset = 0.0f;
    if (best > 0 && best < TAU_GRID - 1) {
        const float a = s_srp[best - 1], b = s_srp[best], c = s_srp[best + 1];
        const float den = a - 2.0f * b + c;
        if (den < 0.0f) {
            offset = 0.5f * (a - c) / den;
        }
    }
    const float tau = -tau_max + (best + offset) * d_tau;
    float ratio = tau / tau_max;
    if (ratio > 1.0f) ratio = 1.0f;
    if (ratio < -1.0f) ratio = -1.0f;

    // Band power by Parseval (one-sided spectrum, window energy corrected).
    // A full-scale sine has power 0.5, which is 0 dBFS.
    const float norm = 2.0f / (n * s_win_energy);
    const float p_band = 0.5f * power * norm;   // averaged over the two channels
    out->level_dbfs = 10.0f * log10f(p_band / 0.5f + 1e-20f);
    out->tau_s = tau;
    out->theta_deg = acosf(ratio) * 180.0f / (float)M_PI;
    out->coherence = kept ? s_srp[best] / kept : 0.0f;
    out->bins = kept;
}
