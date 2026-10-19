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
#else
#define g_bench_enabled 0
#define g_bench_offscreen 0
#define bench_io_tick() ((void)0)
#endif
