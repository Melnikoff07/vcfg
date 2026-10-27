// VCFG flight recorder: always on (also with the debug log off), tiny, per title.
// LOG_DIR/trace-<TITLE>.txt = header + the first TRACE_HEAD (200) log lines of this game start (load crashes happen
// there) + the last TRACE_TAIL lines. Rewritten at most every 2 s and right away while the head fills up (lite mode:
// the new lines written in place, see g_tr_lite); the
// previous game start is kept as trace-<TITLE>.prev.txt. The app's bug report reads both, so a crash can be
// understood from the report alone. Include after LOG_DIR, g_title_id and klog are defined; call vcfg_trace()
// from lg_write (the thread that may write files).
#pragma once
#include <stdio.h>
#include <string.h>

#define VCFG_TSTR2(x) #x
#define VCFG_TSTR(x) VCFG_TSTR2(x)
#ifdef VCFG_VER
#define VCFG_TRACE_VERSION VCFG_TSTR(VCFG_VER)
#else
#define VCFG_TRACE_VERSION "dev"
#endif
#ifndef VCFG_ENGINE
#define VCFG_ENGINE "unknown"
#endif
#define TRACE_HEAD 200
#define TRACE_TAIL 200
#define TRACE_W 200

static char g_tr_head[TRACE_HEAD][TRACE_W], g_tr_tail[TRACE_TAIL][TRACE_W];
static unsigned g_tr_nhead, g_tr_ntail, g_tr_total;
static uint64_t g_tr_started, g_tr_last_us;
static char g_tr_path[96];
// Lite mode (classic: LOG_DIR/tracelite_<TITLE>; off by default and in the other engine): the new lines are written over
// the end marker of the file in place, the header's two counters are rewritten in place (fixed-width fields), and the
// whole file is rewritten only when the appended tail reaches TRACE_TAIL lines. Same content for the readers: header,
// the first lines, the last lines with the skipped count, the end marker last.
static int g_tr_lite = 0;
static unsigned g_tr_in_file;          // lite: lines of this game start in the file (0: next flush rewrites it whole)
static unsigned g_tr_appended;         // lite: tail lines appended since the last whole rewrite
static long g_tr_hdr_off, g_tr_end_off; // lite: offset of "log lines:" and of the end marker
static int g_tr_file_lite;             // the file on disk has the fixed-width header (written whole in lite mode)
static unsigned g_tr_flushes;          // flushes (whole or in place) and bytes written, for the CPU plugin line
static uint64_t g_tr_bytes;

int32_t sceKernelGetSystemSwVersion(OrbisKernelSwVersion *version);

static const char *vcfg_trace_line(unsigned i)   // i-th line of this game start, if still held
{
    if (i < g_tr_nhead) return g_tr_head[i];
    unsigned j = i - TRACE_HEAD;
    if (i < TRACE_HEAD || j >= g_tr_ntail || g_tr_ntail - j > TRACE_TAIL) return NULL;
    return g_tr_tail[j % TRACE_TAIL];
}
static void vcfg_trace_counts(FILE *f)
{
    if (g_tr_lite) fprintf(f, "log lines: %-10u\nseconds since start: %-10llu\n", g_tr_total,
                           (unsigned long long)((sceKernelGetProcessTime() - g_tr_started) / 1000000ull));
    else fprintf(f, "log lines: %u\nseconds since start: %llu\n", g_tr_total,
                 (unsigned long long)((sceKernelGetProcessTime() - g_tr_started) / 1000000ull));
}

