/* Exact ordered float32-accumulate convolution for rf_cnn_runtime.
 * The Python reference deliberately computes each product in float64, adds the
 * current float32 accumulator in float64, and rounds back to float32 after
 * every term.  Bias is added afterwards in float32.  This C routine mirrors
 * that order so the board can keep the numerical contract at ARM speed.
 */
#include <stddef.h>

void ordered_conv2d(const float *x, int cin, int lin, int cout, int kernel, int pad,
                    const float *weight, const float *bias, float *out) {
    int lout = (lin + 2 * pad - kernel) / 2 + 1;
    int c, t, o, n;
    for (o = 0; o < cout; ++o) {
        for (n = 0; n < lout; ++n) {
            float acc = 0.0f;
            for (c = 0; c < cin; ++c) {
                const float *xrow = x + (size_t)c * lin;
                const float *wrow = weight + ((size_t)o * cin + c) * kernel;
                for (t = 0; t < kernel; ++t) {
                    int pos = n * 2 + t - pad;
                    double xv = (pos >= 0 && pos < lin) ? (double)xrow[pos] : 0.0;
                    double prod = xv * (double)wrow[t];
                    double sum = prod + (double)acc;
                    acc = (float)sum;
                }
            }
            out[(size_t)o * lout + n] = acc + bias[o];
        }
    }
}
