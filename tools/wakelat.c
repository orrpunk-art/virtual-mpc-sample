// wakelat SECONDS [PRIO]: how late does a SCHED_FIFO thread wake from a 1 ms sleep?
// Lab diagnostics for clicks: the app's audio has ~6 ms of headroom, so every wake-up later
// than that is a likely xrun. Prints a histogram of lateness and the worst case.
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static long long ns(struct timespec t) { return t.tv_sec * 1000000000LL + t.tv_nsec; }

int main(int argc, char **argv) {
    int secs = argc > 1 ? atoi(argv[1]) : 60;
    struct sched_param sp = { .sched_priority = argc > 2 ? atoi(argv[2]) : 80 };
    if (sched_setscheduler(0, SCHED_FIFO, &sp)) perror("sched_setscheduler");
    const long long edges[] = {1000000, 3000000, 6000000, 12000000, 25000000, 50000000};  // 1 3 6 12 25 50 ms
    long long count[7] = {0}, worst = 0, n = 0;
    struct timespec next, now;
    clock_gettime(CLOCK_MONOTONIC, &next);
    long long end = ns(next) + secs * 1000000000LL;
    for (;;) {
        next.tv_nsec += 1000000;
        if (next.tv_nsec >= 1000000000) { next.tv_nsec -= 1000000000; next.tv_sec++; }
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
        clock_gettime(CLOCK_MONOTONIC, &now);
        long long late = ns(now) - ns(next);
        int b = 0;
        while (b < 6 && late >= edges[b]) b++;
        count[b]++, n++;
        if (late > worst) worst = late;
        if (late > 1000000) next = now;   // do not try to catch up after a stall
        if (ns(now) >= end) break;
    }
    printf("wakeups=%lld late<1ms=%lld 1-3=%lld 3-6=%lld 6-12=%lld 12-25=%lld 25-50=%lld >50ms=%lld worst=%.1fms\n",
           n, count[0], count[1], count[2], count[3], count[4], count[5], count[6], worst / 1e6);
    return 0;
}
