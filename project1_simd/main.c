/*
 * Project 1: SIMD Advantage Profiling
 *
 * One kernel per binary, selected at compile time:
 *   -DSAXPY -DDOT -DMULTIPLY -DSTENCIL
 * Data type:
 *   -DTYPE=float | -DTYPE=double
 * Optimization label (for CSV/reporting only):
 *   -DOPT_NAME='"scalar"' etc.
 *
 * Usage:
 *   ./binary N WARMUP RUNS STRIDE ALIGN_SHIFT_ELEMS [RAW_CSV]
 *
 * STRIDE is the element distance between successive accesses inside each
 * residue-class pass.  Every element is touched exactly once, even when
 * STRIDE > 1; STRIDE is not a sampling/skip factor.  STRIDE=0 selects the
 * gather mode: a deterministic random permutation chooses the source index
 * for each output element, so the whole array is reached in random order.
 *
 * ALIGN_SHIFT_ELEMS intentionally offsets every array by that many elements,
 * which lets the same script study 64B-aligned and misaligned starts for both
 * float and double without invoking undefined byte-offset casts.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <time.h>
#include <unistd.h>

#ifndef TYPE
#define TYPE double
#endif

#ifndef OPT_NAME
#define OPT_NAME unknown
#endif
#define STR_INNER(x) #x
#define STR(x) STR_INNER(x)

#define DEFAULT_N (1 << 20)
#define DEFAULT_WARMUP 5
#define DEFAULT_RUNS 20
#define MAX_RUNS 1000
#define ALIGN_BYTES 64

static TYPE *g_x, *g_y, *g_z, *g_y0;
static uint32_t *g_idx;
static TYPE g_a = (TYPE)1.5;
static TYPE g_b = (TYPE)2.5;
static TYPE g_c = (TYPE)0.5;
static TYPE g_result = (TYPE)0;
static volatile TYPE g_sink = (TYPE)0;

static double now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

/* Deterministic, fast data generator: same stream for every build/type. */
static uint64_t g_rng = 0x9E3779B97F4A7C15ULL;

