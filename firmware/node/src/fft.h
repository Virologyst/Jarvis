// In-place iterative radix-2 complex FFT on split real/imag arrays.
#pragma once

#include <stdbool.h>

bool fft_init(int n);                 // n must be a power of two
void fft_forward(float *re, float *im);
