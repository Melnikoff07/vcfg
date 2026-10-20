// VideoOut-flip trace (research for games that show frames through sceVideoOutSubmitFlip, "INERT: VOUT FLIP API").
// Only with the app's Debug log on and only for titles that are not verified. Records the order of the game's
// Gnm submits, SubmitDone and VideoOut flips on every thread for a short window and writes it once, in one file,
// LOG_DIR/voutrace-<TITLE>.txt, from the game's flip thread. No frame is changed. Include after boot_stage.h.
#pragma once

#define VT_N 320                 // events kept
#define VT_START_FLIP 150        // recording starts at this VideoOut flip (the game is past loading and the 120-frame check)
#define VT_DW 20                 // dwords kept per command buffer: head 6 + tail 14

enum { VT_SUBMIT = 1, VT_FLIP = 2, VT_DONE = 3, VT_SUBMIT_FLIP = 4 };
typedef struct { uint64_t t; uint32_t kind, tid, a, b, c, d; uint32_t dw[VT_DW]; } VtEv;

static VtEv g_vt[VT_N];
static volatile uint32_t g_vt_n, g_vt_on, g_vt_dumped;
static int g_vt_hooked;

int32_t sceGnmSubmitDone(void);
HOOK_INIT(sceGnmSubmitDone);

static void vt_rec(uint32_t kind, uint32_t a, uint32_t b, uint32_t c, uint32_t d, const uint32_t *dcb, uint32_t dcb_bytes)
{
    if (!g_vt_on) return;
    uint32_t i = __atomic_fetch_add(&g_vt_n, 1u, __ATOMIC_RELAXED);
    if (i >= VT_N) return;
    VtEv *e = &g_vt[i];
    e->t = sceKernelGetProcessTime(); e->kind = kind; e->tid = (uint32_t)(uintptr_t)scePthreadSelf();
    e->a = a; e->b = b; e->c = c; e->d = d;
    memset(e->dw, 0, sizeof(e->dw));
    uint32_t nd = dcb_bytes / 4;
    if (dcb && nd >= 1 && nd < (4u << 20) && dcb_mem_ok((uintptr_t)dcb, dcb_bytes)) {
        for (uint32_t k = 0; k < 6 && k < nd; k++) e->dw[k] = dcb[k];
        for (uint32_t k = 0; k < 14 && k < nd; k++) e->dw[6 + k] = dcb[nd - 1 - k];   // tail, last dword first
    }
}

static void vt_write(const char *path, const char *s, size_t n, int first)
{
    int fd = sceKernelOpen(path, first ? 0x601 : 0x209, 0666);
    if (fd < 0) return;
    sceKernelWrite(fd, s, n);
    sceKernelClose(fd);
}

static void vt_dump(void)
{
    if (__atomic_exchange_n(&g_vt_dumped, 1u, __ATOMIC_ACQ_REL)) return;
    g_vt_on = 0;
    uint32_t n = g_vt_n < VT_N ? g_vt_n : VT_N;
    char path[64], line[420];
    snprintf(path, sizeof(path), LOG_DIR "/voutrace-%s.txt", g_title_id);
    int l = snprintf(line, sizeof(line),
                     "VOUT trace %s: %u events. t_us tid kind a b c d | dcb head(6) tail(14, last dword first)\n"
                     "SUBMIT a=count b=dcb bytes c=ccb bytes d=dcb ptr | FLIP a=index b=mode c=arg d=handle | DONE | SUBMIT_FLIP a=index\n",
                     g_title_id, n);
    vt_write(path, line, (size_t)l, 1);
    uint64_t t0 = n ? g_vt[0].t : 0;
    for (uint32_t i = 0; i < n; i++) {
        VtEv *e = &g_vt[i];
        const char *k = e->kind == VT_SUBMIT ? "SUBMIT" : e->kind == VT_FLIP ? "FLIP" : e->kind == VT_DONE ? "DONE" : "SUBMIT_FLIP";
        l = snprintf(line, sizeof(line), "%8llu %08x %-6s %u %u %u %08x |", (unsigned long long)(e->t - t0), e->tid, k, e->a, e->b, e->c, e->d);
        for (int j = 0; j < VT_DW && l < (int)sizeof(line) - 12; j++) l += snprintf(line + l, sizeof(line) - (size_t)l, " %08x", e->dw[j]);
        l += snprintf(line + l, sizeof(line) - (size_t)l, "\n");
        vt_write(path, line, (size_t)l, 0);
    }
    STAGE("voutrace written: %u events -> %s", n, path);
}

int32_t sceGnmSubmitDone_hook(void)
{
    vt_rec(VT_DONE, 0, 0, 0, 0, NULL, 0);
    return HOOK_CONTINUE(sceGnmSubmitDone, int32_t (*)(void));
}
