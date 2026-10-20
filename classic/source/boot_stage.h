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

#define BOOT_STAGE_MAX 200

static int g_boot_lines = 0;

static void boot_stage(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void boot_stage(const char *fmt, ...)
{
    if (g_boot_lines >= BOOT_STAGE_MAX || strncmp(g_title_id, "CUSA", 4)) return;
    char buf[240], line[300], path[64];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    uint64_t us = sceKernelGetProcessTime();
    int n = snprintf(line, sizeof(line), "%5llu.%03llu %s\n", (unsigned long long)(us / 1000000ull),
                     (unsigned long long)(us / 1000ull % 1000ull), buf);
    if (n <= 0) return;
    if ((size_t)n >= sizeof(line)) n = (int)sizeof(line) - 1;
    snprintf(path, sizeof(path), LOG_DIR "/boot-%s.txt", g_title_id);
    if (g_boot_lines == 0) {   // the start before is kept as boot-<TITLE>.prev.txt (testers often start a game 2-3 times)
        char prev[64];
        snprintf(prev, sizeof(prev), LOG_DIR "/boot-%s.prev.txt", g_title_id);
        sceKernelMkdir(LOG_DIR, 0777);
        sceKernelUnlink(prev);
        sceKernelRename(path, prev);
    }
    int fd = sceKernelOpen(path, g_boot_lines == 0 ? 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/ : 0x209 /*O_WRONLY|O_CREAT|O_APPEND*/, 0666);
    if (fd < 0) { g_boot_lines = BOOT_STAGE_MAX; return; }
    sceKernelWrite(fd, line, (size_t)n);
    if (++g_boot_lines == BOOT_STAGE_MAX) sceKernelWrite(fd, "(boot stage limit reached)\n", 27);
    sceKernelClose(fd);
}

#define STAGE(...) do { boot_stage(__VA_ARGS__); BLOG(__VA_ARGS__); } while (0)
