// Greedy graph-aware packing of a DiskANN disk index into flash pages of P nodes (default 40 = 16 KiB of 4 sectors).
// Seeds are taken in node-id order; a page is filled breadth-first from its seed with not-yet-placed neighbours.
// Output: old_to_new slot map (uint32 header n, 1, then n entries), same format as the block-shuffle maps.
// Build: gcc -O2 -o page_pack page_pack.c ; run: ./page_pack <disk.index> <out_old_to_new.bin> [P]
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>

#define SECTOR 4096
#define NODE 388
#define NPS 10

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s disk.index out.bin [P]\n", argv[0]); return 1; }
    int P = argc > 3 ? atoi(argv[3]) : 40;
    int fd = open(argv[1], O_RDONLY);
    struct stat st; fstat(fd, &st);
    const uint8_t *m = mmap(NULL, st.st_size, PROT_READ, MAP_SHARED, fd, 0);
    if (m == MAP_FAILED) { perror("mmap"); return 1; }
    uint64_t n = ((const uint64_t *)m)[1];
    uint32_t *slot = malloc(n * sizeof(uint32_t));
    memset(slot, 0xff, n * sizeof(uint32_t));
    uint32_t *page = malloc(P * sizeof(uint32_t));
    uint64_t next = 0, full_pages = 0, pages = 0;
    time_t t0 = time(NULL);
    for (uint64_t s = 0; s < n; s++) {
        if (slot[s] != 0xffffffffu) continue;
        int cnt = 0, head = 0;
        page[cnt++] = (uint32_t)s; slot[s] = (uint32_t)next++;
        while (head < cnt && cnt < P) {
            uint32_t v = page[head++];
            const uint8_t *b = m + (uint64_t)SECTOR * (1 + v / NPS) + (uint64_t)NODE * (v % NPS);
            uint32_t nn; memcpy(&nn, b + 128, 4);
            const uint32_t *nb = (const uint32_t *)(b + 132);
            for (uint32_t k = 0; k < nn && cnt < P; k++) {
                uint32_t u = nb[k];
                if (u < n && slot[u] == 0xffffffffu) { slot[u] = (uint32_t)next++; page[cnt++] = u; }
            }
        }
        // pad the page so the next page starts on a P boundary
        if (getenv("PAD")) next = (next + P - 1) / P * P;
        pages++; if (cnt == P) full_pages++;
        if ((s & ((1u << 24) - 1)) == 0 && s)
            fprintf(stderr, "%lu/%lu seeds, %lu pages (%.1f%% full), %lds\n", s, n, pages, 100.0 * full_pages / pages,
                    (long)(time(NULL) - t0));
    }
    FILE *fo = fopen(argv[2], "wb");
    uint32_t hdr[2] = {(uint32_t)n, 1};
    fwrite(hdr, 4, 2, fo); fwrite(slot, 4, n, fo); fclose(fo);
    fprintf(stderr, "done: %lu pages, %.1f%% full, slots used %lu (n=%lu), %lds\n", pages, 100.0 * full_pages / pages,
            next, n, (long)(time(NULL) - t0));
    return 0;
}
