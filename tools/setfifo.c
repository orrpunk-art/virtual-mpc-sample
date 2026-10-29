// setfifo PRIO COMM-PREFIX: set every thread whose comm starts with COMM-PREFIX to SCHED_FIFO PRIO.
// Lab diagnostics (the stock rootfs has no chrt).
#include <dirent.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: setfifo PRIO COMM-PREFIX\n"); return 2; }
    struct sched_param sp = { .sched_priority = atoi(argv[1]) };
    DIR *d = opendir("/proc");
    struct dirent *e;
    int n = 0;
    while ((e = readdir(d))) {
        char path[64], comm[64] = "";
        snprintf(path, sizeof path, "/proc/%s/comm", e->d_name);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        if (fgets(comm, sizeof comm, f) && !strncmp(comm, argv[2], strlen(argv[2]))) {
            int pid = atoi(e->d_name);
            if (sched_setscheduler(pid, sp.sched_priority ? SCHED_FIFO : SCHED_OTHER, &sp) == 0) n++;
            else perror(comm);
        }
        fclose(f);
    }
    printf("%d threads set\n", n);
    return 0;
}
