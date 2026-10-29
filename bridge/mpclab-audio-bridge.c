// mpclab-audio-bridge: the rear audio jacks of the virtual MPC Sample.
//
// MPC Sample plays into and records from snd-aloop (the lab's "platform-sound"), 4 ch
// S32_LE 44.1 kHz in 128-frame periods. This bridge is the other end of both loopback pairs:
//   app playback hw:Loopback,0,0 -> reader thread (hw:Loopback,1,0 capture)  -> levels, host
//   app capture  hw:Loopback,0,0 <- feeder thread (hw:Loopback,1,0 playback) <- source
//
// Host audio goes over the virtio-serial port "mpclab.pcm" (a TCP port on the host) as raw
// S16LE stereo 44.1 kHz in both directions; the panel server relays it to the browser, which
// plays it (and records the microphone for AUDIO IN). No QEMU audio device is used: virtio-snd
// returned EIO under load and its blocking writes stalled the guest (measured: 3-6x more app
// stream restarts with it than without).
//
// Why threads, prefill and RT priority: the app's AudioWatchdog restarts both of its linked
// streams whenever its input side has no live partner. So the feeder must never stop, and
// nothing on the host side may block it: all host I/O is non-blocking behind ring buffers.
//
// The panel talks to the bridge over "mpclab.audio": it sends
//   "routing out=on pair=0 in=tone gain=50 tone_hz=440"
// and the bridge answers "levels out <peak per channel...> in <l> <r> drop <n> under <n>" ~10x/s.
// Lab only. Built against the device's alsa-lib (libasound.so.2).
#include <alsa/asoundlib.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define CHUNK 128          // frames per transfer on the loopback pairs (the app's period)
#ifndef HOST_CHUNK
#define HOST_CHUNK 256     // frames per write to the host (5.8 ms): halves virtio kicks
#endif
#define FEED_BUFFER 1024   // app input buffer; FEED_PREFILL frames of silence before start
#define FEED_PREFILL 256
#define HOST_IN_TARGET 512 // frames kept queued from the host microphone (absorbs network jitter)
#define MAXCH 32
#define RT_PRIO 15         // below the app's audio threads (SCHED_RR 20)
#define LINK "/dev/virtio-ports/mpclab.audio"
#define PCM "/dev/virtio-ports/mpclab.pcm"
#define LOOP_STATUS(dir) "/proc/asound/Loopback/pcm0" dir "/sub0/status"

// ------------------------------------------------------------------ shared state
enum { IN_SILENCE, IN_TONE, IN_LOOP, IN_HOST };
struct routing {
    int out;              // 1: app output goes to the host
    unsigned pair;        // first channel of the pair sent to the host (0 main, 2 second mix)
    int in;               // IN_*
    double gain;          // REC GAIN 0..1, applied to the input source
    double tone_hz;
};
static struct routing R = {1, 0, IN_SILENCE, 0.5, 440};
static pthread_mutex_t r_lock = PTHREAD_MUTEX_INITIALIZER;

static struct routing routing_get(void) {
    pthread_mutex_lock(&r_lock);
    struct routing r = R;
    pthread_mutex_unlock(&r_lock);
    return r;
}

// single-producer/single-consumer ring of stereo float frames
struct ring { float buf[16384 * 2]; atomic_uint w, r; };
static struct ring loop_ring, host_ring;   // reader -> feeder (loop cable), host mic -> feeder
#define RING_CAP (sizeof loop_ring.buf / sizeof loop_ring.buf[0] / 2)

static void ring_push(struct ring *q, const float *lr, unsigned n) {
    unsigned w = atomic_load(&q->w);
    for (unsigned i = 0; i < n && w - atomic_load(&q->r) < RING_CAP; i++, w++) {   // full: drop new frames
        q->buf[(w % RING_CAP) * 2] = lr[i * 2];
        q->buf[(w % RING_CAP) * 2 + 1] = lr[i * 2 + 1];
    }
    atomic_store(&q->w, w);   // only the producer writes w, only the consumer writes r
}

