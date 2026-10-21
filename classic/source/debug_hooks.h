// DirectMemory valve, boot-stage logger & in-process crash/exception handler:
// - sceKernelAllocateDirectMemory / MapDirectMemory / MapNamedDirectMemory are always installed on game processes
//   (when not in bench mode) so our early FG reservation is automatically yielded (Memory Valve) or tail-shaved
//   (Arena Shaving) if a tight game requests nearly 100% of DirectMemory during startup.
// - sceVideoOutOpen / sceVideoOutClose and the FreeBSD amd64 crash signal handler (crash-<TITLE>.txt + boot-<TITLE>.txt)
//   are installed when the app's "Debug log" switch is on (LOG_DIR/debug_log).
// Include after boot_stage.h.
#pragma once

int32_t sceVideoOutOpen(int32_t userId, int32_t busType, int32_t index, const void *param);
int32_t sceVideoOutClose(int32_t handle);

typedef struct {
    uint32_t __bits[4];
} OrbisSigset;

typedef struct {
    void (*sa_handler)(int, void *, void *);
    int sa_flags;
    OrbisSigset sa_mask;
} OrbisSigaction;

int sigaction(int sig, const OrbisSigaction *act, OrbisSigaction *oact);

HOOK_INIT(sceVideoOutOpen);
HOOK_INIT(sceVideoOutClose);
HOOK_INIT(sceKernelAllocateDirectMemory);
HOOK_INIT(sceKernelMapDirectMemory);
HOOK_INIT(sceKernelMapNamedDirectMemory);

static int g_dbg_hooks;
static int g_dm_hooks;
static volatile int g_own_alloc;            // our own requests are logged by alloc_dm / alloc_mbufs themselves
static volatile uint32_t g_dbg_allocs;
static volatile uint32_t g_dbg_maps;
static volatile uint32_t g_dbg_alloc_fail;  // the game's own refused requests (after valve/shaving): one input of the safe-mode check

static long g_shave_phys = 0;
static size_t g_shave_real_len = 0, g_shave_orig_len = 0;
static int g_shave_type = 0, g_shave_tail_mapped = 0, g_mem_tight = 0;

// ---- Lock-free API flight ring buffer (dumped on crash) ----
#define API_RING_N 64
static char g_api_ring[API_RING_N][224];
static volatile uint32_t g_api_ring_w = 0;

