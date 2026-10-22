// Crash-safe marks of the early game start (plugin_load, allocations, RegisterBuffers, SetFlipRate, GPU init, first
// submits): LOG_DIR/boot-<TITLE>.txt, rewritten at every game start, always on (also with the debug log off).
// One open/append/close per line with raw kernel calls (stdio inside plugin_load faults in libSceLibcInternal), at
// most BOOT_STAGE_MAX lines, so only the start is covered and frames never pay for it. If the game dies before
// its first frame, the last line is how far the start got (the lg() queue and the flight recorder may not have
// reached the disk). The app's bug report includes these files. Include after LOG_DIR and g_title_id.
#pragma once
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define BOOT_STAGE_MAX 600

static volatile int g_boot_lines = 0;
static volatile uint32_t g_boot_lock = 0;
static volatile int g_in_crash = 0;

static void boot_stage(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void boot_stage(const char *fmt, ...)
{
    if (g_boot_lines >= BOOT_STAGE_MAX || strncmp(g_title_id, "CUSA", 4)) return;
    char buf[260], line[340], path[64];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    uint64_t us = sceKernelGetProcessTime();
    int tid = scePthreadGetthreadid();
    int n = snprintf(line, sizeof(line), "%5llu.%03llu [t:%d f:%u] %s\n", (unsigned long long)(us / 1000000ull),
                     (unsigned long long)(us / 1000ull % 1000ull), tid, g_flips, buf);
    if (n <= 0) return;
    if ((size_t)n >= sizeof(line)) n = (int)sizeof(line) - 1;
    int got_lock = 0;
    if (!g_in_crash) {
        for (int sp = 0; sp < 50000; sp++) {
            if (!__atomic_exchange_n(&g_boot_lock, 1u, __ATOMIC_ACQUIRE)) { got_lock = 1; break; }
        }
    }
    int cur_line = __atomic_fetch_add(&g_boot_lines, 1, __ATOMIC_ACQ_REL);
    if (cur_line >= BOOT_STAGE_MAX) {
        if (got_lock) __atomic_store_n(&g_boot_lock, 0u, __ATOMIC_RELEASE);
        return;
    }
    snprintf(path, sizeof(path), LOG_DIR "/boot-%s.txt", g_title_id);
    if (cur_line == 0) {   // the start before is kept as boot-<TITLE>.prev.txt (testers often start a game 2-3 times)
        char prev[64];
        snprintf(prev, sizeof(prev), LOG_DIR "/boot-%s.prev.txt", g_title_id);
        sceKernelMkdir(LOG_DIR, 0777);
        sceKernelUnlink(prev);
        sceKernelRename(path, prev);
    }
    int fd = sceKernelOpen(path, cur_line == 0 ? 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/ : 0x209 /*O_WRONLY|O_CREAT|O_APPEND*/, 0666);
    if (fd >= 0) {
        sceKernelWrite(fd, line, (size_t)n);
        if (cur_line + 1 == BOOT_STAGE_MAX) sceKernelWrite(fd, "(boot stage limit reached)\n", 27);
        sceKernelClose(fd);
    }
    if (got_lock) __atomic_store_n(&g_boot_lock, 0u, __ATOMIC_RELEASE);
}

#define STAGE(...) do { boot_stage(__VA_ARGS__); BLOG(__VA_ARGS__); } while (0)