static unsigned ring_pop(struct ring *q, float *lr, unsigned n) {   // returns frames copied
    unsigned w = atomic_load(&q->w), r = atomic_load(&q->r), k = 0;
    for (; k < n && r != w; k++, r++) {
        lr[k * 2] = q->buf[(r % RING_CAP) * 2];
        lr[k * 2 + 1] = q->buf[(r % RING_CAP) * 2 + 1];
    }
    atomic_store(&q->r, r);
    return k;
}

static unsigned ring_fill(struct ring *q) { return atomic_load(&q->w) - atomic_load(&q->r); }
static void ring_skip(struct ring *q, unsigned keep) {   // consumer side: drop all but the newest `keep`
    unsigned w = atomic_load(&q->w);
    if (w - atomic_load(&q->r) > keep) atomic_store(&q->r, w - keep);
}

// levels: peaks since the last report, kept as float bits so threads update them atomically
static atomic_uint out_peak[MAXCH], in_peak[2];
static atomic_uint out_ch, drops, unders;
static void peak_max(atomic_uint *p, float v) {
    union { float f; unsigned u; } cur, nv = { .f = v };
    cur.u = atomic_load(p);
    while (v > cur.f && !atomic_compare_exchange_weak(p, &cur.u, nv.u)) {}
}
static float peak_take(atomic_uint *p) { union { float f; unsigned u; } x; x.u = atomic_exchange(p, 0); return x.f; }

// ------------------------------------------------------------------ helpers
static void say(const char *what, int err) { fprintf(stderr, "bridge: %s: %s\n", what, snd_strerror(err)); }

static int running(const char *status_file) {
    char buf[256] = {0};
    FILE *f = fopen(status_file, "r");
    if (!f) return 0;
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    return strstr(buf, "RUNNING") != NULL;
}

static void make_rt(const char *who) {
    struct sched_param sp = { .sched_priority = RT_PRIO };
    int e = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    if (e) fprintf(stderr, "bridge: %s: no realtime priority (%s)\n", who, strerror(e));
}

// Open one end of an aloop pair with exactly the parameters the app's running side imposes
// (while one side of a pair is open the other is held to its format, rate and channels).
static int open_loop_end(snd_pcm_stream_t dir, snd_pcm_t **pcm, unsigned *ch, snd_pcm_format_t *fmt,
                         unsigned *rate, snd_pcm_uframes_t buffer) {
    snd_pcm_hw_params_t *hw;
    snd_pcm_sw_params_t *sw;
    snd_pcm_uframes_t period = CHUNK;
    int err = snd_pcm_open(pcm, "hw:Loopback,1,0", dir, 0);
    if (err < 0) return err;
    snd_pcm_hw_params_alloca(&hw);
    snd_pcm_sw_params_alloca(&sw);
    if ((err = snd_pcm_hw_params_any(*pcm, hw)) < 0 ||
        (err = snd_pcm_hw_params_set_access(*pcm, hw, SND_PCM_ACCESS_RW_INTERLEAVED)) < 0 ||
        (err = snd_pcm_hw_params_set_format_first(*pcm, hw, fmt)) < 0 ||
        (err = snd_pcm_hw_params_set_channels_first(*pcm, hw, ch)) < 0 ||
        (err = snd_pcm_hw_params_set_rate_first(*pcm, hw, rate, NULL)) < 0 ||
        (err = snd_pcm_hw_params_set_period_size_near(*pcm, hw, &period, NULL)) < 0 ||
        (err = snd_pcm_hw_params_set_buffer_size_near(*pcm, hw, &buffer)) < 0 ||
        (err = snd_pcm_hw_params(*pcm, hw)) < 0 ||
        (err = snd_pcm_sw_params_current(*pcm, sw)) < 0 ||
        // playback starts only once the prefill is queued; capture on the first read
        (err = snd_pcm_sw_params_set_start_threshold(*pcm, sw, dir == SND_PCM_STREAM_PLAYBACK ? FEED_PREFILL : 1)) < 0 ||
        (err = snd_pcm_sw_params(*pcm, sw)) < 0) {
        snd_pcm_close(*pcm);
        *pcm = NULL;
        return err;
    }
    return 0;
}