static void vcfg_trace_flush(void)
{
    FILE *f = fopen(g_tr_path, "w");
    if (!f) return;
    OrbisKernelSwVersion sw; memset(&sw, 0, sizeof(sw)); sw.Size = sizeof(sw);
    int swr = sceKernelGetSystemSwVersion(&sw);
    fprintf(f, "VCFG trace\nversion: %s\nengine: %s\ntitle: %s\nfirmware: %s\n",
            VCFG_TRACE_VERSION, VCFG_ENGINE, g_title_id, swr == 0 ? sw.VersionString : "?");
    g_tr_hdr_off = ftell(f);
    vcfg_trace_counts(f);
    fprintf(f, "---- first lines ----\n");
    for (unsigned i = 0; i < g_tr_nhead; i++) fputs(g_tr_head[i], f);
    if (g_tr_total > TRACE_HEAD) {
        unsigned n = g_tr_ntail < TRACE_TAIL ? g_tr_ntail : TRACE_TAIL, from = g_tr_ntail - n;
        fprintf(f, "---- last lines (%u skipped before) ----\n", g_tr_total - TRACE_HEAD - n);
        for (unsigned i = from; i < g_tr_ntail; i++) fputs(g_tr_tail[i % TRACE_TAIL], f);
    }
    g_tr_end_off = ftell(f);
    fprintf(f, "---- end of trace ----\n");
    g_tr_flushes++;
    long sz = ftell(f);
    if (sz > 0) g_tr_bytes += (uint64_t)sz;
    fclose(f);
    g_tr_in_file = g_tr_total;
    g_tr_appended = 0;
    g_tr_file_lite = g_tr_lite;
}

// Lite: the lines added since the last flush, over the end marker; whole rewrite when that is not possible.
static void vcfg_trace_flush_lite(void)
{
    unsigned add = g_tr_total - g_tr_in_file;
    if (!g_tr_in_file || !g_tr_file_lite || g_tr_hdr_off <= 0 || g_tr_end_off <= 0 || add > TRACE_TAIL ||
        g_tr_appended + add > TRACE_TAIL || !vcfg_trace_line(g_tr_in_file)) {
        vcfg_trace_flush();
        return;
    }
    FILE *f = fopen(g_tr_path, "r+");
    if (!f) { vcfg_trace_flush(); return; }
    long w0 = 0;
    if (fseek(f, g_tr_hdr_off, SEEK_SET) == 0) vcfg_trace_counts(f);
    if (fseek(f, g_tr_end_off, SEEK_SET) != 0) { fclose(f); vcfg_trace_flush(); return; }
    w0 = g_tr_end_off;
    for (unsigned i = g_tr_in_file; i < g_tr_total; i++) {
        if (i == TRACE_HEAD) fprintf(f, "---- last lines (0 skipped before) ----\n");   // the head just filled up
        const char *l = vcfg_trace_line(i);
        if (l) fputs(l, f);
        if (i >= TRACE_HEAD) g_tr_appended++;
    }
    g_tr_end_off = ftell(f);
    fprintf(f, "---- end of trace ----\n");
    g_tr_flushes++;
    long sz = ftell(f);
    if (sz > w0) g_tr_bytes += (uint64_t)(sz - w0) + 40u;   // + the two header counters
    fclose(f);
    g_tr_in_file = g_tr_total;
}

static void vcfg_trace(const char *line)
{
    if (strncmp(g_title_id, "CUSA", 4)) return;      // not a game process (or title not known yet)
    uint64_t now = sceKernelGetProcessTime();
    if (!g_tr_path[0]) {
        char prev[96];
        snprintf(g_tr_path, sizeof(g_tr_path), LOG_DIR "/trace-%s.txt", g_title_id);
        snprintf(prev, sizeof(prev), LOG_DIR "/trace-%s.prev.txt", g_title_id);
        sceKernelMkdir(LOG_DIR, 0777);
        remove(prev); rename(g_tr_path, prev);
        g_tr_started = now;
    }
    char t[TRACE_W];
    snprintf(t, sizeof(t), "%7.2f %s", (double)(now - g_tr_started) / 1e6, line);
    size_t l = strlen(t); if (!l || t[l - 1] != '\n') { if (l >= TRACE_W - 1) l = TRACE_W - 2; t[l] = '\n'; t[l + 1] = 0; }
    g_tr_total++;
    if (g_tr_nhead < TRACE_HEAD) memcpy(g_tr_head[g_tr_nhead++], t, TRACE_W);
    else memcpy(g_tr_tail[g_tr_ntail++ % TRACE_TAIL], t, TRACE_W);
    int urgent = strstr(line, "FAIL") || strstr(line, "fail") || strstr(line, "stop") || strstr(line, "inert") ||
                 strstr(line, "crash") || strstr(line, "overflow");      // reasons are written out right away
    if (g_tr_nhead < TRACE_HEAD || urgent || now - g_tr_last_us > 2000000ull) {
        g_tr_last_us = now;
        if (g_tr_lite) vcfg_trace_flush_lite();
        else vcfg_trace_flush();
    }
}
