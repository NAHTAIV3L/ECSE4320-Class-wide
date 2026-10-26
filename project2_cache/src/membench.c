/*
 * Project 2: Cache & Memory Performance Profiling
 *
 * Portable single-thread microbenchmarks (Linux, x86-64 or any POSIX host):
 *
 *   membench latency   --size BYTES [--stride BYTES] [--mode random|seq]
 *                      [--chains K] [--trials T] [--iters N] [--huge 0|1]
 *
 *   membench bandwidth --size BYTES [--stride BYTES] [--mode seq|random]
 *                      [--rw read|write|copy|mix70|mix50]
 *                      [--trials T] [--huge 0|1]
 *
 * Each invocation prints one CSV row per trial (header on stderr):
 *   test,size_bytes,stride_bytes,mode,rw,chains,huge,trial,time_s,steps,
 *   accesses,bytes_moved,bytes_traffic_est,ns_per_step,ns_per_access,GiB_s,
 *   GiB_s_traffic
 *
 * Latency uses a dependent pointer chase (zero outstanding requests per chain).
 * Chains > 1 start at different positions on the same random cycle, keeping the
 * footprint and access distribution fixed while varying memory-level parallelism.
 * Bandwidth uses unrolled loads/stores; "traffic_est" accounts for 64 B lines
 * touched by strided/random accesses (a first-order estimate, not a counter).
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <inttypes.h>
#include <math.h>
#include <time.h>
#include <unistd.h>
#include <sys/mman.h>

#define LINE_BYTES 64
#define MAX_CHAINS 256

typedef struct {
    const char *test;
    size_t size;
    size_t stride;
    const char *mode;      /* seq | random */
    const char *rw;        /* read | write | copy | mix70 | mix50 */
    int chains;
    int huge;
    int trials;
    size_t iters;          /* latency only; 0 = auto */
} cfg_t;

static uint64_t g_rng = 0x9E3779B97F4A7C15ULL;