// S16/S32 little-endian (what aloop carries for this app); other widths read as 0
static float get(const char *buf, size_t idx, size_t width) {
    return width == 4 ? ((const int32_t *)buf)[idx] / 2147483648.0f : width == 2 ? ((const int16_t *)buf)[idx] / 32768.0f : 0;
}
static void put(char *buf, size_t idx, size_t width, float v) {
    if (v > 1) v = 1;
    if (v < -1) v = -1;
    if (width == 4) ((int32_t *)buf)[idx] = (int32_t)(v * 2147483647.0);
    else if (width == 2) ((int16_t *)buf)[idx] = (int16_t)(v * 32767.0f);
}
static int16_t s16(float v) { return (int16_t)(v > 1 ? 32767 : v < -1 ? -32767 : v * 32767.0f); }

// ------------------------------------------------------------------ host PCM port
static atomic_int pcm_fd = -1;

static int pcm_port(void) {   // opened lazily; the port exists only when QEMU was given it
    int fd = atomic_load(&pcm_fd);
    if (fd < 0) {
        fd = open(PCM, O_RDWR | O_NONBLOCK | O_CLOEXEC);
        int expected = -1;
        if (fd >= 0 && !atomic_compare_exchange_strong(&pcm_fd, &expected, fd)) { close(fd); fd = expected; }
    }
    return fd;
}

// ------------------------------------------------------------------ reader: app output
static void *reader(void *arg) {
    (void)arg;
    make_rt("reader");
    static char raw[CHUNK * MAXCH * 4];
    float lp[CHUNK * 2];
    int16_t host[HOST_CHUNK * 2];
    unsigned hn = 0;
    for (;;) {
        while (!running(LOOP_STATUS("p"))) usleep(200000);
        snd_pcm_t *cap;
        unsigned ch = 0, rate = 0;
        snd_pcm_format_t fmt;
        int err = open_loop_end(SND_PCM_STREAM_CAPTURE, &cap, &ch, &fmt, &rate, 2048);
        if (err < 0) { say("loop capture open", err); sleep(1); continue; }
        size_t w = (size_t)snd_pcm_format_physical_width(fmt) / 8;
        if (ch < 2 || ch > MAXCH || (w != 2 && w != 4)) {
            fprintf(stderr, "bridge: unexpected app output %u ch, %zu bytes/sample\n", ch, w);
            snd_pcm_close(cap); sleep(1); continue;
        }
        atomic_store(&out_ch, ch);
        fprintf(stderr, "bridge: app output %u ch %s %u Hz\n", ch, snd_pcm_format_name(fmt), rate);
        for (;;) {
            snd_pcm_sframes_t n = snd_pcm_readi(cap, raw, CHUNK);
            if (n == -EPIPE) { snd_pcm_prepare(cap); continue; }
            if (n < 0) { say("loop capture", (int)n); break; }
            struct routing r = routing_get();
            unsigned pair = r.pair + 1 < ch ? r.pair : 0;
            for (snd_pcm_sframes_t i = 0; i < n; i++) {
                for (unsigned c = 0; c < ch; c++) peak_max(&out_peak[c], fabsf(get(raw, i * ch + c, w)));
                host[hn * 2] = r.out ? s16(get(raw, i * ch + pair, w)) : 0;
                host[hn * 2 + 1] = r.out ? s16(get(raw, i * ch + pair + 1, w)) : 0;
                lp[i * 2] = get(raw, i * ch, w);          // loop cable carries main out L/R
                lp[i * 2 + 1] = get(raw, i * ch + 1, w);
                if (++hn == HOST_CHUNK) {                 // never blocks: a full port drops the chunk
                    int fd = pcm_port();
                    if (fd >= 0 && write(fd, host, sizeof host) != (ssize_t)sizeof host) atomic_fetch_add(&drops, 1);
                    hn = 0;
                }
            }
            ring_push(&loop_ring, lp, (unsigned)n);
        }
        snd_pcm_close(cap);
        sleep(1);
    }
    return NULL;
}