static void api_ring_push(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void api_ring_push(const char *fmt, ...)
{
    uint32_t idx = __atomic_fetch_add(&g_api_ring_w, 1u, __ATOMIC_RELAXED);
    char *slot = g_api_ring[idx % API_RING_N];
    uint64_t us = sceKernelGetProcessTime();
    int tid = scePthreadGetthreadid();
    int pfx = snprintf(slot, 224, "#%u %llu.%03llu [t:%d f:%u] ", idx + 1u,
                       (unsigned long long)(us / 1000000ull),
                       (unsigned long long)(us / 1000ull % 1000ull), tid, g_flips);
    if (pfx < 0 || pfx >= 220) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(slot + pfx, (size_t)(224 - pfx), fmt, ap);
    va_end(ap);
}

// ---- Coalesced 1-MB MapDirectMemory run tracker ----
static volatile uint32_t g_map_run_cnt = 0;
static long g_map_run_phys0 = 0, g_map_run_phys_last = 0;
static uintptr_t g_map_run_va0 = 0, g_map_run_va_last = 0;
static int g_map_run_prot = 0, g_map_run_flags = 0;

static void flush_map_run(void)
{
    uint32_t cnt = __atomic_exchange_n(&g_map_run_cnt, 0u, __ATOMIC_ACQ_REL);
    if (cnt > 1) {
        STAGE("  ... coalesced %u contiguous 1MB maps: phys=0x%lx..0x%lx -> va=0x%lx..0x%lx (prot=0x%x flags=0x%x)",
              cnt, g_map_run_phys0, g_map_run_phys_last + 0x100000L,
              (unsigned long)g_map_run_va0, (unsigned long)(g_map_run_va_last + 0x100000u),
              g_map_run_prot, g_map_run_flags);
    }
}

// ---- Tracked FlexibleMemory-backed virtual ranges ----
#define FLEX_VMAP_MAX 64
typedef struct {
    uintptr_t va_start;
    uintptr_t va_end;
    long phys_start;
    int prot;
    int type;
} FlexVmapEntry;
static FlexVmapEntry g_flex_vmap[FLEX_VMAP_MAX];
static volatile uint32_t g_flex_vmap_n = 0;

static void record_flex_vmap(void *va, size_t len, long phys, int prot, int type)
{
    if (!va || !len) return;
    uintptr_t vs = (uintptr_t)va, ve = vs + len;
    uint32_t n = __atomic_load_n(&g_flex_vmap_n, __ATOMIC_ACQUIRE);
    for (uint32_t i = 0; i < n && i < FLEX_VMAP_MAX; i++) {
        if (g_flex_vmap[i].va_start == vs && g_flex_vmap[i].va_end == ve) {
            g_flex_vmap[i].phys_start = phys;
            g_flex_vmap[i].prot = prot;
            g_flex_vmap[i].type = type;
            return;
        }
    }
    uint32_t idx = __atomic_fetch_add(&g_flex_vmap_n, 1u, __ATOMIC_ACQ_REL);
    if (idx < FLEX_VMAP_MAX) {
        g_flex_vmap[idx].va_start = vs;
        g_flex_vmap[idx].va_end = ve;
        g_flex_vmap[idx].phys_start = phys;
        g_flex_vmap[idx].prot = prot;
        g_flex_vmap[idx].type = type;
    }
}

// ---- FreeBSD / OrbisOS amd64 crash signal handler ----
static OrbisSigaction g_old_sa[16];
static const int k_crash_sigs[] = { 4 /*SIGILL*/, 5 /*SIGTRAP*/, 6 /*SIGABRT*/, 8 /*SIGFPE*/, 10 /*SIGBUS*/, 11 /*SIGSEGV*/, 12 /*SIGSYS*/ };

static const char *sig_name_str(int sig)
{
    switch (sig) {
        case 4:  return "SIGILL";
        case 5:  return "SIGTRAP";
        case 6:  return "SIGABRT";
        case 8:  return "SIGFPE";
        case 10: return "SIGBUS";
        case 11: return "SIGSEGV";
        case 12: return "SIGSYS";
        default: return "SIG?";
    }
}

typedef struct {
    char name[64];
    uintptr_t base0, end0;
    uintptr_t base1, end1;
} CrashModEntry;
static CrashModEntry g_crash_mods[64];
static int g_crash_nmods = 0;

static void crash_load_modules(void)
{
    OrbisKernelModule list[64];
    size_t actual = 0;
    if (sceKernelGetModuleList(list, 64, &actual) != 0 || actual == 0) return;
    g_crash_nmods = 0;
    for (size_t i = 0; i < actual && i < 64; i++) {
        OrbisKernelModuleInfo mi;
        memset(&mi, 0, sizeof(mi));
        mi.size = sizeof(mi);
        if (sceKernelGetModuleInfo(list[i], &mi) == 0) {
            int k = g_crash_nmods++;
            snprintf(g_crash_mods[k].name, sizeof(g_crash_mods[k].name), "%s", mi.name);
            g_crash_mods[k].base0 = (uintptr_t)mi.segmentInfo[0].address;
            g_crash_mods[k].end0  = g_crash_mods[k].base0 + mi.segmentInfo[0].size;
            g_crash_mods[k].base1 = (uintptr_t)mi.segmentInfo[1].address;
            g_crash_mods[k].end1  = g_crash_mods[k].base1 + mi.segmentInfo[1].size;
        }
    }
}

static int crash_resolve_addr(uintptr_t addr, char *out, size_t out_sz)
{
    if (addr < 0x10000u) return 0;
    for (int i = 0; i < g_crash_nmods; i++) {
        if (addr >= g_crash_mods[i].base0 && addr < g_crash_mods[i].end0) {
            snprintf(out, out_sz, "%s+0x%lx", g_crash_mods[i].name, (unsigned long)(addr - g_crash_mods[i].base0));
            return 1;
        }
        if (g_crash_mods[i].end1 > g_crash_mods[i].base1 && addr >= g_crash_mods[i].base1 && addr < g_crash_mods[i].end1) {
            snprintf(out, out_sz, "%s[seg1]+0x%lx", g_crash_mods[i].name, (unsigned long)(addr - g_crash_mods[i].base1));
            return 1;
        }
    }
    if (addr >= 0x00400000ul && addr < 0x04000000ul) {
        snprintf(out, out_sz, "eboot.bin+0x%lx", (unsigned long)(addr - 0x00400000ul));
        return 1;
    }
    uint32_t fn = __atomic_load_n(&g_flex_vmap_n, __ATOMIC_ACQUIRE);
    for (uint32_t i = 0; i < fn && i < FLEX_VMAP_MAX; i++) {
        if (addr >= g_flex_vmap[i].va_start && addr < g_flex_vmap[i].va_end) {
            snprintf(out, out_sz, "flex_vmap[%u]+0x%lx (phys=0x%lx)",
                     i, (unsigned long)(addr - g_flex_vmap[i].va_start),
                     g_flex_vmap[i].phys_start + (long)(addr - g_flex_vmap[i].va_start));
            return 1;
        }
    }
    if (addr >= 0x1000000000ul && addr < 0x2500000000ul) {
        snprintf(out, out_sz, "game_arena_va(0x%lx)", (unsigned long)addr);
        return 1;
    }
    return 0;
}

static void crash_write_both(int fd_crash, int fd_boot, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static void crash_write_both(int fd_crash, int fd_boot, const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n <= 0) return;
    if ((size_t)n >= sizeof(buf)) n = (int)sizeof(buf) - 1;
    if (fd_crash >= 0) sceKernelWrite(fd_crash, buf, (size_t)n);
    if (fd_boot >= 0)  sceKernelWrite(fd_boot, buf, (size_t)n);
}

static int crash_addr_readable(uintptr_t addr, size_t len)
{
    if (addr < 0x10000u) return 0;
    OrbisKernelVirtualQueryInfo vq;
    memset(&vq, 0, sizeof(vq));
    if (sceKernelVirtualQuery((const void *)addr, 0, &vq, sizeof(vq)) != 0) return 0;
    if ((uintptr_t)vq.unk02 < addr + len) return 0;
    return (vq.unk04 & 1) != 0;
}

static void crash_sig_handler(int sig, void *info, void *uctx)
{
    if (__atomic_exchange_n(&g_in_crash, 1, __ATOMIC_ACQ_REL) != 0) return;
    flush_map_run();

    char cpath[64], bpath[64], tname[64];
    memset(tname, 0, sizeof(tname));
    scePthreadGetname(scePthreadSelf(), tname);
    int tid = scePthreadGetthreadid();
    uint64_t us = sceKernelGetProcessTime();

    snprintf(cpath, sizeof(cpath), LOG_DIR "/crash-%s.txt", g_title_id);
    snprintf(bpath, sizeof(bpath), LOG_DIR "/boot-%s.txt", g_title_id);
    int fd_c = sceKernelOpen(cpath, 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/, 0666);
    int fd_b = sceKernelOpen(bpath, 0x209 /*O_WRONLY|O_CREAT|O_APPEND*/, 0666);

    crash_load_modules();

    int si_code = info ? *(const int *)((const uint8_t *)info + 8) : 0;
    uintptr_t si_addr = info ? *(const uintptr_t *)((const uint8_t *)info + 0x18) : 0;
    char addr_sym[96] = "?";
    crash_resolve_addr(si_addr, addr_sym, sizeof(addr_sym));

    crash_write_both(fd_c, fd_b,
        "\n=== CRASH %s (sig=%d code=%d) at %llu.%03llus | flips=%u vo_flips=%u in_submit=%d | tid=%d '%s' ===\n"
        "  FAULT_ADDR: si_addr=0x%016lx (%s)\n",
        sig_name_str(sig), sig, si_code,
        (unsigned long long)(us / 1000000ull), (unsigned long long)(us / 1000ull % 1000ull),
        g_flips, g_vo_submit_flips, g_in_submit, tid, tname,
        (unsigned long)si_addr, addr_sym);

    if (si_addr >= 32 && crash_addr_readable(si_addr - 32, 64)) {
        const uint8_t *ip = (const uint8_t *)(si_addr - 32);
        crash_write_both(fd_c, fd_b,
            "  BYTES[si_addr-32..-17]: %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x\n"
            "  BYTES[si_addr-16.. +7]: %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x | <%02x %02x %02x %02x %02x %02x %02x %02x>\n",
            ip[0], ip[1], ip[2], ip[3], ip[4], ip[5], ip[6], ip[7],
            ip[8], ip[9], ip[10], ip[11], ip[12], ip[13], ip[14], ip[15],
            ip[16], ip[17], ip[18], ip[19], ip[20], ip[21], ip[22], ip[23],
            ip[24], ip[25], ip[26], ip[27], ip[28], ip[29], ip[30], ip[31],
            ip[32], ip[33], ip[34], ip[35], ip[36], ip[37], ip[38], ip[39]);
    }

    // Dump all 36 qwords of ucontext_t (verified on PS4 OrbisOS: uc[17]=RBP, uc[28]=RIP, uc[30]=RFLAGS, uc[31]=RSP)
    if (uctx) {
        const uint64_t *uc = (const uint64_t *)uctx;
        crash_write_both(fd_c, fd_b, "  UCONTEXT QWORDS (0..35):\n");
        uintptr_t best_rsp = 0;
        for (int i = 0; i < 36; i++) {
            uintptr_t v = (uintptr_t)uc[i];
            if (!v) continue;
            char sym[96] = "";
            int resolved = crash_resolve_addr(v, sym, sizeof(sym));
            crash_write_both(fd_c, fd_b, "    uc[%2d] (+0x%03x) = 0x%016lx%s%s\n",
                             i, i * 8, (unsigned long)v, resolved ? "  -> " : "", sym);
            if (i >= 9 && i <= 23 && v >= 0x1000000000ul && v < 0x2500000000ul && crash_addr_readable(v - 32, 96)) {
                const uint32_t *mw = (const uint32_t *)((v & ~3ul) - 32);
                crash_write_both(fd_c, fd_b,
                    "      MEM[uc[%d]-32..+60]: %08x %08x %08x %08x %08x %08x %08x %08x | <%08x %08x %08x %08x> %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x\n",
                    i,
                    mw[0], mw[1], mw[2], mw[3], mw[4], mw[5], mw[6], mw[7],
                    mw[8], mw[9], mw[10], mw[11], mw[12], mw[13], mw[14], mw[15],
                    mw[16], mw[17], mw[18], mw[19], mw[20], mw[21], mw[22], mw[23]);
            }
            if (v >= 0x700000000ul && v < 0x800000000ul && (v & 7u) == 0 && crash_addr_readable(v, 128)) {
                if (!best_rsp || i == 31) best_rsp = v;
            }
        }
        if (best_rsp) {
            const uintptr_t *sp = (const uintptr_t *)best_rsp;
            crash_write_both(fd_c, fd_b, "  STACK BACKTRACE (from RSP=0x%lx):\n", (unsigned long)best_rsp);
            int hits = 0;
            for (int i = 0; i < 96 && hits < 24; i++) {
                if (!crash_addr_readable((uintptr_t)&sp[i], 8)) break;
                char sym[96];
                if (crash_resolve_addr(sp[i], sym, sizeof(sym))) {
                    crash_write_both(fd_c, fd_b, "    [rsp+0x%03x] 0x%016lx (%s)\n", i * 8, (unsigned long)sp[i], sym);
                    hits++;
                }
            }
        }
    }

    crash_write_both(fd_c, fd_b, "  LOADED MODULES (%d):\n", g_crash_nmods);
    for (int i = 0; i < g_crash_nmods; i++) {
        crash_write_both(fd_c, fd_b, "    [%2d] %-28s seg0=0x%lx..0x%lx seg1=0x%lx..0x%lx\n",
                         i, g_crash_mods[i].name,
                         (unsigned long)g_crash_mods[i].base0, (unsigned long)g_crash_mods[i].end0,
                         (unsigned long)g_crash_mods[i].base1, (unsigned long)g_crash_mods[i].end1);
    }

    uint32_t rw = __atomic_load_n(&g_api_ring_w, __ATOMIC_ACQUIRE);
    uint32_t rstart = rw > API_RING_N ? rw - API_RING_N : 0;
    crash_write_both(fd_c, fd_b, "  LAST API CALLS (%u total):\n", rw);
    for (uint32_t i = rstart; i < rw; i++) {
        crash_write_both(fd_c, fd_b, "    %s\n", g_api_ring[i % API_RING_N]);
    }
    // Crash-driven self-adaptation: persist automatic workaround in adapt-<TITLE>.ini for the next start.
    if (strncmp(g_title_id, "CUSA", 4) == 0) {
        char apath[64], abuf[160];
        snprintf(apath, sizeof(apath), LOG_DIR "/adapt-%s.ini", g_title_id);
        if (g_flips < INJECT_AFTER_FLIPS && g_vo_submit_flips < INJECT_AFTER_FLIPS) {
            int next_shave = g_cfg.shave_mode;
            int next_reg = g_cfg.reg_mode;
            if (g_shave_orig_len > 0 && g_cfg.shave_mode == 0) {
                next_shave = 1;
            } else if (g_cfg.shave_mode < 2 || g_cfg.reg_mode < 2) {
                next_shave = 2;
                next_reg = 2;
            }
            if (next_shave != g_cfg.shave_mode || next_reg != g_cfg.reg_mode) {
                int an = snprintf(abuf, sizeof(abuf),
                                  "; auto-generated on boot crash\nshave_mode = %d\nreg_mode = %d\npace_order = %d\n",
                                  next_shave, next_reg, g_cfg.pace_order);
                int fd_a = sceKernelOpen(apath, 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/, 0666);
                if (fd_a >= 0 && an > 0) { sceKernelWrite(fd_a, abuf, (size_t)an); sceKernelClose(fd_a); }
                char bmarker[64];
                snprintf(bmarker, sizeof(bmarker), LOG_DIR "/safe-%s.boot", g_title_id);
                sceKernelUnlink(bmarker);
                crash_write_both(fd_c, fd_b,
                    "  SELF-ADAPT: crash during boot -> wrote shave_mode=%d reg_mode=%d to %s for next start\n",
                    next_shave, next_reg, apath);
            }
        } else if ((g_flips >= INJECT_AFTER_FLIPS && g_flips < INJECT_AFTER_FLIPS + 600) ||
                   (g_vo_submit_flips >= INJECT_AFTER_FLIPS && g_vo_submit_flips < INJECT_AFTER_FLIPS + 600)) {
            int next_pace = (g_cfg.pace_order == 2) ? 3 : (g_last_pace_pre ? 2 : 1);
            int an = snprintf(abuf, sizeof(abuf),
                              "; auto-generated on early FG crash (flips=%u)\nshave_mode = %d\nreg_mode = %d\npace_order = %d\n",
                              g_flips, g_cfg.shave_mode, g_cfg.reg_mode, next_pace);
            int fd_a = sceKernelOpen(apath, 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/, 0666);
            if (fd_a >= 0 && an > 0) { sceKernelWrite(fd_a, abuf, (size_t)an); sceKernelClose(fd_a); }
            crash_write_both(fd_c, fd_b,
                "  SELF-ADAPT: crash during early FG (flips=%u, pace_pre=%d) -> wrote pace_order=%d to %s for next start\n",
                g_flips, g_last_pace_pre, next_pace, apath);
        }
    }
    crash_write_both(fd_c, fd_b, "=== END CRASH REPORT ===\n");

    if (fd_c >= 0) sceKernelClose(fd_c);
    if (fd_b >= 0) sceKernelClose(fd_b);

    OrbisSigaction dsa;
    memset(&dsa, 0, sizeof(dsa));
    sigaction(sig, &dsa, NULL);
}

static void crash_handlers_install(void)
{
    for (size_t i = 0; i < sizeof(k_crash_sigs) / sizeof(k_crash_sigs[0]); i++) {
        int sig = k_crash_sigs[i];
        OrbisSigaction sa, osa;
        memset(&sa, 0, sizeof(sa));
        memset(&osa, 0, sizeof(osa));
        sa.sa_handler = crash_sig_handler;
        sa.sa_flags = 0x0040 /*SA_SIGINFO*/ | 0x0010 /*SA_NODEFER*/;
        if (sigaction(sig, &sa, &osa) == 0 && osa.sa_handler != crash_sig_handler)
            g_old_sa[sig] = osa;
    }
}

static int debug_log_on(void)               // raw kernel calls: stdio inside plugin_load faults
{
    int fd = sceKernelOpen(LOG_DIR "/debug_log", 0 /*O_RDONLY*/, 0);
    if (fd < 0) return 0;
    sceKernelClose(fd);
    return 1;
}

static size_t dm_avail_range(long s, long e, size_t align, off_t *start_out)
{
    off_t start = 0; size_t size = 0;
    int r = ((int32_t (*)(off_t, off_t, size_t, off_t *, size_t *))(void *)sceKernelAvailableDirectMemorySize)(
        (off_t)s, (off_t)e, align, &start, &size);
    if (start_out) *start_out = r == 0 ? start : 0;
    return r == 0 ? size : 0;
}

int32_t sceVideoOutOpen_hook(int32_t u, int32_t bus, int32_t idx, const void *p)
{
    flush_map_run();
    int32_t r = HOOK_CONTINUE(sceVideoOutOpen, int32_t (*)(int32_t, int32_t, int32_t, const void *), u, bus, idx, p);
    api_ring_push("VideoOutOpen(u=%d bus=%d idx=%d) = %d", u, bus, idx, r);
    STAGE("game VideoOutOpen(user=%d bus=%d index=%d param=%p) = %d", u, bus, idx, p, r);
    return r;
}
int32_t sceVideoOutClose_hook(int32_t h)
{
    flush_map_run();
    STAGE("game VideoOutClose(handle=%d)", h);
    int32_t r = HOOK_CONTINUE(sceVideoOutClose, int32_t (*)(int32_t), h);
    api_ring_push("VideoOutClose(h=%d) = 0x%08x", h, (unsigned)r);
    STAGE("game VideoOutClose done = 0x%08x", (unsigned)r);
    return r;
}

static void shrink_early_mem_to(size_t keep)
{
    if (!g_pre_mem || g_pre_size <= keep) return;
    size_t drop = g_pre_size - keep;
    long new_phys = g_pre_mem_phys + (long)drop;
    int um = sceKernelMunmap(g_pre_mem, g_pre_size);
    int rr = um == 0 ? sceKernelReleaseDirectMemory(g_pre_mem_phys, drop) : -1;
    void *new_va = NULL;
    g_own_alloc++;
    int mr = (um == 0 && rr == 0) ? sceKernelMapDirectMemory(&new_va, keep, 0x33, 0, new_phys, 0x200000) : -1;
    g_own_alloc--;
    if (mr == 0 && new_va) {
        STAGE("memory valve: shrunk display 0x%zx -> 0x%zx (released phys=0x%lx..0x%lx, kept phys=0x%lx va=%p)",
              g_pre_size, keep, g_pre_mem_phys, new_phys, new_phys, new_va);
        g_pre_mem = new_va;
        g_pre_mem_phys = new_phys;
        g_pre_size = keep;
        g_mem_tight = 1;
    } else {
        if (um == 0) sceKernelReleaseDirectMemory(new_phys, keep);
        g_pre_mem = NULL;
        g_pre_mem_phys = 0;
        g_pre_size = 0;
    }
}

int32_t sceKernelAllocateDirectMemory_hook(long s, long e, size_t len, size_t align, int type, long *phys)
{
    if (!g_own_alloc) flush_map_run();
    int32_t r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
    if (!g_own_alloc && (uint32_t)r == 0x80020023u && g_flips == 0 && g_gpu_state == 0 && g_mbuf_state <= 0) {
        if (g_dump_mem) {
            release_early_dump();
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
        // For multi-arena games (len < 1 GB, e.g. CUSA04529 4th arena 0x2f400000), first shrink g_pre_mem 32 MB -> 8 MB
        // so we minimize any tail shaving needed.
        if ((uint32_t)r == 0x80020023u && len < 0x40000000u && g_pre_mem && g_pre_size > 0x800000u) {
            shrink_early_mem_to(0x800000u);
        }
        // Arena shaving:
        // - Single-arena games (len >= 1 GB, e.g. CUSA17419): shave up to 36 MB while keeping 32 MB g_pre_mem intact.
        // - Multi-arena games (len >= 256 MB, e.g. CUSA04529 0x2f400000): if the arena would consume 100% of remaining
        //   2MB-aligned DirectMemory, reserve 8 MB (0x800000) in DirectMemory for post-arena allocations (Alloc 5).
        if ((uint32_t)r == 0x80020023u && g_cfg.shave_mode == 0 && !g_shave_orig_len && len >= 0x10000000u && align == 0x200000u &&
            (g_pre_mem || g_pre_work)) {
            off_t av_start = 0;
            size_t shave_av = dm_avail_range(s, e, align, &av_start);
            size_t reserve = 0;
            if (len < 0x40000000u && shave_av >= 0x1000000u && shave_av <= len && len - shave_av < 0x800000u) {
                reserve = 0x800000u - (len - shave_av);
                shave_av -= reserve;
            }
            if (shave_av < len && len - shave_av <= 0x2400000u && shave_av >= 0x400000u) {
                int32_t sr = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                                           s, e, shave_av, align, type, phys);
                if (sr == 0 && phys) {
                    g_shave_phys = *phys;
                    g_shave_real_len = shave_av;
                    g_shave_orig_len = len;
                    g_shave_type = type;
                    g_mem_tight = 1;
                    r = 0;
                    STAGE("arena shave OK: game asked len=0x%zx, allocated 0x%zx at phys=0x%lx (shaved 0x%zx, reserve=0x%zx)",
                          len, shave_av, *phys, len - shave_av, reserve);
                }
            }
        }
        // Memory valve fallback: if the allocation still failed, shrink or yield FG buffers step by step.
        if ((uint32_t)r == 0x80020023u && g_pre_mem && g_pre_size > 0x800000u) {
            shrink_early_mem_to(0x800000u);
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
        if ((uint32_t)r == 0x80020023u && g_pre_mem) {
            release_early_mem();
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
        if ((uint32_t)r == 0x80020023u && (g_pre_work || g_pre_gpu)) {
            release_early_work();
            release_early_gpu();
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
    }
    if (!g_own_alloc) {
        if (r != 0) __atomic_add_fetch(&g_dbg_alloc_fail, 1u, __ATOMIC_RELAXED);
        __atomic_add_fetch(&g_dbg_allocs, 1u, __ATOMIC_RELAXED);
        void *caller = __builtin_return_address(0);
        api_ring_push("AllocDM(len=0x%zx al=0x%zx ty=%d) = 0x%08x phys=0x%lx c=%p",
                      len, align, type, (unsigned)r, (r == 0 && phys) ? *phys : 0L, caller);
        STAGE("game AllocateDirectMemory(len=0x%zx align=0x%zx type=%d range=0x%lx..0x%lx) = 0x%08x phys=0x%lx caller=%p",
              len, align, type, s, e, (unsigned)r, (r == 0 && phys) ? *phys : 0L, caller);
    }
    return r;
}

static int32_t call_orig_map(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize,
                             const char *name, int is_named)
{
    if (is_named)
        return HOOK_CONTINUE(sceKernelMapNamedDirectMemory,
                             int32_t (*)(void **, size_t, int, int, long, size_t, const char *),
                             addr, len, prot, flags, dmStart, maxPageSize, name);
    return HOOK_CONTINUE(sceKernelMapDirectMemory,
                         int32_t (*)(void **, size_t, int, int, long, size_t),
                         addr, len, prot, flags, dmStart, maxPageSize);
}

static int32_t handle_game_map(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize,
                               const char *name, int is_named, void *caller)
{
    void *orig_addr_in = addr ? *addr : NULL;
    const char *mname = is_named ? "MapNamedDirectMemory" : "MapDirectMemory";

    if (!g_own_alloc && g_shave_orig_len > 0) {
        long shave_cut = g_shave_phys + (long)g_shave_real_len;
        long shave_end = g_shave_phys + (long)g_shave_orig_len;
        long req_end = dmStart + (long)len;
        if (dmStart >= g_shave_phys && dmStart < shave_cut && req_end > shave_cut && req_end <= shave_end) {
            flush_map_run();
            size_t real_part = (size_t)(shave_cut - dmStart);
            size_t tail_part = len - real_part;
            int32_t r = call_orig_map(addr, real_part, prot, flags, dmStart, maxPageSize, name, is_named);
            if (r == 0 && addr && *addr) {
                g_shave_tail_mapped = 1;
                void *want_va = (uint8_t *)(*addr) + real_part;
                void *got_va = want_va;
                int32_t rf = sceKernelMapFlexibleMemory(&got_va, tail_part, prot, flags | 0x10 /*MAP_FIXED*/);
                if (rf != 0 || got_va != want_va) {
                    if (rf == 0 && got_va) sceKernelMunmap(got_va, tail_part);
                    got_va = want_va;
                    rf = sceKernelMapFlexibleMemory(&got_va, tail_part, prot, flags);
                    if (rf == 0 && got_va != want_va) {
                        sceKernelMunmap(got_va, tail_part);
                        rf = -1;
                    }
                }
                if (rf == 0 && got_va == want_va)
                    record_flex_vmap(want_va, tail_part, shave_cut, prot, g_shave_type);
                api_ring_push("%s(shave-straddle phys=0x%lx len=0x%zx) = 0 va=%p rf=0x%x c=%p",
                              mname, dmStart, len, *addr, (unsigned)rf, caller);
                STAGE("arena shave map OK: va=%p real=0x%zx shaved_tail=0x%zx flex_tail=%s (rf=0x%08x)",
                      *addr, real_part, tail_part, (rf == 0 && got_va == want_va) ? "mapped" : "unmapped-guard", (unsigned)rf);
                STAGE("game %s(phys=0x%lx len=0x%zx prot=0x%x flags=0x%x align=0x%zx in=%p) = 0x00000000 va=%p (shaved) caller=%p",
                      mname, dmStart, len, prot, flags, maxPageSize, orig_addr_in, *addr, caller);
                return 0;
            }
            if (addr) *addr = orig_addr_in;
            if (dmStart == g_shave_phys && g_flips == 0 && g_gpu_state == 0 && g_mbuf_state <= 0) {
                int rr = sceKernelReleaseDirectMemory(g_shave_phys, g_shave_real_len);
                release_early_mem();
                release_early_work();
                long full_phys = 0;
                int ar = HOOK_CONTINUE(sceKernelAllocateDirectMemory,
                                       int32_t (*)(long, long, size_t, size_t, int, long *),
                                       g_shave_phys, shave_end, g_shave_orig_len, 0x200000u, g_shave_type, &full_phys);
                STAGE("arena shave fallback to valve: release_shave=0x%08x realloc_full=0x%08x phys=0x%lx",
                      (unsigned)rr, (unsigned)ar, full_phys);
                g_shave_orig_len = 0;
                g_shave_real_len = 0;
                if (ar == 0)
                    dmStart = full_phys;
            }
        }
    }

    int32_t r = call_orig_map(addr, len, prot, flags, dmStart, maxPageSize, name, is_named);
    if (r != 0 && !g_own_alloc && g_shave_orig_len > 0 && addr) {
        long shave_cut = g_shave_phys + (long)g_shave_real_len;
        long shave_end = g_shave_phys + (long)g_shave_orig_len;
        long req_end = dmStart + (long)len;
        if (dmStart >= shave_cut && req_end <= shave_end) {
            flush_map_run();
            *addr = orig_addr_in;
            int32_t rf = sceKernelMapFlexibleMemory(addr, len, prot, flags);
            if (rf == 0 && *addr) {
                g_shave_tail_mapped = 1;
                record_flex_vmap(*addr, len, dmStart, prot, 0);
                api_ring_push("%s(shave-tail phys=0x%lx len=0x%zx prot=0x%x fl=0x%x in=%p) = 0 va=%p c=%p",
                              mname, dmStart, len, prot, flags, orig_addr_in, *addr, caller);
                STAGE("arena shave tail-only flex map OK: phys=0x%lx len=0x%zx prot=0x%x flags=0x%x in=%p -> va=%p caller=%p",
                      dmStart, len, prot, flags, orig_addr_in, *addr, caller);
                return 0;
            }
            *addr = orig_addr_in;
        }
    }
    if (!g_own_alloc) {
        uintptr_t va_out = (r == 0 && addr && *addr) ? (uintptr_t)(*addr) : 0;
        __atomic_add_fetch(&g_dbg_maps, 1u, __ATOMIC_RELAXED);
        api_ring_push("%s(phys=0x%lx len=0x%zx prot=0x%x fl=0x%x in=%p) = 0x%08x va=0x%lx c=%p",
                      mname, dmStart, len, prot, flags, orig_addr_in, (unsigned)r, (unsigned long)va_out, caller);
        if (r == 0 && len == 0x100000u && !is_named) {
            uint32_t cnt = __atomic_load_n(&g_map_run_cnt, __ATOMIC_ACQUIRE);
            if (cnt > 0 && dmStart == g_map_run_phys_last + 0x100000L && va_out == g_map_run_va_last + 0x100000u &&
                prot == g_map_run_prot && flags == g_map_run_flags) {
                g_map_run_phys_last = dmStart;
                g_map_run_va_last = va_out;
                __atomic_add_fetch(&g_map_run_cnt, 1u, __ATOMIC_RELEASE);
                return r;
            }
            flush_map_run();
            g_map_run_phys0 = g_map_run_phys_last = dmStart;
            g_map_run_va0 = g_map_run_va_last = va_out;
            g_map_run_prot = prot;
            g_map_run_flags = flags;
            __atomic_store_n(&g_map_run_cnt, 1u, __ATOMIC_RELEASE);
        } else {
            flush_map_run();
        }
        STAGE("game %s(phys=0x%lx len=0x%zx prot=0x%x flags=0x%x align=0x%zx in=%p) = 0x%08x va=%p caller=%p",
              mname, dmStart, len, prot, flags, maxPageSize, orig_addr_in, (unsigned)r, (void *)va_out, caller);
    }
    return r;
}

int32_t sceKernelMapDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize)
{
    return handle_game_map(addr, len, prot, flags, dmStart, maxPageSize, NULL, 0, __builtin_return_address(0));
}
int32_t sceKernelMapNamedDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize, const char *name)
{
    return handle_game_map(addr, len, prot, flags, dmStart, maxPageSize, name, 1, __builtin_return_address(0));
}

static void debug_hooks_install(void)
{
    HOOK32(sceKernelAllocateDirectMemory);
    HOOK32(sceKernelMapDirectMemory);
    HOOK32(sceKernelMapNamedDirectMemory);
    g_dm_hooks = 1;
    crash_load_modules();
    crash_handlers_install();
    HOOK32(sceVideoOutOpen);
    HOOK32(sceVideoOutClose);
    g_dbg_hooks = 1;
    STAGE("debug hooks + crash signal handlers installed (always-on logger + Allocate/MapDirectMemory trace)");
}
static void debug_hooks_remove(void)
{
    if (g_dm_hooks) {
        UNHOOK(sceKernelAllocateDirectMemory);
        UNHOOK(sceKernelMapDirectMemory);
        UNHOOK(sceKernelMapNamedDirectMemory);
        g_dm_hooks = 0;
    }
    if (!g_dbg_hooks) return;
    UNHOOK(sceVideoOutOpen);
    UNHOOK(sceVideoOutClose);
    g_dbg_hooks = 0;
}
