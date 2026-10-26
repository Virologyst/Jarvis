#include "fft.h"

#include <math.h>
#include <stdlib.h>

static int s_n;
static float *s_cos;    // n/2 twiddles
static float *s_sin;

bool fft_init(int n)
{
    if (n < 2 || (n & (n - 1))) {
        return false;
    }
    s_n = n;
    s_cos = malloc(sizeof(float) * n / 2);
    s_sin = malloc(sizeof(float) * n / 2);
    if (!s_cos || !s_sin) {
        return false;
    }
    for (int i = 0; i < n / 2; i++) {
        double a = -2.0 * M_PI * i / n;
        s_cos[i] = (float)cos(a);
        s_sin[i] = (float)sin(a);
    }
    return true;
}

void fft_forward(float *re, float *im)
{
    const int n = s_n;

    // Bit-reversal permutation
    for (int i = 1, j = 0; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) {
            j ^= bit;
        }
        j ^= bit;
        if (i < j) {
            float t = re[i]; re[i] = re[j]; re[j] = t;
            t = im[i]; im[i] = im[j]; im[j] = t;
        }
    }

    // Butterflies
    for (int len = 2; len <= n; len <<= 1) {
        const int half = len >> 1;
        const int step = n / len;
        for (int i = 0; i < n; i += len) {
            for (int k = 0; k < half; k++) {
                const float wr = s_cos[k * step];
                const float wi = s_sin[k * step];
                const int a = i + k;
                const int b = a + half;
                const float tr = re[b] * wr - im[b] * wi;
                const float ti = re[b] * wi + im[b] * wr;
                re[b] = re[a] - tr;
                im[b] = im[a] - ti;
                re[a] += tr;
                im[a] += ti;
            }
        }
    }
}