// ------------------------------------------------------------------ host input (microphone)
static void *host_in(void *arg) {
    (void)arg;
    static int16_t buf[1024 * 2];
    static float lr[1024 * 2];
    size_t have = 0;   // bytes of a partial frame carried over
    for (;;) {
        int fd = pcm_port();
        if (fd < 0) { sleep(2); continue; }
        struct pollfd p = { fd, POLLIN, 0 };
        if (poll(&p, 1, 1000) <= 0) continue;
        ssize_t k = read(fd, (char *)buf + have, sizeof buf - have);
        if (k <= 0) { if (k < 0 && errno != EAGAIN) usleep(100000); continue; }
        size_t bytes = have + (size_t)k, frames = bytes / 4;
        for (size_t i = 0; i < frames * 2; i++) lr[i] = buf[i] / 32768.0f;
        ring_push(&host_ring, lr, (unsigned)frames);
        have = bytes - frames * 4;
        memmove(buf, (char *)buf + frames * 4, have);
    }
    return NULL;
}

// ------------------------------------------------------------------ feeder: app input
static void *feeder(void *arg) {
    (void)arg;
    make_rt("feeder");
    static char raw[CHUNK * MAXCH * 4];
    float lr[CHUNK * 2];
    double phase = 0;
    for (;;) {
        while (!running(LOOP_STATUS("c"))) usleep(200000);
        snd_pcm_t *feed;
        unsigned ch = 0, rate = 0;
        snd_pcm_format_t fmt;
        int err = open_loop_end(SND_PCM_STREAM_PLAYBACK, &feed, &ch, &fmt, &rate, FEED_BUFFER);
        if (err < 0) { say("loop playback open", err); sleep(1); continue; }
        size_t w = (size_t)snd_pcm_format_physical_width(fmt) / 8;
        if (ch < 1 || ch > MAXCH || (w != 2 && w != 4)) { snd_pcm_close(feed); sleep(1); continue; }
        fprintf(stderr, "bridge: app input %u ch %s\n", ch, snd_pcm_format_name(fmt));
        // prefill: the stream starts with FEED_PREFILL frames queued, so a late wake-up of
        // this thread never leaves the app's input without data
        memset(raw, 0, sizeof raw);
        for (int i = 0; i < FEED_PREFILL / CHUNK; i++) snd_pcm_writei(feed, raw, CHUNK);
        int host_primed = 0;
        for (;;) {
            struct routing r = routing_get();
            unsigned got = 0;
            if (r.in == IN_LOOP) got = ring_pop(&loop_ring, lr, CHUNK);
            else ring_skip(&loop_ring, 0);                // keep the loop ring fresh for later
            if (r.in == IN_HOST) {
                // the browser's clock drifts against the guest's: keep about HOST_IN_TARGET
                // frames queued, wait for that much after an underrun, drop the excess
                unsigned fill = ring_fill(&host_ring);
                if (fill > 4 * HOST_IN_TARGET) ring_skip(&host_ring, HOST_IN_TARGET);
                if (!host_primed && fill >= HOST_IN_TARGET) host_primed = 1;
                if (host_primed && (got = ring_pop(&host_ring, lr, CHUNK)) < CHUNK) host_primed = 0;
            } else ring_skip(&host_ring, 0);
            memset(raw, 0, (size_t)CHUNK * ch * w);
            for (unsigned i = 0; i < CHUNK; i++) {
                for (unsigned c = 0; c < 2 && c < ch; c++) {
                    float v = r.in == IN_TONE ? 0.5f * (float)sin(phase) : i < got ? lr[i * 2 + c] : 0;
                    v *= (float)r.gain;
                    put(raw, i * ch + c, w, v);
                    peak_max(&in_peak[c], fabsf(v));
                }
                phase += 2 * M_PI * r.tone_hz / rate;
                if (phase > 2 * M_PI) phase -= 2 * M_PI;
            }
            snd_pcm_sframes_t wr = snd_pcm_writei(feed, raw, CHUNK);   // paced by the aloop clock
            if (wr == -EPIPE) {
                atomic_fetch_add(&unders, 1);
                snd_pcm_prepare(feed);
                memset(raw, 0, sizeof raw);
                for (int i = 0; i < FEED_PREFILL / CHUNK; i++) snd_pcm_writei(feed, raw, CHUNK);
            } else if (wr < 0) { say("loop playback", (int)wr); break; }   // app closed its input
        }
        snd_pcm_close(feed);
        sleep(1);
    }
    return NULL;
}

