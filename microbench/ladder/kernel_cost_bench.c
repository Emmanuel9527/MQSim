// Pipeline-ladder study: host cost of the per-node work of a DiskANN round, split into the parts an in-SSD compute
// unit would have to take over. Synthetic data with the SIFT100M shapes (128-d uint8, R=64, 31 PQ chunks, L=100).
//   exact : full-precision L2 of one node vector against the query
//   pq    : fetch the PQ codes of 64 neighbours from a table of <npts> points (random ids) + ADC table lookup
//   list  : visited-set insert + ordered insert into the candidate list (capacity L) for 64 neighbours
// Usage: kernel_cost_bench <npts> [iters]     e.g. 100000 (codes fit in cache) and 100000000 (3.1 GB, memory bound)
// Build: gcc -O3 -march=native -o kernel_cost_bench kernel_cost_bench.c   (and -O2 -fno-tree-vectorize for scalar)
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define DIM 128
#define R 64
#define CH 31
#define L 100
static uint64_t rs = 88172645463325252ULL;
static inline uint64_t rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return rs; }
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1e9 + t.tv_nsec; }
typedef struct { float d; uint32_t id; } Cand;
int main(int argc, char **argv)
{
    size_t npts = argc > 1 ? strtoull(argv[1], 0, 10) : 100000;
    int iters = argc > 2 ? atoi(argv[2]) : 200000;
    uint8_t *codes = malloc(npts * CH);
    for (size_t i = 0; i < npts * CH; i += 8) { uint64_t v = rnd(); memcpy(codes + i, &v, npts * CH - i < 8 ? npts * CH - i : 8); }
    float *table = malloc(sizeof(float) * 256 * CH);
    for (int i = 0; i < 256 * CH; i++) table[i] = (float)(rnd() % 100000) / 7.0f;
    uint8_t q[DIM], *vecs = malloc(4096 * DIM);
    for (int i = 0; i < DIM; i++) q[i] = rnd();
    for (int i = 0; i < 4096 * DIM; i++) vecs[i] = rnd();
    uint32_t *ids = malloc(sizeof(uint32_t) * R * 4096);
    for (int i = 0; i < R * 4096; i++) ids[i] = rnd() % npts;
    uint8_t *agg = malloc(R * CH); float dist[R];
    size_t hs = 1 << 14; uint32_t *hash = malloc(sizeof(uint32_t) * hs);
    Cand list[L + 1]; int ln;
    volatile float sink = 0; double t0, te = 0, tp = 0, tl = 0;
    for (int rep = 0; rep < 2; rep++) {              // rep 0 = warm-up
        te = tp = tl = 0;
        for (int it = 0; it < iters; it++) {
            const uint32_t *nb = ids + (size_t)(rnd() % 4096) * R;
            const uint8_t *v = vecs + (size_t)(rnd() % 4096) * DIM;
            if (it % 31 == 0) { memset(hash, 0xff, sizeof(uint32_t) * hs); ln = 0; }   // new query every ~31 nodes
            t0 = now();
            { uint32_t s = 0; for (int i = 0; i < DIM; i++) { int d = (int)q[i] - (int)v[i]; s += d * d; } sink += s; }
            te += now() - t0; t0 = now();
            for (int j = 0; j < R; j++) memcpy(agg + j * CH, codes + (size_t)nb[j] * CH, CH);
            memset(dist, 0, sizeof(dist));
            for (int c = 0; c < CH; c++) { const float *tc = table + 256 * c; for (int j = 0; j < R; j++) dist[j] += tc[agg[j * CH + c]]; }
            tp += now() - t0; t0 = now();
            for (int j = 0; j < R; j++) {
                uint32_t id = nb[j]; size_t h = (id * 2654435761u) & (hs - 1); int seen = 0;
                while (hash[h] != 0xffffffffu) { if (hash[h] == id) { seen = 1; break; } h = (h + 1) & (hs - 1); }
                if (seen) continue;
                hash[h] = id;
                if (ln == L && list[ln - 1].d < dist[j]) continue;
                int lo = 0, hi = ln; while (lo < hi) { int m = (lo + hi) >> 1; if (dist[j] < list[m].d) hi = m; else lo = m + 1; }
                memmove(list + lo + 1, list + lo, (ln - lo) * sizeof(Cand)); list[lo].d = dist[j]; list[lo].id = id;
                if (ln < L) ln++;
            }
            tl += now() - t0;
        }
    }
    double ov = 0; t0 = now(); for (int i = 0; i < 1000000; i++) ov += now() - now(); ov = (now() - t0) / 2e6;  // per now() call
    printf("npts=%zu codes=%.2fGB iters=%d  per node (ns): exact %.0f  pq %.0f  list %.0f  total %.0f  (timer overhead ~%.0f ns per part, not subtracted)\n",
           npts, npts * CH / 1e9, iters, te / iters, tp / iters, tl / iters, (te + tp + tl) / iters, ov);
    return (int)sink * 0;
}