static inline uint64_t splitmix64(void) {
    uint64_t z = (g_rng += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

static void *alloc_region(size_t bytes, int huge) {
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    size_t rounded = (bytes + page - 1) & ~(page - 1);
    void *p = mmap(NULL, rounded, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) {
        perror("mmap");
        return NULL;
    }
#ifdef MADV_HUGEPAGE
    if (huge) {
        if (madvise(p, rounded, MADV_HUGEPAGE) != 0)
            fprintf(stderr, "warning: MADV_HUGEPAGE failed\n");
    } else {
        madvise(p, rounded, MADV_NOHUGEPAGE);
    }
#else
    (void)huge;
#endif
    /* Fault every page once. */
    for (size_t off = 0; off < rounded; off += page)
        ((volatile char *)p)[off] = 0;
    return p;
}

static void free_region(void *p, size_t bytes) {
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    size_t rounded = (bytes + page - 1) & ~(page - 1);
    munmap(p, rounded);
}

static int parse_args(int argc, char **argv, cfg_t *c) {
    if (argc < 2) return -1;
    memset(c, 0, sizeof(*c));
    c->test = argv[1];
    c->size = 64u << 20;          /* 64 MiB */
    c->stride = 64;
    c->mode = "random";
    c->rw = "read";
    c->chains = 1;
    c->huge = 0;
    c->trials = 7;
    c->iters = 0;

    for (int i = 2; i < argc; i++) {
        const char *a = argv[i];
        const char *v = (i + 1 < argc) ? argv[i + 1] : NULL;
        if (!v) { fprintf(stderr, "missing value for %s\n", a); return -1; }
        if (!strcmp(a, "--size")) c->size = (size_t)strtoull(v, NULL, 0);
        else if (!strcmp(a, "--stride")) c->stride = (size_t)strtoull(v, NULL, 0);
        else if (!strcmp(a, "--mode")) c->mode = v;
        else if (!strcmp(a, "--rw")) c->rw = v;
        else if (!strcmp(a, "--chains")) c->chains = atoi(v);
        else if (!strcmp(a, "--huge")) c->huge = atoi(v);
        else if (!strcmp(a, "--trials")) c->trials = atoi(v);
        else if (!strcmp(a, "--iters")) c->iters = (size_t)strtoull(v, NULL, 0);
        else { fprintf(stderr, "unknown option %s\n", a); return -1; }
        i++;
    }
    if (c->chains < 1) c->chains = 1;
    if (c->chains > MAX_CHAINS) c->chains = MAX_CHAINS;
    if (c->trials < 1) c->trials = 1;
    if (c->stride < sizeof(uint64_t)) c->stride = sizeof(uint64_t);
    if (c->stride % sizeof(uint64_t)) {
        fprintf(stderr, "stride must be a multiple of 8 bytes\n");
        return -1;
    }
    if (strcmp(c->test, "latency") && strcmp(c->test, "bandwidth")) {
        fprintf(stderr, "test must be latency or bandwidth\n");
        return -1;
    }
    return 0;
}

static void print_header(void) {
    fprintf(stderr,
            "test,size_bytes,stride_bytes,mode,rw,chains,huge,trial,time_s,"
            "steps,accesses,bytes_moved,bytes_traffic_est,ns_per_step,"
            "ns_per_access,GiB_s,GiB_s_traffic\n");
}

static void emit(const cfg_t *c, int trial, double time_s, size_t steps,
                 size_t accesses, size_t bytes_moved, size_t traffic) {
    double ns_step = (steps ? time_s * 1e9 / (double)steps : 0.0);
    double ns_acc = (accesses ? time_s * 1e9 / (double)accesses : 0.0);
    double gib = (time_s > 0.0) ? (double)bytes_moved / time_s / 1e9 : 0.0;
    double gibr = (time_s > 0.0) ? (double)traffic / time_s / 1e9 : 0.0;
    printf("%s,%zu,%zu,%s,%s,%d,%d,%d,%.12g,%zu,%zu,%zu,%zu,%.6f,%.6f,"
           "%.6f,%.6f\n",
           c->test, c->size, c->stride, c->mode, c->rw, c->chains, c->huge,
           trial, time_s, steps, accesses, bytes_moved, traffic, ns_step,
           ns_acc, gib, gibr);
    fflush(stdout);
}

/* ------------------------------- latency -------------------------------- */

static size_t *make_permutation(size_t nodes) {
    size_t *perm = (size_t *)malloc(nodes * sizeof(size_t));
    if (!perm) return NULL;
    for (size_t i = 0; i < nodes; i++) perm[i] = i;
    for (size_t i = nodes; i > 1; i--) {
        size_t j = (size_t)(splitmix64() % i);
        size_t t = perm[i - 1];
        perm[i - 1] = perm[j];
        perm[j] = t;
    }
    return perm;
}

static int run_latency(const cfg_t *c) {
    size_t nodes = c->size / c->stride;
    if (nodes < (size_t)c->chains + 2) {
        fprintf(stderr, "working set too small for %d chains\n", c->chains);
        return 1;
    }
    void *base = alloc_region(c->size, c->huge);
    if (!base) return 1;

    size_t *perm = NULL;
    int random = !strcmp(c->mode, "random");
    if (random) {
        perm = make_permutation(nodes);
        if (!perm) { free_region(base, c->size); return 1; }
    }

    /* Build a single cycle through the selected nodes. */
    for (size_t i = 0; i < nodes; i++) {
        size_t a = random ? perm[i] : i;
        size_t b = random ? perm[(i + 1) % nodes] : ((i + 1) % nodes);
        *(uintptr_t *)((char *)base + a * c->stride) =
            (uintptr_t)((char *)base + b * c->stride);
    }

    uintptr_t starts[MAX_CHAINS];
    for (int k = 0; k < c->chains; k++) {
        size_t pos = (size_t)(((__uint128_t)k * nodes) / (size_t)c->chains);
        size_t node = random ? perm[pos] : pos;
        starts[k] = (uintptr_t)((char *)base + node * c->stride);
    }
    free(perm);

    size_t iters = c->iters;
    if (!iters) {
        size_t want = nodes * 2;
        if (want < 20000) want = 20000;
        if (want > 2000000) want = 2000000;
        iters = want;
    }

    uintptr_t *p = (uintptr_t *)malloc((size_t)c->chains * sizeof(uintptr_t));
    if (!p) { free_region(base, c->size); return 1; }
    volatile uintptr_t sink = 0;

    /* Warm-up pass. */
    for (int k = 0; k < c->chains; k++) p[k] = starts[k];
    for (size_t it = 0; it < iters / 10 + 1; it++)
        for (int k = 0; k < c->chains; k++)
            p[k] = *(volatile uintptr_t *)p[k];
    sink ^= p[0];

    for (int t = 0; t < c->trials; t++) {
        for (int k = 0; k < c->chains; k++) p[k] = starts[k];
        double t0 = now_s();
        for (size_t it = 0; it < iters; it++) {
            for (int k = 0; k < c->chains; k++)
                p[k] = *(volatile uintptr_t *)p[k];
        }
        double t1 = now_s();
        sink ^= p[0];
        size_t accesses = iters * (size_t)c->chains;
        /* Each dependent load consumes one 64 B line. */
        size_t moved = accesses * LINE_BYTES;
        emit(c, t, t1 - t0, iters, accesses, moved, moved);
    }
    (void)sink;
    free(p);
    free_region(base, c->size);
    return 0;
}

/* ------------------------------ bandwidth ------------------------------- */

static inline size_t random_index(uint64_t *state, size_t n) {
    uint64_t r = splitmix64();
    (void)state;
    return (size_t)(((__uint128_t)r * (__uint128_t)n) >> 64);
}

static int run_bandwidth(const cfg_t *c) {
    size_t words = c->size / sizeof(uint64_t);
    size_t step = c->stride / sizeof(uint64_t);
    size_t nodes = c->size / c->stride;
    int random = !strcmp(c->mode, "random");
    if (words < 16 || (random && nodes < 16)) {
        fprintf(stderr, "working set too small\n");
        return 1;
    }
    size_t passes = step < words ? step : words;
    int two_buf = !strcmp(c->rw, "copy") || !strcmp(c->rw, "mix70") ||
                  !strcmp(c->rw, "mix50");
    void *src = alloc_region(c->size, c->huge);
    void *dst = two_buf ? alloc_region(c->size, c->huge) : NULL;
    if (!src || (two_buf && !dst)) {
        if (src) free_region(src, c->size);
        if (dst) free_region(dst, c->size);
        return 1;
    }

    volatile uint64_t sink = 0;

    for (int t = 0; t < c->trials; t++) {
        double t0 = now_s();
        if (!strcmp(c->rw, "read")) {
            if (!random) {
                uint64_t s0 = 0, s1 = 0, s2 = 0, s3 = 0, s4 = 0, s5 = 0, s6 = 0, s7 = 0;
                for (size_t base = 0; base < passes; base++) {
                    size_t i = base;
                    for (; i + 7 * step < words; i += 8 * step) {
                        const char *q = (const char *)src + i * sizeof(uint64_t);
                        s0 += *(const uint64_t *)(q + 0 * c->stride);
                        s1 += *(const uint64_t *)(q + 1 * c->stride);
                        s2 += *(const uint64_t *)(q + 2 * c->stride);
                        s3 += *(const uint64_t *)(q + 3 * c->stride);
                        s4 += *(const uint64_t *)(q + 4 * c->stride);
                        s5 += *(const uint64_t *)(q + 5 * c->stride);
                        s6 += *(const uint64_t *)(q + 6 * c->stride);
                        s7 += *(const uint64_t *)(q + 7 * c->stride);
                    }
                    for (; i < words; i += step)
                        s0 += *(const uint64_t *)((const char *)src + i * sizeof(uint64_t));
                }
                sink ^= s0 ^ s1 ^ s2 ^ s3 ^ s4 ^ s5 ^ s6 ^ s7;
            } else {
                uint64_t s0 = 0, s1 = 0, s2 = 0, s3 = 0;
                size_t i = 0;
                for (; i + 3 < nodes; i += 4) {
                    s0 += *(const uint64_t *)((const char *)src + random_index(NULL, nodes) * c->stride);
                    s1 += *(const uint64_t *)((const char *)src + random_index(NULL, nodes) * c->stride);
                    s2 += *(const uint64_t *)((const char *)src + random_index(NULL, nodes) * c->stride);
                    s3 += *(const uint64_t *)((const char *)src + random_index(NULL, nodes) * c->stride);
                }
                for (; i < nodes; i++)
                    s0 += *(const uint64_t *)((const char *)src + random_index(NULL, nodes) * c->stride);
                sink ^= s0 ^ s1 ^ s2 ^ s3;
            }
        } else if (!strcmp(c->rw, "write")) {
            if (!random) {
                for (size_t base = 0; base < passes; base++)
                    for (size_t i = base; i < words; i += step)
                        *(uint64_t *)((char *)src + i * sizeof(uint64_t)) = (uint64_t)i;
            } else {
                for (size_t i = 0; i < nodes; i++)
                    *(uint64_t *)((char *)src + random_index(NULL, nodes) * c->stride) = (uint64_t)i;
            }
        } else if (!strcmp(c->rw, "copy")) {
            if (!random) {
                for (size_t base = 0; base < passes; base++)
                    for (size_t i = base; i < words; i += step)
                        *(uint64_t *)((char *)dst + i * sizeof(uint64_t)) =
                            *(const uint64_t *)((const char *)src + i * sizeof(uint64_t));
            } else {
                for (size_t i = 0; i < nodes; i++) {
                    size_t j = random_index(NULL, nodes);
                    *(uint64_t *)((char *)dst + j * c->stride) =
                        *(const uint64_t *)((const char *)src + j * c->stride);
                }
            }
        } else if (!strcmp(c->rw, "mix70")) {
            if (!random) {
                for (size_t base = 0; base < passes; base++) {
                    uint64_t s = 0;
                    size_t i = base, k = 0;
                    for (; i + 9 * step < words; i += 10 * step, k += 10) {
                        for (int u = 0; u < 7; u++)
                            s += *(const uint64_t *)((const char *)src + (i + u * step) * sizeof(uint64_t));
                        for (int u = 7; u < 10; u++)
                            *(uint64_t *)((char *)dst + (i + u * step) * sizeof(uint64_t)) = (uint64_t)(i + u * step);
                    }
                    for (; i < words; i += step, k++) {
                        if ((k % 10) < 7)
                            s += *(const uint64_t *)((const char *)src + i * sizeof(uint64_t));
                        else
                            *(uint64_t *)((char *)dst + i * sizeof(uint64_t)) = (uint64_t)i;
                    }
                    sink ^= s;
                }
            } else {
                uint64_t s = 0;
                size_t i = 0;
                for (; i + 9 < nodes; i += 10) {
                    for (int u = 0; u < 7; u++)
                        s += *(const uint64_t *)((const char *)src + random_index(NULL, nodes) * c->stride);
                    for (int u = 7; u < 10; u++)
                        *(uint64_t *)((char *)dst + random_index(NULL, nodes) * c->stride) = (uint64_t)(i + u);
                }
                for (; i < nodes; i++) {
                    size_t j = random_index(NULL, nodes);
                    if ((i - (i / 10) * 10) < 7)
                        s += *(const uint64_t *)((const char *)src + j * c->stride);
                    else
                        *(uint64_t *)((char *)dst + j * c->stride) = (uint64_t)i;
                }
                sink ^= s;
            }
        } else { /* mix50 */
            if (!random) {
                for (size_t base = 0; base < passes; base++) {
                    uint64_t s = 0;
                    size_t i = base, k = 0;
                    for (; i + 9 * step < words; i += 10 * step, k += 10) {
                        for (int u = 0; u < 5; u++)
                            s += *(const uint64_t *)((const char *)src + (i + u * step) * sizeof(uint64_t));
                        for (int u = 5; u < 10; u++)
                            *(uint64_t *)((char *)dst + (i + u * step) * sizeof(uint64_t)) = (uint64_t)(i + u * step);
                    }
                    for (; i < words; i += step, k++) {
                        if ((k % 10) < 5)
                            s += *(const uint64_t *)((const char *)src + i * sizeof(uint64_t));
                        else
                            *(uint64_t *)((char *)dst + i * sizeof(uint64_t)) = (uint64_t)i;
                    }
                    sink ^= s;
                }
            } else {
                uint64_t s = 0;
                size_t i = 0;
                for (; i + 9 < nodes; i += 10) {
                    for (int u = 0; u < 5; u++)
                        s += *(const uint64_t *)((const char *)src + random_index(NULL, nodes) * c->stride);
                    for (int u = 5; u < 10; u++)
                        *(uint64_t *)((char *)dst + random_index(NULL, nodes) * c->stride) = (uint64_t)(i + u);
                }
                for (; i < nodes; i++) {
                    size_t j = random_index(NULL, nodes);
                    if (i & 1)
                        s += *(const uint64_t *)((const char *)src + j * c->stride);
                    else
                        *(uint64_t *)((char *)dst + j * c->stride) = (uint64_t)i;
                }
                sink ^= s;
            }
        }
        double t1 = now_s();

        size_t accesses = random ? nodes : words;
        size_t bytes;
        if (!strcmp(c->rw, "read") || !strcmp(c->rw, "write"))
            bytes = accesses * sizeof(uint64_t);
        else if (!strcmp(c->rw, "copy"))
            bytes = accesses * sizeof(uint64_t) * 2;
        else
            bytes = accesses * sizeof(uint64_t); /* exactly one op per element */

        /* First-order cache-line traffic estimate. */
        size_t streams = !strcmp(c->rw, "copy") ? 2 : 1;
        size_t traffic;
        if (random || c->stride >= LINE_BYTES)
            traffic = accesses * LINE_BYTES * streams;
        else
            traffic = c->size * streams;
        if (traffic < bytes) traffic = bytes;

        emit(c, t, t1 - t0, accesses, accesses, bytes, traffic);
    }
    (void)sink;
    if (dst) free_region(dst, c->size);
    free_region(src, c->size);
    return 0;
}

int main(int argc, char **argv) {
    cfg_t c;
    if (parse_args(argc, argv, &c) != 0) {
        fprintf(stderr,
                "usage: %s latency|bandwidth --size BYTES [--stride BYTES]\n"
                "       [--mode seq|random] [--chains K] [--trials T]\n"
                "       [--iters N] [--huge 0|1] [--rw read|write|copy|mix70|mix50]\n",
                argv[0]);
        return 1;
    }
    print_header();
    if (!strcmp(c.test, "latency")) return run_latency(&c);
    return run_bandwidth(&c);
}