static inline uint64_t splitmix64(void) {
    uint64_t z = (g_rng += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static inline TYPE next_value(void) {
    /* Uniform in [-1, 1).  Zero-mean data keeps reductions well conditioned
     * even for a naive float32 accumulator at hundred-million-element sizes. */
    return (TYPE)(((double)(splitmix64() >> 11) / 9007199254740992.0) * 2.0 - 1.0);
}

static TYPE *alloc_array(size_t n, size_t shift_elems) {
    size_t bytes = (n + shift_elems + 16) * sizeof(TYPE);
    void *p = NULL;
    if (posix_memalign(&p, ALIGN_BYTES, bytes) != 0) return NULL;
    memset(p, 0, bytes);
    return (TYPE *)p;
}

static void init_data(size_t n, size_t shift_elems) {
    g_rng = 0x9E3779B97F4A7C15ULL;
    for (size_t i = 0; i < n + shift_elems; i++) {
        g_x[i] = next_value();
        g_y0[i] = next_value();
        g_y[i] = g_y0[i];
        g_z[i] = (TYPE)0;
    }
}

/* Deterministic random permutation for the gather access pattern. */
static uint32_t *alloc_indices(size_t n) {
    if (n > UINT32_MAX) return NULL;
    uint32_t *idx = (uint32_t *)malloc(n * sizeof(*idx));
    if (!idx) return NULL;
    for (size_t i = 0; i < n; i++) idx[i] = (uint32_t)i;
    uint64_t state = 0xD1B54A32D192ED03ULL;
    for (size_t i = n; i > 1; i--) {
        state += 0x9E3779B97F4A7C15ULL;
        uint64_t z = state;
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        size_t j = (size_t)((z ^ (z >> 31)) % i);
        uint32_t tmp = idx[i - 1];
        idx[i - 1] = idx[j];
        idx[j] = tmp;
    }
    return idx;
}

/* ----------------------------- kernels ---------------------------------- */

__attribute__((noinline))
static void kernel_unit(size_t n, TYPE *restrict x, TYPE *restrict y,
                        TYPE *restrict z) {
#if defined(SAXPY)
    for (size_t i = 0; i < n; i++) y[i] = g_a * x[i] + y[i];
#elif defined(DOT)
    TYPE s = (TYPE)0;
    for (size_t i = 0; i < n; i++) s += x[i] * y[i];
    g_result = s;
    g_sink = s;
#elif defined(MULTIPLY)
    for (size_t i = 0; i < n; i++) z[i] = x[i] * y[i];
#elif defined(STENCIL)
    if (n > 0) z[0] = x[0];
    for (size_t i = 1; i + 1 < n; i++)
        z[i] = g_a * x[i - 1] + g_b * x[i] + g_c * x[i + 1];
    if (n > 1) z[n - 1] = x[n - 1];
#else
    (void)n; (void)x; (void)y; (void)z;
#endif
}

__attribute__((noinline))
static void kernel_stride(size_t n, size_t stride, TYPE *restrict x,
                          TYPE *restrict y, TYPE *restrict z) {
    size_t passes = stride < n ? stride : n;
#if defined(SAXPY)
    for (size_t base = 0; base < passes; base++)
        for (size_t i = base; i < n; i += stride)
            y[i] = g_a * x[i] + y[i];
#elif defined(DOT)
    TYPE s = (TYPE)0;
    for (size_t base = 0; base < passes; base++)
        for (size_t i = base; i < n; i += stride)
            s += x[i] * y[i];
    g_result = s;
    g_sink = s;
#elif defined(MULTIPLY)
    for (size_t base = 0; base < passes; base++)
        for (size_t i = base; i < n; i += stride)
            z[i] = x[i] * y[i];
#elif defined(STENCIL)
    for (size_t base = 0; base < passes; base++)
        for (size_t i = base; i < n; i += stride) {
            if (i < stride || i + stride >= n)
                z[i] = x[i];
            else
                z[i] = g_a * x[i - stride] + g_b * x[i] + g_c * x[i + stride];
        }
#else
    (void)n; (void)stride; (void)x; (void)y; (void)z;
#endif
}

/* Random-permutation gather: each output element reads a random source index,
 * and the permutation visits every element exactly once. */
__attribute__((noinline))
static void kernel_gather(size_t n, const uint32_t *restrict idx,
                          TYPE *restrict x, TYPE *restrict y,
                          TYPE *restrict z) {
#if defined(SAXPY)
    for (size_t i = 0; i < n; i++) {
        size_t j = idx[i];
        y[i] = g_a * x[j] + y[i];
    }
#elif defined(DOT)
    TYPE s = (TYPE)0;
    for (size_t i = 0; i < n; i++) {
        size_t j = idx[i];
        s += x[j] * y[j];
    }
    g_result = s;
    g_sink = s;
#elif defined(MULTIPLY)
    for (size_t i = 0; i < n; i++) {
        size_t j = idx[i];
        z[i] = x[j] * y[j];
    }
#elif defined(STENCIL)
    for (size_t i = 0; i < n; i++) {
        size_t j = idx[i];
        if (j == 0 || j + 1 >= n)
            z[i] = x[j];
        else
            z[i] = g_a * x[j - 1] + g_b * x[j] + g_c * x[j + 1];
    }
#else
    (void)n; (void)idx; (void)x; (void)y; (void)z;
#endif
}

/* ------------------------- reference and validation ---------------------- */

static int validate_gather(size_t n, const uint32_t *idx, const TYPE *x,
                           const TYPE *y, const TYPE *z, const TYPE *y0);

static int validate(size_t n, size_t stride, const uint32_t *idx,
                    const TYPE *x, const TYPE *y, const TYPE *z,
                    const TYPE *y0) {
    int ok = 1;
    if (stride == 0) return validate_gather(n, idx, x, y, z, y0);
#if defined(SAXPY)
    for (size_t i = 0; i < n && ok; i++) {
        long double ref = (long double)g_a * (long double)x[i] +
                          (long double)y0[i];
        long double got = (long double)y[i];
        long double diff = fabsl(got - ref);
        long double tol = 1e-5L * (1.0L + fabsl(ref));
#ifdef TYPE_FLOAT
        tol = 1e-4L * (1.0L + fabsl(ref));
#endif
        if (diff > tol) ok = 0;
    }
#elif defined(DOT)
    {
        long double ref = 0.0L;
        size_t passes = stride < n ? stride : n;
        for (size_t base = 0; base < passes; base++)
            for (size_t i = base; i < n; i += stride)
                ref += (long double)x[i] * (long double)y[i];
        long double got = (long double)g_result;
        long double tol = 1e-6L * (1.0L + fabsl(ref));
#ifdef TYPE_FLOAT
        tol = 5e-3L * (1.0L + fabsl(ref));
#endif
        if (getenv("DEBUG_VALIDATE"))
            fprintf(stderr, "dot got=%.12Lg ref=%.12Lg diff=%.12Lg tol=%.12Lg\n",
                    got, ref, fabsl(got - ref), tol);
        if (fabsl(got - ref) > tol) ok = 0;
    }
#elif defined(MULTIPLY)
    for (size_t i = 0; i < n && ok; i++) {
        long double ref = (long double)x[i] * (long double)y[i];
        long double got = (long double)z[i];
        long double tol = 1e-6L * (1.0L + fabsl(ref));
#ifdef TYPE_FLOAT
        tol = 1e-4L * (1.0L + fabsl(ref));
#endif
        if (fabsl(got - ref) > tol) ok = 0;
    }
#elif defined(STENCIL)
    for (size_t i = 0; i < n && ok; i++) {
        long double ref;
        if (i < stride || i + stride >= n)
            ref = (long double)x[i];
        else
            ref = (long double)g_a * (long double)x[i - stride] +
                  (long double)g_b * (long double)x[i] +
                  (long double)g_c * (long double)x[i + stride];
        long double got = (long double)z[i];
        long double tol = 1e-6L * (1.0L + fabsl(ref));
#ifdef TYPE_FLOAT
        tol = 1e-4L * (1.0L + fabsl(ref));
#endif
        if (fabsl(got - ref) > tol) ok = 0;
    }
#endif
    return ok;
}

static int validate_gather(size_t n, const uint32_t *idx, const TYPE *x,
                           const TYPE *y, const TYPE *z, const TYPE *y0) {
    int ok = 1;
    (void)y0;

#if defined(SAXPY)
    for (size_t i = 0; i < n && ok; i++) {
        size_t j = idx[i];
        long double ref = (long double)g_a * (long double)x[j] +
                          (long double)y0[i];
        long double got = (long double)y[i];
        long double tol = 1e-5L * (1.0L + fabsl(ref));
#ifdef TYPE_FLOAT
        tol = 1e-4L * (1.0L + fabsl(ref));
#endif
        if (fabsl(got - ref) > tol) ok = 0;
    }
#elif defined(DOT)
    {
        long double ref = 0.0L;
        for (size_t i = 0; i < n; i++) {
            size_t j = idx[i];
            ref += (long double)x[j] * (long double)y[j];
        }
        long double got = (long double)g_result;
        long double tol = 1e-6L * (1.0L + fabsl(ref));
#ifdef TYPE_FLOAT
        tol = 5e-3L * (1.0L + fabsl(ref));
#endif
        if (getenv("DEBUG_VALIDATE"))
            fprintf(stderr, "gather dot got=%.12Lg ref=%.12Lg diff=%.12Lg tol=%.12Lg\n",
                    got, ref, fabsl(got - ref), tol);
        if (fabsl(got - ref) > tol) ok = 0;
    }
#elif defined(MULTIPLY)
    for (size_t i = 0; i < n && ok; i++) {
        size_t j = idx[i];
        long double ref = (long double)x[j] * (long double)y[j];
        long double got = (long double)z[i];
        long double tol = 1e-6L * (1.0L + fabsl(ref));
#ifdef TYPE_FLOAT
        tol = 1e-4L * (1.0L + fabsl(ref));
#endif
        if (fabsl(got - ref) > tol) ok = 0;
    }
#elif defined(STENCIL)
    for (size_t i = 0; i < n && ok; i++) {
        size_t j = idx[i];
        long double ref;
        if (j == 0 || j + 1 >= n)
            ref = (long double)x[j];
        else
            ref = (long double)g_a * (long double)x[j - 1] +
                  (long double)g_b * (long double)x[j] +
                  (long double)g_c * (long double)x[j + 1];
        long double got = (long double)z[i];
        long double tol = 1e-6L * (1.0L + fabsl(ref));
#ifdef TYPE_FLOAT
        tol = 1e-4L * (1.0L + fabsl(ref));
#endif
        if (fabsl(got - ref) > tol) ok = 0;
    }
#endif
    return ok;
}

static const char *kernel_name(void) {
#if defined(SAXPY)
    return "saxpy";
#elif defined(DOT)
    return "dot";
#elif defined(MULTIPLY)
    return "multiply";
#elif defined(STENCIL)
    return "stencil";
#else
    return "none";
#endif
}

/* Traffic and FLOPs per touched element. */
static void traffic(size_t touched, double *bytes, double *flops) {
#if defined(SAXPY)
    *bytes = (double)touched * (double)sizeof(TYPE) * 3.0;
    *flops = (double)touched * 2.0;
#elif defined(DOT)
    *bytes = (double)touched * (double)sizeof(TYPE) * 2.0;
    *flops = (double)touched * 2.0;
#elif defined(MULTIPLY)
    *bytes = (double)touched * (double)sizeof(TYPE) * 3.0;
    *flops = (double)touched * 1.0;
#elif defined(STENCIL)
    *bytes = (double)touched * (double)sizeof(TYPE) * 2.0;
    *flops = (double)touched * 5.0;
#else
    *bytes = 0.0;
    *flops = 0.0;
#endif
}

static int cmp_double(const void *pa, const void *pb) {
    double a = *(const double *)pa;
    double b = *(const double *)pb;
    return (a > b) - (a < b);
}

int main(int argc, char **argv) {
    size_t n = DEFAULT_N;
    int warmup = DEFAULT_WARMUP;
    int runs = DEFAULT_RUNS;
    size_t stride = 1;
    size_t shift_elems = 0;
    const char *raw_path = NULL;

    if (argc > 1) {
        long v = strtol(argv[1], NULL, 10);
        if (v > 0) n = (size_t)v;
    }
    if (argc > 2) {
        long v = strtol(argv[2], NULL, 10);
        if (v >= 0) warmup = (int)v;
    }
    if (argc > 3) {
        long v = strtol(argv[3], NULL, 10);
        if (v > 0) runs = (int)v;
    }
    if (argc > 4) {
        long v = strtol(argv[4], NULL, 10);
        if (v >= 0) stride = (size_t)v;
    }
    if (argc > 5) {
        long v = strtol(argv[5], NULL, 10);
        if (v >= 0) shift_elems = (size_t)v;
    }
    if (argc > 6) raw_path = argv[6];
    if (runs > MAX_RUNS) runs = MAX_RUNS;

    g_x = alloc_array(n, shift_elems);
    g_y = alloc_array(n, shift_elems);
    g_z = alloc_array(n, shift_elems);
    g_y0 = alloc_array(n, shift_elems);
    if (!g_x || !g_y || !g_z || !g_y0) {
        fprintf(stderr, "allocation failed for n=%zu\n", n);
        return 1;
    }

    TYPE *x = g_x + shift_elems;
    TYPE *y = g_y + shift_elems;
    TYPE *z = g_z + shift_elems;
    TYPE *y0 = g_y0 + shift_elems;

    init_data(n, shift_elems);
    if (stride == 0) {
        g_idx = alloc_indices(n);
        if (!g_idx) {
            fprintf(stderr, "index allocation failed for n=%zu\n", n);
            return 1;
        }
    }

    for (int w = 0; w < warmup; w++) {
        memcpy(y, y0, n * sizeof(TYPE));
        if (stride == 0) kernel_gather(n, g_idx, x, y, z);
        else if (stride == 1) kernel_unit(n, x, y, z);
        else kernel_stride(n, stride, x, y, z);
    }

    double *times = (double *)malloc((size_t)runs * sizeof(double));
    if (!times) return 1;

    FILE *raw = NULL;
    if (raw_path) {
        raw = fopen(raw_path, "a");
        if (raw) {
            fseek(raw, 0, SEEK_END);
            if (ftell(raw) == 0L)
                fprintf(raw, "kernel,optimization,type_size,n,stride,align_shift_elems,trial,seconds\n");
        }
    }

    for (int r = 0; r < runs; r++) {
        /* Reset in-place input outside the timed region.  For SAXPY this avoids
         * overflow across repetitions; for other kernels it is a no-op cost.
         * Every stride pass touches every element, so restore all of y. */
        memcpy(y, y0, n * sizeof(TYPE));

        double t0 = now_seconds();
        if (stride == 0) kernel_gather(n, g_idx, x, y, z);
        else if (stride == 1) kernel_unit(n, x, y, z);
        else kernel_stride(n, stride, x, y, z);
        double t1 = now_seconds();

        times[r] = t1 - t0;
        if (raw)
            fprintf(raw, "%s,%s,%zu,%zu,%zu,%zu,%d,%.12g\n", kernel_name(),
                    STR(OPT_NAME), sizeof(TYPE), n, stride, shift_elems, r, times[r]);
    }
    if (raw) fclose(raw);

    int ok = validate(n, stride, g_idx, x, y, z, y0);

    double sum = 0.0, mn = times[0], mx = times[0];
    for (int r = 0; r < runs; r++) {
        sum += times[r];
        if (times[r] < mn) mn = times[r];
        if (times[r] > mx) mx = times[r];
    }
    double mean = sum / (double)runs;
    double var = 0.0;
    for (int r = 0; r < runs; r++) {
        double d = times[r] - mean;
        var += d * d;
    }
    double sd = runs > 1 ? sqrt(var / (double)(runs - 1)) : 0.0;

    qsort(times, (size_t)runs, sizeof(double), cmp_double);
    double median = (runs & 1) ? times[runs / 2]
                               : 0.5 * (times[runs / 2 - 1] + times[runs / 2]);

    size_t touched = n;
    double bytes = 0.0, flops = 0.0;
    traffic(touched, &bytes, &flops);
    double gib_s = bytes / (mean > 1e-12 ? mean : 1e-12) / 1e9;
    double gflop_s = flops / (mean > 1e-12 ? mean : 1e-12) / 1e9;

    fprintf(stderr,
            "kernel,optimization,type_size,n,stride,align_shift_elems,align_offset_bytes,"
            "touched,valid,runs,mean_s,median_s,min_s,max_s,sd_s,GiB_s,GFLOP_s,"
            "arithmetic_intensity\n");
    printf("%s,%s,%zu,%zu,%zu,%zu,%zu,%zu,%d,%d,%.12g,%.12g,%.12g,%.12g,%.12g,"
           "%.9g,%.9g,%.9g\n",
           kernel_name(), STR(OPT_NAME), sizeof(TYPE), n, stride, shift_elems,
           shift_elems * sizeof(TYPE), touched, ok, runs, mean, median, mn, mx,
           sd, gib_s, gflop_s, flops / (bytes > 0.0 ? bytes : 1.0));

    free(times);
    free(g_x);
    free(g_y);
    free(g_z);
    free(g_y0);
    free(g_idx);
    return ok ? 0 : 2;
}
