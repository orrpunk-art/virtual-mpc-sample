// iolat FILE [N] [BS]: O_DIRECT random reads, prints p50/p90/max latency in ms. Lab diagnostics.
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
static int cmp(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }
int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: iolat FILE [N] [BS]\n"); return 2; }
    int n = argc > 2 ? atoi(argv[2]) : 200, bs = argc > 3 ? atoi(argv[3]) : 4096;
    int fd = open(argv[1], O_RDONLY | O_DIRECT);
    if (fd < 0) { perror("open"); return 1; }
    struct stat st; fstat(fd, &st);
    long blocks = st.st_size / bs;
    if (blocks < 1) { fprintf(stderr, "file too small\n"); return 1; }
    void *buf; if (posix_memalign(&buf, 4096, bs)) return 1;
    double *t = malloc(sizeof *t * n);
    srand(1);
    for (int i = 0; i < n; i++) {
        struct timespec a, b;
        clock_gettime(CLOCK_MONOTONIC, &a);
        if (pread(fd, buf, bs, (off_t)(rand() % blocks) * bs) < 0) { perror("pread"); return 1; }
        clock_gettime(CLOCK_MONOTONIC, &b);
        t[i] = (b.tv_sec - a.tv_sec) * 1e3 + (b.tv_nsec - a.tv_nsec) / 1e6;
    }
    qsort(t, n, sizeof *t, cmp);
    printf("%d x %d B O_DIRECT: p50 %.3f ms  p90 %.3f ms  max %.3f ms\n", n, bs, t[n / 2], t[n * 9 / 10], t[n - 1]);
    return 0;
}