// ------------------------------------------------------------------ panel link (main thread)
static int link_fd = -1;

static void link_poll(void) {
    static char line[256];
    static size_t len;
    if (link_fd < 0 && (link_fd = open(LINK, O_RDWR | O_NONBLOCK | O_CLOEXEC)) < 0) { usleep(100000); return; }
    struct pollfd p = { link_fd, POLLIN, 0 };
    if (poll(&p, 1, 100) <= 0) return;   // also paces the levels report
    char buf[256];
    ssize_t n = read(link_fd, buf, sizeof buf);
    if (n == 0 || (n < 0 && errno != EAGAIN)) { usleep(100000); return; }
    for (ssize_t i = 0; i < n; i++) {
        if (buf[i] != '\n') { if (len < sizeof line - 1) line[len++] = buf[i]; continue; }
        line[len] = 0, len = 0;
        if (strncmp(line, "routing ", 8)) continue;
        struct routing r = routing_get();
        char *save, *tok = strtok_r(line + 8, " ", &save);
        for (; tok; tok = strtok_r(NULL, " ", &save)) {
            char *v = strchr(tok, '=');
            if (!v) continue;
            *v++ = 0;
            if (!strcmp(tok, "out")) r.out = !strcmp(v, "on");
            else if (!strcmp(tok, "pair")) r.pair = (unsigned)atoi(v);
            else if (!strcmp(tok, "in")) r.in = !strcmp(v, "tone") ? IN_TONE : !strcmp(v, "loop") ? IN_LOOP : !strcmp(v, "host") ? IN_HOST : IN_SILENCE;
            else if (!strcmp(tok, "gain")) r.gain = atof(v) / 100.0;
            else if (!strcmp(tok, "tone_hz")) r.tone_hz = atof(v);
        }
        pthread_mutex_lock(&r_lock);
        R = r;
        pthread_mutex_unlock(&r_lock);
        fprintf(stderr, "bridge: routing out=%d pair=%u in=%d gain=%.2f tone=%.0f Hz\n", r.out, r.pair, r.in, r.gain, r.tone_hz);
    }
}

static void link_send(const char *msg) {
    if (link_fd >= 0 && write(link_fd, msg, strlen(msg)) < 0 && errno != EAGAIN) { close(link_fd); link_fd = -1; }
}

int main(void) {
    if (mlockall(MCL_CURRENT | MCL_FUTURE)) perror("bridge: mlockall");   // no page faults in the RT threads
    pthread_t t1, t2, t3;
    pthread_create(&t1, NULL, reader, NULL);
    pthread_create(&t2, NULL, feeder, NULL);
    pthread_create(&t3, NULL, host_in, NULL);
    struct timespec last = {0}, now;
    for (;;) {
        link_poll();
        clock_gettime(CLOCK_MONOTONIC, &now);
        if ((now.tv_sec - last.tv_sec) * 1000 + (now.tv_nsec - last.tv_nsec) / 1000000 < 100) continue;
        last = now;
        char msg[96 + MAXCH * 8];
        unsigned ch = atomic_load(&out_ch);
        int k = snprintf(msg, sizeof msg, "levels out");
        for (unsigned c = 0; c < ch && c < MAXCH; c++) k += snprintf(msg + k, sizeof msg - k, " %.4f", peak_take(&out_peak[c]));
        snprintf(msg + k, sizeof msg - k, " in %.4f %.4f drop %u under %u\n", peak_take(&in_peak[0]), peak_take(&in_peak[1]),
                 atomic_load(&drops), atomic_load(&unders));
        if (ch) link_send(msg);
    }
}
