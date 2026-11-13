// LD_PRELOAD shim that shortens one sleep of Godot's Dummy audio driver.
// See tools/replay_check/README.md, section "Audio faster than real time".
//
// Godot 3.5's AudioDriverDummy::thread_func (servers/audio/
// audio_driver_dummy.cpp) mixes one buffer, then sleeps the full play time
// of that buffer through OS_Unix::delay_usec -> nanosleep. That fixed sleep
// caps the mixer below real time, so a tool that paces audio frame by frame
// (AudioServer.lock) cannot run faster than real time.
//
// A nanosleep request of exactly AUDIO_SLEEP_NS (the driver's buffer time)
// becomes a sleep of AUDIO_SLEEP_NEW_NS. Every other sleep is unchanged.
// The new sleep must not be zero: the mixer must release its lock long
// enough for the tool's AudioServer.lock() to take it. Measured in the
// container with lockstep pacing over 20 s of game time: 1 ms gave 7.4x real
// time, exact sample count, and a lead of at most 1,023 samples; 0 let the
// mixer run 34 s ahead.
//
// Without AUDIO_SLEEP_NS the shim changes nothing.

#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <time.h>

static int (*real_nanosleep)(const struct timespec *, struct timespec *);
static long match_ns = -1;
static long new_ns;

int nanosleep(const struct timespec *req, struct timespec *rem) {
    if (!real_nanosleep) {
        real_nanosleep = dlsym(RTLD_NEXT, "nanosleep");
    }
    if (match_ns < 0) {
        const char *m = getenv("AUDIO_SLEEP_NS");
        const char *n = getenv("AUDIO_SLEEP_NEW_NS");
        match_ns = m ? atol(m) : 0;
        new_ns = n ? atol(n) : 1000000;
    }
    if (match_ns > 0 && req->tv_sec == 0 && req->tv_nsec == match_ns) {
        struct timespec shorter = {0, new_ns};
        return real_nanosleep(&shorter, rem);
    }
    return real_nanosleep(req, rem);
}
