#pragma once
#ifndef VCFG_BENCH
#define VCFG_BENCH 0
#endif
#if VCFG_BENCH && VCFG_2BUF_TEST
#error benchmark and presentation experiments must be separate builds
#endif
#if VCFG_BENCH
#include "bench_shaders.h"
#include "bench_build.h"
static int g_bench_enabled, g_bench_offscreen;
static uint32_t g_bench_pack_off;
static void bench_io_tick(void);
// crash-safe boot trace: open/append/close per line, independent of the lg() queue and the debug_log switch
static void bench_blog(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void bench_blog(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    // raw kernel calls only: stdio fopen() inside plugin_load faults in libSceLibcInternal (klog backtrace of run c)
    char line[300];
    int n = snprintf(line, sizeof(line), "%llu %s\n", (unsigned long long)sceKernelGetProcessTimeCounter(), buf);
    if (n <= 0) return;
    int fd = sceKernelOpen(LOG_DIR "/bench_boot.txt", 0x209 /*O_WRONLY|O_CREAT|O_APPEND*/, 0666);
    if (fd < 0) return;
    sceKernelWrite(fd, line, (size_t)n);
    sceKernelClose(fd);
}
#define BLOG(...) bench_blog(__VA_ARGS__)
#else
#define g_bench_enabled 0
#define g_bench_offscreen 0
#define bench_io_tick() ((void)0)
#define BLOG(...) ((void)0)
#endif
