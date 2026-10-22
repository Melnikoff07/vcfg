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
HOOK_INIT(sceKernelAvailableDirectMemorySize);
HOOK_INIT(sceKernelMapDirectMemory);
HOOK_INIT(sceKernelMapNamedDirectMemory);

static int g_dbg_hooks;
static int g_dm_hooks;
static volatile int g_own_alloc;            // our own requests are logged by alloc_dm / alloc_mbufs themselves
static volatile uint32_t g_dbg_allocs;
static volatile uint32_t g_dbg_maps;
static volatile uint32_t g_dbg_alloc_fail;  // the game's own refused requests (after valve/shaving): one input of the safe-mode check

#define SHAVE_MAX 512
static volatile uint32_t g_dbg_releases __attribute__((unused));
static volatile uint32_t g_dbg_batch_maps __attribute__((unused));
static volatile uint32_t g_dbg_batch_fail __attribute__((unused));
typedef struct {
    long phys;
    size_t real_len;
    size_t orig_len;
    int type;
} ShaveEntry;
static ShaveEntry g_shaves[SHAVE_MAX];
static int g_shave_n = 0;
static size_t g_syn_phys_cursor = 0;

static long g_shave_phys = 0;
static size_t g_shave_real_len = 0, g_shave_orig_len = 0;
static int g_shave_type = 0, g_shave_tail_mapped = 0, g_mem_tight = 0;
static long g_shave_last_probe_s = -1, g_shave_last_probe_e = -1;
static size_t g_shave_last_probe_len = 0;
static int g_shave_last_probe_tid = -1;

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
#define FLEX_VMAP_MAX 512
typedef struct {
    uintptr_t va_start;
    uintptr_t va_end;
    long phys_start;
    int prot;
    int type;
    uintptr_t arena_va_start;
    uintptr_t arena_va_end;
    long arena_phys_start;
} FlexVmapEntry;
static FlexVmapEntry g_flex_vmap[FLEX_VMAP_MAX];
static volatile uint32_t g_flex_vmap_n = 0;

static void record_flex_vmap_ex(void *va, size_t len, long phys, int prot, int type,
                                uintptr_t arena_vs, uintptr_t arena_ve, long arena_phys)
{
    if (!va || !len) return;
    uintptr_t vs = (uintptr_t)va, ve = vs + len;
    if (!arena_vs || !arena_ve) { arena_vs = vs; arena_ve = ve; arena_phys = phys; }
    uint32_t n = __atomic_load_n(&g_flex_vmap_n, __ATOMIC_ACQUIRE);
    for (uint32_t i = 0; i < n && i < FLEX_VMAP_MAX; i++) {
        if (g_flex_vmap[i].va_start == vs && g_flex_vmap[i].va_end == ve) {
            g_flex_vmap[i].phys_start = phys;
            g_flex_vmap[i].prot = prot;
            g_flex_vmap[i].type = type;
            g_flex_vmap[i].arena_va_start = arena_vs;
            g_flex_vmap[i].arena_va_end = arena_ve;
            g_flex_vmap[i].arena_phys_start = arena_phys;
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
        g_flex_vmap[idx].arena_va_start = arena_vs;
        g_flex_vmap[idx].arena_va_end = arena_ve;
        g_flex_vmap[idx].arena_phys_start = arena_phys;
    }
}

static void record_flex_vmap(void *va, size_t len, long phys, int prot, int type)
{
    record_flex_vmap_ex(va, len, phys, prot, type, (uintptr_t)va, (uintptr_t)va + len, phys);
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
static CrashModEntry g_crash_mods[256];
static int g_crash_nmods = 0;

static void crash_load_modules(void)
{
    OrbisKernelModule list[256];
    size_t actual = 0;
    if (sceKernelGetModuleList(list, 256, &actual) != 0 || actual == 0) return;
    g_crash_nmods = 0;
    for (size_t i = 0; i < actual && i < 256; i++) {
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

static size_t
dm_avail_range(long s, long e, size_t align, off_t *start_out);
static size_t total_shaved_bytes(void);

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
        "\n=== CRASH %s (sig=%d code=%d) at %llu.%03llus | flips=%u vo_flips=%u in_submit=%d alloc_fail=%u shave=%d tight=%d | tid=%d '%s' ===\n"
        "  FAULT_ADDR: si_addr=0x%016lx (%s)\n",
        sig_name_str(sig), sig, si_code,
        (unsigned long long)(us / 1000000ull), (unsigned long long)(us / 1000ull % 1000ull),
        g_flips, g_vo_submit_flips, vcfg_in_submit_get(), g_dbg_alloc_fail, g_shave_n, g_mem_tight, tid, tname,
        (unsigned long)si_addr, addr_sym);

    {
        OrbisKernelVirtualQueryInfo vq_si;
        memset(&vq_si, 0, sizeof(vq_si));
        if (si_addr && sceKernelVirtualQuery((const void *)si_addr, 0, &vq_si, sizeof(vq_si)) == 0) {
            crash_write_both(fd_c, fd_b,
                "  VM_QUERY(si_addr): va=0x%lx..0x%lx prot=0x%x is_flex=%d is_dm=%d name='%s'\n",
                (unsigned long)(uintptr_t)vq_si.unk01, (unsigned long)(uintptr_t)vq_si.unk02,
                (unsigned)vq_si.unk04,
                (int)vq_si.isFlexibleMemory, (int)vq_si.isDirectMemory, vq_si.name);
        } else {
            crash_write_both(fd_c, fd_b, "  VM_QUERY(si_addr): UNMAPPED (0x%016lx)\n", (unsigned long)si_addr);
        }
        if (uctx) {
            uintptr_t rip_val = (uintptr_t)((const uint64_t *)uctx)[28];
            OrbisKernelVirtualQueryInfo vq_rip;
            memset(&vq_rip, 0, sizeof(vq_rip));
            if (rip_val && sceKernelVirtualQuery((const void *)rip_val, 0, &vq_rip, sizeof(vq_rip)) == 0) {
                crash_write_both(fd_c, fd_b,
                    "  VM_QUERY(RIP):     va=0x%lx..0x%lx prot=0x%x is_flex=%d is_dm=%d name='%s'\n",
                    (unsigned long)(uintptr_t)vq_rip.unk01, (unsigned long)(uintptr_t)vq_rip.unk02,
                    (unsigned)vq_rip.unk04,
                    (int)vq_rip.isFlexibleMemory, (int)vq_rip.isDirectMemory, vq_rip.name);
            }
        }
        size_t dm_tot = sceKernelGetDirectMemorySize();
        size_t dm_2m = dm_avail_range(0, (long)dm_tot, 0x200000u, NULL);
        size_t dm_4k = dm_avail_range(0, (long)dm_tot, 0x4000u, NULL);
        crash_write_both(fd_c, fd_b,
            "  MEM_STATE: dm_total=0x%zx free_2mb=0x%zx free_16kb=0x%zx | pre_mem=%p(phys=0x%lx sz=0x%zx) pre_work=%p(phys=0x%lx sz=0x%zx) pre_gpu=%p(phys=0x%lx sz=0x%zx)\n"
            "  API_COUNTS: allocs=%u(fail=%u) maps=%u releases=%u batch_maps=%u(fail=%u) shaves=%d(shaved_bytes=0x%zx)\n"
            "  FG_STATE: res=%ux%u pitch=%u fmt=0x%x(sup=%d) ngame=%d nmid=%d mbase=%d nbuf=%d mbuf_state=%d gpu_state=%d pcopy=%d/%d safe=%d/%d\n"
            "  CFG_STATE: fg=%d mode=%d preset=%d shave_mode=%d reg_mode=%d pace_order=%d pcopy_cfg=%d two_buf=%d gv=%d\n",
            dm_tot, dm_2m, dm_4k,
            g_pre_mem, g_pre_mem_phys, g_pre_size,
            g_pre_work, g_pre_work_phys, g_pre_work_sz,
            g_pre_gpu, g_pre_gpu_phys, (size_t)g_gpu_sz,
            g_dbg_allocs, g_dbg_alloc_fail, g_dbg_maps, g_dbg_releases, g_dbg_batch_maps, g_dbg_batch_fail,
            g_shave_n, total_shaved_bytes(),
            g_bw, g_bh, g_pitch, g_attr.format, g_fmt_supported,
            g_ngame, g_nmid, g_mbase, g_nbuf, g_mbuf_state, g_gpu_state, g_pcopy_avail, g_pcopy_on, g_safe, g_safe_active,
            g_cfg.framegen, g_cfg.mode, g_cfg.preset, g_cfg.shave_mode, g_cfg.reg_mode, g_cfg.pace_order,
            g_cfg.present_copy, g_cfg.two_buffer, g_cfg.game_vectors);
    }

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
        if ((g_flips < INJECT_AFTER_FLIPS && g_vo_submit_flips < INJECT_AFTER_FLIPS) ||
            (g_dbg_alloc_fail > 0 && (g_cfg.shave_mode < 2 || g_cfg.reg_mode < 2))) {
            int next_shave = g_cfg.shave_mode;
            int next_reg = g_cfg.reg_mode;
            if (g_cfg.shave_mode == 2 && g_flips < INJECT_AFTER_FLIPS && g_vo_submit_flips < INJECT_AFTER_FLIPS) {
                next_shave = 1;
                next_reg = 2;
            } else if (g_cfg.shave_mode < 2 || g_cfg.reg_mode < 2) {
                next_shave = 2;
                next_reg = 2;
            }
            if (next_shave != g_cfg.shave_mode || next_reg != g_cfg.reg_mode) {
                int an = snprintf(abuf, sizeof(abuf),
                                  "; auto-generated on boot/alloc crash\nshave_mode = %d\nreg_mode = %d\npace_order = %d\n",
                                  next_shave, next_reg, g_cfg.pace_order);
                int fd_a = sceKernelOpen(apath, 0x601 /*O_WRONLY|O_CREAT|O_TRUNC*/, 0666);
                if (fd_a >= 0 && an > 0) { sceKernelWrite(fd_a, abuf, (size_t)an); sceKernelClose(fd_a); }
                char bmarker[64];
                snprintf(bmarker, sizeof(bmarker), LOG_DIR "/safe-%s.boot", g_title_id);
                sceKernelUnlink(bmarker);
                crash_write_both(fd_c, fd_b,
                    "  SELF-ADAPT: crash (flips=%u alloc_fail=%u) -> wrote shave_mode=%d reg_mode=%d to %s for next start\n",
                    g_flips, g_dbg_alloc_fail, next_shave, next_reg, apath);
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
    int r;
    if (g_dm_hooks) {
        r = HOOK_CONTINUE(sceKernelAvailableDirectMemorySize,
                          int32_t (*)(off_t, off_t, size_t, off_t *, size_t *),
                          (off_t)s, (off_t)e, align, &start, &size);
    } else {
        r = ((int32_t (*)(off_t, off_t, size_t, off_t *, size_t *))(void *)sceKernelAvailableDirectMemorySize)(
            (off_t)s, (off_t)e, align, &start, &size);
    }
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
    // If g_pre_gpu or g_pre_work was packed into the 2 MB page immediately below the old g_pre_mem_phys,
    // release it when shrinking g_pre_mem from the low end so it never stays stranded as an island in the
    // middle of DirectMemory (gpu_init() will reallocate or fall back to FlexibleMemory at frame 120).
    if (g_gpu_state == 0 && g_pre_mem_phys >= 0x200000L) {
        long sub_lo = g_pre_mem_phys - 0x200000L;
        if (g_pre_gpu && g_pre_gpu_phys >= sub_lo && g_pre_gpu_phys < g_pre_mem_phys)
            release_early_gpu();
        if (g_pre_work && g_pre_work_phys >= sub_lo && g_pre_work_phys < g_pre_mem_phys)
            release_early_work();
    }
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

static size_t total_shaved_bytes(void)
{
    size_t sum = 0;
    for (int i = 0; i < g_shave_n; i++) {
        if (g_shaves[i].orig_len > g_shaves[i].real_len)
            sum += g_shaves[i].orig_len - g_shaves[i].real_len;
    }
    return sum;
}

static void record_shave_entry(long phys, size_t real_len, size_t orig_len, int type)
{
    g_shave_phys = phys;
    g_shave_real_len = real_len;
    g_shave_orig_len = orig_len;
    g_shave_type = type;
    for (int i = 0; i < g_shave_n; i++) {
        if (g_shaves[i].phys == phys && g_shaves[i].orig_len == orig_len) {
            g_shaves[i].real_len = real_len;
            g_shaves[i].type = type;
            return;
        }
    }
    for (int i = 0; i < g_shave_n; i++) {
        if (g_shaves[i].orig_len == 0) {
            g_shaves[i].phys = phys;
            g_shaves[i].real_len = real_len;
            g_shaves[i].orig_len = orig_len;
            g_shaves[i].type = type;
            return;
        }
    }
    if (g_shave_n < SHAVE_MAX) {
        g_shaves[g_shave_n].phys = phys;
        g_shaves[g_shave_n].real_len = real_len;
        g_shaves[g_shave_n].orig_len = orig_len;
        g_shaves[g_shave_n].type = type;
        g_shave_n++;
    }
}

// Relocate or trim g_pre_mem out of a range-constrained [avoid_s, avoid_e) request when free 2MB-aligned DirectMemory
// exists outside that range, preserving our scanout buffer without blocking the game's fixed-range request.
static int relocate_early_mem_outside(long avoid_s, long avoid_e)
{
    if (!g_pre_mem || !g_pre_size) return 0;
    long pm_s = g_pre_mem_phys;
    long pm_e = g_pre_mem_phys + (long)g_pre_size;
    if (pm_e <= avoid_s || pm_s >= avoid_e) return 0;

    // If [avoid_s, avoid_e) only overlaps the upper tail of g_pre_mem (e.g. CUSA08809/CUSA08034 requesting top 2.125 MB
    // 0x127de0000..0x128000000), trim the overlapping tail in-place so g_pre_mem keeps its non-overlapping prefix
    // without moving in physical or virtual memory.
    if (pm_s < avoid_s) {
        long trim_end = avoid_s & ~(long)0x1FFFFF;
        if (trim_end > pm_s && (size_t)(trim_end - pm_s) >= 0x800000u) {
            size_t keep_prefix = ((size_t)(trim_end - pm_s)) & ~(size_t)0x7FFFFFu;
            if (keep_prefix < 0x800000u) keep_prefix = 0x800000u;
            if (keep_prefix < g_pre_size) {
                size_t rel_sz = g_pre_size - keep_prefix;
                long rel_phys = pm_s + (long)keep_prefix;
                int um = sceKernelMunmap((uint8_t *)g_pre_mem + keep_prefix, rel_sz);
                int rr = (um == 0) ? sceKernelReleaseDirectMemory(rel_phys, rel_sz) : -1;
                if (um == 0 && rr == 0) {
                    STAGE("memory valve: trimmed overlapping tail of g_pre_mem in-place 0x%zx->0x%zx (kept phys=0x%lx..0x%lx, freed 0x%lx..0x%lx for 0x%lx..0x%lx)",
                          g_pre_size, keep_prefix, pm_s, rel_phys, rel_phys, pm_e, avoid_s, avoid_e);
                    g_pre_size = keep_prefix;
                    if (keep_prefix < 0x2000000u) g_mem_tight = 1;
                    return 1;
                }
            }
        }
    }

    size_t dm_sz = sceKernelGetDirectMemorySize();
    size_t keep = g_pre_size > 0x800000u ? 0x800000u : g_pre_size;
    long cand_ranges[3][2] = {
        { avoid_e, (long)dm_sz },
        { avoid_s >= (long)keep ? avoid_s - (long)keep : 0L, avoid_s },
        { 0L, avoid_s }
    };
    for (int ci = 0; ci < 3; ci++) {
        long rs = cand_ranges[ci][0], re = cand_ranges[ci][1];
        if (re <= rs || (size_t)(re - rs) < keep) continue;
        long new_phys = 0;
        void *new_va = NULL;
        g_own_alloc++;
        int ar = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                               rs, re, keep, 0x200000u, 3 /*WC_GARLIC*/, &new_phys);
        int mr = (ar == 0) ? HOOK_CONTINUE(sceKernelMapDirectMemory, int32_t (*)(void **, size_t, int, int, long, size_t),
                                           &new_va, keep, 0x33, 0, new_phys, 0x200000u) : -1;
        g_own_alloc--;
        if (ar == 0 && mr == 0 && new_va) {
            memset(new_va, 0, keep);
            int um = sceKernelMunmap(g_pre_mem, g_pre_size);
            if (um == 0) sceKernelReleaseDirectMemory(g_pre_mem_phys, g_pre_size);
            STAGE("memory valve: relocated display 0x%zx->0x%zx from phys=0x%lx to phys=0x%lx va=%p (outside 0x%lx..0x%lx)",
                  g_pre_size, keep, g_pre_mem_phys, new_phys, new_va, avoid_s, avoid_e);
            g_pre_mem = new_va;
            g_pre_mem_phys = new_phys;
            g_pre_size = keep;
            if (keep < 0x2000000u) g_mem_tight = 1;
            return 1;
        }
        if (ar == 0) sceKernelReleaseDirectMemory(new_phys, keep);
    }
    return 0;
}

// In shave_mode=2 (LATE ALLOC), plugin_load skipped early DirectMemory allocations. Right when the game's allocations
// are about to consume the final 8 MB of DirectMemory, claim 8 MB at the top of the remaining free range so our
// 1 mid display buffer is preserved and Arena Shaving can back the game's final 8 MB tail with FlexibleMemory.
static void claim_late_pre_mem_8mb(long s, long e)
{
    if (g_pre_mem || g_gpu_state != 0 || g_mbuf_state > 0) return;
    size_t keep = 0x800000u;
    off_t av_start = 0;
    size_t av_sz = dm_avail_range(s, e, 0x200000u, &av_start);
    if (av_sz < keep) return;
    long top_s = (long)av_start + (long)(av_sz - keep);
    long top_e = (long)av_start + (long)av_sz;
    long phys = 0;
    void *va = NULL;
    g_own_alloc++;
    int ar = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                           top_s, top_e, keep, 0x200000u, 3 /*WC_GARLIC*/, &phys);
    if (ar != 0)
        ar = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                           s, e, keep, 0x200000u, 3 /*WC_GARLIC*/, &phys);
    int mr = (ar == 0) ? HOOK_CONTINUE(sceKernelMapDirectMemory, int32_t (*)(void **, size_t, int, int, long, size_t),
                                       &va, keep, 0x33, 0, phys, 0x200000u) : -1;
    g_own_alloc--;
    if (ar == 0 && mr == 0 && va) {
        memset(va, 0, keep);
        g_pre_mem = va;
        g_pre_mem_phys = phys;
        g_pre_size = keep;
        g_mem_tight = 1;
        STAGE("late reserve (shave_mode=2): claimed 8 MB display buffer at phys=0x%lx va=%p before pool exhaustion", phys, va);
    } else if (ar == 0) {
        sceKernelReleaseDirectMemory(phys, keep);
    }
}

int32_t sceKernelAvailableDirectMemorySize_hook(off_t s, off_t e, size_t align, off_t *startOut, size_t *sizeOut)
{
    int32_t r = HOOK_CONTINUE(sceKernelAvailableDirectMemorySize,
                              int32_t (*)(off_t, off_t, size_t, off_t *, size_t *),
                              s, e, align, startOut, sizeOut);
    if (!g_own_alloc && !g_safe_never && !g_fg_off && r == 0 && sizeOut &&
        g_gpu_state == 0 && g_mbuf_state <= 0) {
        // If g_pre_mem is not yet held (e.g. shave_mode=2 LATE ALLOC) and the game queries the remaining pool,
        // reserve 8 MB (0x800000) in the reported size when < 8 MB is free outside [s, e) so the game leaves 8 MB for 1 mid buffer.
        if (!g_pre_mem && g_cfg.shave_mode != 1 && *sizeOut >= 0x1000000u) {
            size_t dm_sz = sceKernelGetDirectMemorySize();
            size_t out_lo = (s > 0) ? dm_avail_range(0, (long)s, 0x200000u, NULL) : 0;
            size_t out_hi = ((size_t)e < dm_sz) ? dm_avail_range((long)e, (long)dm_sz, 0x200000u, NULL) : 0;
            if (out_lo + out_hi < 0x800000u) {
                size_t keep = (align > 0x800000u) ? align : 0x800000u;
                if (*sizeOut > keep) {
                    *sizeOut -= keep;
                    g_mem_tight = 1;
                    STAGE("AvailableDirectMemorySize reserve: reported 0x%zx (reserved 0x%zx for 1 mid buffer in range 0x%lx..0x%lx)",
                          *sizeOut, keep, (long)s, (long)e);
                }
            }
        }
    }
    return r;
}

int32_t sceKernelAllocateDirectMemory_hook(long s, long e, size_t len, size_t align, int type, long *phys)
{
    if (!g_own_alloc) flush_map_run();
    size_t eff_align = align ? align : 0x4000u;

    // In shave_mode=2 (LATE ALLOC), if this allocation would leave < 8 MB of 2MB-aligned DirectMemory in the pool,
    // claim our 8 MB g_pre_mem at the top of the free range first so Arena Shaving preserves 1 mid buffer.
    if (!g_own_alloc && !g_safe_never && !g_fg_off && !g_pre_mem && g_cfg.shave_mode == 2 &&
        g_gpu_state == 0 && g_mbuf_state <= 0 &&
        len >= 0x400000u && total_shaved_bytes() < 0x800000u) {
        size_t pool_av2 = dm_avail_range(0, (long)sceKernelGetDirectMemorySize(), 0x200000u, NULL);
        size_t rng_av = dm_avail_range(s, e, eff_align, NULL);
        if (rng_av >= len && pool_av2 >= 0x800000u && pool_av2 < len + 0x800000u) {
            claim_late_pre_mem_8mb(s, e);
        }
    }

    int32_t r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
    if (!g_own_alloc && !g_safe_never && (uint32_t)r == 0x80020023u) {
        int boot_phase = (g_flips == 0 && g_gpu_state == 0);
        int pre_gpu_phase = (g_gpu_state == 0);
        if (pre_gpu_phase && g_dump_mem) {
            release_early_dump();
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
        // Step 1: Yield DirectMemory-backed g_pre_work and g_pre_gpu FIRST when their physical range overlaps [s, e)
        // and freeing them can actually contribute to satisfying the request (if g_pre_gpu/g_pre_work sit inside the
        // already-partially-occupied sub-2MB page below g_low_sub2mb_end and len >= 0x200000 || eff_align >= 0x200000,
        // releasing them can never create a >= 2MB block).
        // Both g_gpu and g_work can fall back to FlexibleMemory (alloc_flex_gpu with prot 0x33), consuming 0 bytes of
        // DirectMemory, whereas display buffers (g_pre_mem) MUST reside in DirectMemory for sceVideoOutRegisterBuffers.
        if (pre_gpu_phase && (uint32_t)r == 0x80020023u && ((g_pre_work && g_pre_work_phys > 0) || (g_pre_gpu && g_pre_gpu_phys > 0))) {
            int rel_any = 0;
            int cant_use_sub2mb = (eff_align >= 0x200000u || len >= 0x200000u);
            if (g_pre_work && g_pre_work_phys > 0 && g_pre_work_phys < e && g_pre_work_phys + (long)g_pre_work_sz > s &&
                !(cant_use_sub2mb && g_pre_work_phys < g_low_sub2mb_end)) {
                release_early_work();
                rel_any = 1;
            }
            if (g_pre_gpu && g_pre_gpu_phys > 0 && g_pre_gpu_phys < e && g_pre_gpu_phys + (long)g_gpu_sz > s &&
                !(cant_use_sub2mb && g_pre_gpu_phys < g_low_sub2mb_end)) {
                release_early_gpu();
                rel_any = 1;
            }
            if (rel_any) {
                r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                                  s, e, len, align, type, phys);
            }
        }
        // Step 2: If the game requested a specific physical subrange [s, e) that overlaps g_pre_mem while other
        // DirectMemory outside [s, e) is still free, relocate g_pre_mem outside [s, e) and retry.
        if (pre_gpu_phase && (uint32_t)r == 0x80020023u && g_mbuf_state <= 0 && g_pre_mem && relocate_early_mem_outside(s, e)) {
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
        // Step 3: For sub-1GB allocations (multi-arena games like CUSA04529/CUSA10249/CUSA07820/CUSA00341 4th arena,
        // or sequential chunk allocators), shrink g_pre_mem 32 MB -> 8 MB and retry unshaved FIRST so the arena
        // succeeds 100% in real DirectMemory whenever it fits (consuming 0 bytes of FlexibleMemory).
        if (pre_gpu_phase && (uint32_t)r == 0x80020023u && g_mbuf_state <= 0 && len < 0x40000000u && g_pre_mem && g_pre_size > 0x800000u) {
            shrink_early_mem_to(0x800000u);
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
        // Step 4: Multi-block, sequential-chunk & runtime streaming Arena Shaving:
        // - Single-arena games (len >= 1 GB, e.g. CUSA17419): shave up to 36 MB while keeping 32 MB g_pre_mem intact.
        // - Multi-arena games (len >= 64 MB, e.g. CUSA13323/CUSA11456 8th arena 0x4000000): shave up to 32 MB and map
        //   the shaved tail via MapDirectMemory / MapDirectMemory2 / BatchMap / BatchMap2.
        // - Post-arena or runtime streaming chunks when 0 bytes of unallocated DirectMemory remain outside our scanout
        //   buffers (e.g. CUSA10249/CUSA07820/CUSA04529 6 MB Alloc 5, or CUSA32946/CUSA06548/CUSA10237/CUSA11260/
        //   CUSA10123/CUSA00010/CUSA02819/CUSA09311/CUSA18534): synthesize a flex-backed chunk (>= 16 KB).
        size_t min_shave_len = (g_mbuf_state == 1 || !boot_phase || g_dbg_allocs >= 64u) ? 0x4000u : 0x400000u;
        int non_2mb_giant_bundle = (len >= 0x10000000u && (len & 0x1FFFFFu) != 0);
        int cur_tid = scePthreadGetthreadid();
        int is_desc_probe = (cur_tid == g_shave_last_probe_tid &&
                             g_shave_last_probe_s == s &&
                             g_shave_last_probe_e == e &&
                             g_shave_last_probe_len > len);
        if ((uint32_t)r == 0x80020023u && !non_2mb_giant_bundle &&
            g_cfg.shave_mode != 1 && len >= min_shave_len &&
            (g_pre_mem || g_pre_work || g_mbuf_state == 1 || !boot_phase)) {
            off_t av_start = 0;
            size_t shave_av = dm_avail_range(s, e, eff_align, &av_start);
            size_t reserve = 0;
            size_t already_shaved = total_shaved_bytes();
            size_t max_total_shave = (len >= 0x40000000u) ? 0x2400000u : (g_pre_size ? g_pre_size + 0x400000u : 0x800000u);
            if (boot_phase && len >= 0x10000000u && len < 0x40000000u && shave_av >= 0x1000000u) {
                if (shave_av <= len && len - shave_av < 0x800000u) {
                    reserve = 0x800000u - (len - shave_av);
                    shave_av -= reserve;
                }
                if (reserve && max_total_shave < (len - shave_av) + already_shaved)
                    max_total_shave = (len - shave_av) + already_shaved;
            }
            if ((g_mbuf_state == 1 || !boot_phase || g_dbg_allocs >= 64u) && max_total_shave < already_shaved + 0x2400000u)
                max_total_shave = already_shaved + 0x2400000u;
            if (shave_av < len && (len - shave_av) <= 0x2400000u &&
                already_shaved + (len - shave_av) <= max_total_shave) {
                if (shave_av >= 0x4000u && !(is_desc_probe && len > 0x400000u && shave_av >= 0x40000u)) {
                    int32_t sr = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                                               s, e, shave_av, align, type, phys);
                    if (sr == 0 && phys) {
                        record_shave_entry(*phys, shave_av, len, type);
                        g_mem_tight = 1;
                        r = 0;
                        STAGE("arena shave #%d OK: game asked len=0x%zx, allocated 0x%zx at phys=0x%lx (shaved 0x%zx, reserve=0x%zx)",
                              g_shave_n, len, shave_av, *phys, len - shave_av, reserve);
                    }
                } else if (shave_av == 0 && (g_pre_mem || g_mbuf_state == 1 || !boot_phase || g_dbg_allocs >= 64u) &&
                           ((g_pre_mem && len <= g_pre_size && already_shaved + len <= g_pre_size) ||
                            ((g_mbuf_state == 1 || !boot_phase || g_dbg_allocs >= 64u) && len <= 0x1000000u && already_shaved + len <= 0x2400000u)) &&
                           (!is_desc_probe || len <= 0x400000u)) {
                    // Exact final chunk (or runtime streaming chunk up to 16 MB) when 0 bytes of unallocated DirectMemory
                    // remain outside our scanout buffers, and not an intermediate step of a descending-size probe loop:
                    // synthesize a tail-only shave entry backed by FlexibleMemory on MapDirectMemory/BatchMap while keeping
                    // scanout buffers intact.
                    long base_syn = (g_pre_mem && g_pre_mem_phys > 0)
                        ? g_pre_mem_phys
                        : (long)(sceKernelGetDirectMemorySize() - 0x2000000u);
                    size_t cur_off = (g_syn_phys_cursor > already_shaved) ? g_syn_phys_cursor : already_shaved;
                    if (eff_align > 0x4000u && (eff_align & (eff_align - 1u)) == 0) {
                        cur_off = (cur_off + eff_align - 1u) & ~(eff_align - 1u);
                    }
                    long syn_phys = base_syn + (long)cur_off;
                    g_syn_phys_cursor = cur_off + len;
                    if (phys) *phys = syn_phys;
                    record_shave_entry(syn_phys, 0, len, type);
                    g_mem_tight = 1;
                    r = 0;
                    STAGE("arena shave #%d (tail-only chunk) OK: game asked len=0x%zx, synthetic phys=0x%lx (backed by flex on map)",
                          g_shave_n, len, syn_phys);
                }
            }
        }
        // Step 5: Memory valve fallback if the allocation still failed (only before RegisterBuffers).
        if (pre_gpu_phase && (uint32_t)r == 0x80020023u && g_mbuf_state <= 0 && g_pre_mem && g_pre_size > 0x800000u) {
            shrink_early_mem_to(0x800000u);
            r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                              s, e, len, align, type, phys);
        }
        if (boot_phase && (uint32_t)r == 0x80020023u && g_mbuf_state <= 0 && g_pre_mem &&
            !(non_2mb_giant_bundle && g_cfg.shave_mode == 2) && total_shaved_bytes() < g_pre_size) {
            size_t cur_av = dm_avail_range(s, e, eff_align, NULL);
            if (cur_av + g_pre_size >= len) {
                release_early_mem();
                r = HOOK_CONTINUE(sceKernelAllocateDirectMemory, int32_t (*)(long, long, size_t, size_t, int, long *),
                                  s, e, len, align, type, phys);
            }
        }
        if ((uint32_t)r == 0x80020023u) {
            g_shave_last_probe_s = s;
            g_shave_last_probe_e = e;
            g_shave_last_probe_len = len;
            g_shave_last_probe_tid = cur_tid;
        }
    }
    if (!g_own_alloc && r == 0) {
        g_shave_last_probe_len = 0;
        g_shave_last_probe_tid = -1;
    }
    if (!g_own_alloc) {
        if (r != 0) __atomic_add_fetch(&g_dbg_alloc_fail, 1u, __ATOMIC_RELAXED);
        uint32_t acnt = __atomic_add_fetch(&g_dbg_allocs, 1u, __ATOMIC_RELAXED);
        void *caller = __builtin_return_address(0);
        api_ring_push("AllocDM(len=0x%zx al=0x%zx ty=%d) = 0x%08x phys=0x%lx c=%p",
                      len, align, type, (unsigned)r, (r == 0 && phys) ? *phys : 0L, caller);
        if (r != 0 || len >= 0x100000u || acnt <= 48u)
            STAGE("game AllocateDirectMemory(len=0x%zx align=0x%zx type=%d range=0x%lx..0x%lx) = 0x%08x phys=0x%lx caller=%p",
                  len, align, type, s, e, (unsigned)r, (r == 0 && phys) ? *phys : 0L, caller);
    }
    return r;
}

static Detour Detour_sceKernelMapDirectMemory2 __attribute__((unused));
static int g_map2_dm_hooked = 0;

// Per-thread re-entrancy guards (TLS-free, safe in Orbis PRX):
// In libkernel.sprx, sceKernelMapDirectMemory / sceKernelMapNamedDirectMemory internally call sceKernelMapDirectMemory2,
// and sceKernelBatchMap internally calls sceKernelBatchMap2. Guarding by thread ID prevents mutual recursion when
// mapping shaved-tail physical addresses (e.g. CUSA04529 0x11f000000) and avoids duplicate log entries / double counting.
#define HOOK_GUARD_SLOTS 16
static volatile int g_in_map_tids[HOOK_GUARD_SLOTS];
static volatile int g_in_batch_tids[HOOK_GUARD_SLOTS] __attribute__((unused));

static int tid_guard_enter(volatile int *slots, int tid)
{
    if (tid <= 0) tid = 1;
    for (int i = 0; i < HOOK_GUARD_SLOTS; i++) {
        if (__atomic_load_n(&slots[i], __ATOMIC_ACQUIRE) == tid) return -1; // already active on this thread
    }
    for (int i = 0; i < HOOK_GUARD_SLOTS; i++) {
        int expected = 0;
        if (__atomic_compare_exchange_n(&slots[i], &expected, tid, 0, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED))
            return i;
    }
    return -2;
}

static void tid_guard_leave(volatile int *slots, int slot)
{
    if (slot >= 0 && slot < HOOK_GUARD_SLOTS)
        __atomic_store_n(&slots[slot], 0, __ATOMIC_RELEASE);
}

static int tid_guard_active(volatile int *slots, int tid)
{
    if (tid <= 0) tid = 1;
    for (int i = 0; i < HOOK_GUARD_SLOTS; i++) {
        if (__atomic_load_n(&slots[i], __ATOMIC_ACQUIRE) == tid) return 1;
    }
    return 0;
}

static int32_t call_orig_map(void **addr, size_t len, int type, int prot, int flags, long dmStart, size_t maxPageSize,
                             const char *name, int map_kind)
{
    if (map_kind == 1)
        return HOOK_CONTINUE(sceKernelMapNamedDirectMemory,
                             int32_t (*)(void **, size_t, int, int, long, size_t, const char *),
                             addr, len, prot, flags, dmStart, maxPageSize, name);
    if (map_kind == 2 && g_map2_dm_hooked)
        return HOOK_CONTINUE(sceKernelMapDirectMemory2,
                             int32_t (*)(void **, size_t, int, int, int, long, size_t),
                             addr, len, type, prot, flags, dmStart, maxPageSize);
    return HOOK_CONTINUE(sceKernelMapDirectMemory,
                         int32_t (*)(void **, size_t, int, int, long, size_t),
                         addr, len, prot, flags, dmStart, maxPageSize);
}

static int32_t map_shaved_flex_tail(void *want_va, size_t tail_part, long shave_cut, int prot, int type,
                                    uintptr_t arena_vs, uintptr_t arena_ve, long arena_phys)
{
    void *got_va = want_va;
    g_own_alloc++;
    int32_t rf = sceKernelMapFlexibleMemory(&got_va, tail_part, prot, 0x10 /*MAP_FIXED*/);
    if (rf != 0 || got_va != want_va) {
        if (rf == 0 && got_va) sceKernelMunmap(got_va, tail_part);
        got_va = want_va;
        rf = sceKernelMapFlexibleMemory(&got_va, tail_part, prot, 0);
        if (rf == 0 && got_va != want_va) {
            sceKernelMunmap(got_va, tail_part);
            rf = -1;
        }
    }
    if (rf == 0 && got_va == want_va) {
        g_own_alloc--;
        record_flex_vmap_ex(want_va, tail_part, shave_cut, prot, type, arena_vs, arena_ve, arena_phys);
        return 0;
    }
    if (tail_part >= 0x200000u) {
        // Partial FlexibleMemory fallback when the process has < tail_part of free FlexibleMemory
        // (e.g. CUSA17722 0x8002000c): map the final 1 MB sentinel page first (so end-of-arena heap
        // footer writes at want_va + tail_part - 0x10 never fault), then map as much of the prefix as fits.
        void *sent_want = (uint8_t *)want_va + tail_part - 0x100000u;
        void *sent_got = sent_want;
        if (sceKernelMapFlexibleMemory(&sent_got, 0x100000u, prot, 0x10 /*MAP_FIXED*/) == 0 &&
            sent_got == sent_want) {
            record_flex_vmap_ex(sent_want, 0x100000u, shave_cut + (long)(tail_part - 0x100000u), prot, type,
                                arena_vs, arena_ve, arena_phys);
        }
        for (size_t try_flex = (tail_part - 0x100000u) & ~(size_t)0x1FFFFFu;
             try_flex >= 0x200000u; try_flex -= 0x200000u) {
            got_va = want_va;
            rf = sceKernelMapFlexibleMemory(&got_va, try_flex, prot, 0x10 /*MAP_FIXED*/);
            if (rf == 0 && got_va == want_va) {
                record_flex_vmap_ex(want_va, try_flex, shave_cut, prot, type, arena_vs, arena_ve, arena_phys);
                break;
            }
        }
    }
    g_own_alloc--;
    return rf;
}

static int32_t map_flex_aligned(void **addr, void *orig_addr_in, size_t len, int prot, size_t align)
{
    *addr = orig_addr_in;
    int flex_flags = orig_addr_in ? 0x10 /*MAP_FIXED*/ : 0;
    g_own_alloc++;
    int32_t rf = sceKernelMapFlexibleMemory(addr, len, prot, flex_flags);
    if (rf != 0 && flex_flags != 0) {
        *addr = NULL;
        rf = sceKernelMapFlexibleMemory(addr, len, prot, 0);
    }
    if (rf == 0 && *addr && !orig_addr_in && align > 0x4000u && (align & (align - 1u)) == 0 &&
        (((uintptr_t)(*addr)) & (align - 1u)) != 0) {
        sceKernelMunmap(*addr, len);
        void *tmp = NULL;
        size_t big = len + align;
        int32_t r2 = sceKernelMapFlexibleMemory(&tmp, big, prot, 0);
        if (r2 == 0 && tmp) {
            uintptr_t base = (uintptr_t)tmp;
            uintptr_t aligned = (base + align - 1u) & ~(uintptr_t)(align - 1u);
            if (aligned > base) sceKernelMunmap((void *)base, aligned - base);
            uintptr_t end_big = base + big;
            uintptr_t end_al  = aligned + len;
            if (end_big > end_al) sceKernelMunmap((void *)end_al, end_big - end_al);
            *addr = (void *)aligned;
            rf = 0;
        } else {
            *addr = NULL;
            rf = sceKernelMapFlexibleMemory(addr, len, prot, 0);
        }
    }
    g_own_alloc--;
    return rf;
}

static int32_t handle_game_map_inner(void **addr, size_t len, int type, int prot, int flags, long dmStart, size_t maxPageSize,
                                     const char *name, int map_kind, void *caller)
{
    void *orig_addr_in = addr ? *addr : NULL;
    const char *mname = (map_kind == 1) ? "MapNamedDirectMemory" : ((map_kind == 2) ? "MapDirectMemory2" : "MapDirectMemory");

    // Sync legacy single-entry globals if a test or caller modified them directly.
    if (g_shave_orig_len > 0 && g_shave_n == 0) {
        record_shave_entry(g_shave_phys, g_shave_real_len, g_shave_orig_len, g_shave_type);
    } else if (g_shave_orig_len == 0 && g_shave_n > 0) {
        g_shave_n = 0;
    }

    if (!g_own_alloc && g_shave_n > 0) {
        long req_end = dmStart + (long)len;
        for (int si = 0; si < g_shave_n; si++) {
            ShaveEntry *se = &g_shaves[si];
            if (!se->orig_len) continue;
            long shave_cut = se->phys + (long)se->real_len;
            long shave_end = se->phys + (long)se->orig_len;

            // 1. Straddle map across [phys..shave_cut) (real DirectMemory) and [shave_cut..shave_end) (FlexibleMemory tail).
            if (se->real_len > 0 && dmStart >= se->phys && dmStart < shave_cut && req_end > shave_cut && req_end <= shave_end) {
                flush_map_run();
                size_t real_part = (size_t)(shave_cut - dmStart);
                size_t tail_part = len - real_part;
                int32_t r = call_orig_map(addr, real_part, type, prot, flags, dmStart, maxPageSize, name, map_kind);
                if (r == 0 && addr && *addr) {
                    g_shave_tail_mapped = 1;
                    void *want_va = (uint8_t *)(*addr) + real_part;
                    int32_t rf = map_shaved_flex_tail(want_va, tail_part, shave_cut, prot, se->type,
                                                      (uintptr_t)(*addr), (uintptr_t)(*addr) + len, dmStart);
                    api_ring_push("%s(shave-straddle phys=0x%lx len=0x%zx) = 0 va=%p rf=0x%x c=%p",
                                  mname, dmStart, len, *addr, (unsigned)rf, caller);
                    STAGE("arena shave map OK: va=%p real=0x%zx shaved_tail=0x%zx flex_tail=%s (rf=0x%08x)",
                          *addr, real_part, tail_part, (rf == 0) ? "mapped" : "partial/sentinel", (unsigned)rf);
                    STAGE("game %s(phys=0x%lx len=0x%zx prot=0x%x flags=0x%x align=0x%zx in=%p) = 0x00000000 va=%p (shaved) caller=%p",
                          mname, dmStart, len, prot, flags, maxPageSize, orig_addr_in, *addr, caller);
                    return 0;
                }
                if (addr) *addr = orig_addr_in;
                if (dmStart == se->phys && g_flips == 0 && g_gpu_state == 0 && g_mbuf_state <= 0) {
                    int rr = sceKernelReleaseDirectMemory(se->phys, se->real_len);
                    release_early_mem();
                    release_early_work();
                    long full_phys = 0;
                    int ar = HOOK_CONTINUE(sceKernelAllocateDirectMemory,
                                           int32_t (*)(long, long, size_t, size_t, int, long *),
                                           se->phys, shave_end, se->orig_len, 0x200000u, se->type, &full_phys);
                    STAGE("arena shave fallback to valve: release_shave=0x%08x realloc_full=0x%08x phys=0x%lx",
                          (unsigned)rr, (unsigned)ar, full_phys);
                    se->orig_len = 0;
                    se->real_len = 0;
                    if (si == g_shave_n - 1) {
                        g_shave_orig_len = 0;
                        g_shave_real_len = 0;
                    }
                    if (ar == 0)
                        dmStart = full_phys;
                }
                break;
            }

            // 2. Tail-only map that overlaps g_pre_mem_phys (or a synthetic real_len==0 shaved chunk):
            //    Must map directly from FlexibleMemory BEFORE call_orig_map so we never alias g_pre_mem's physical pages!
            if (addr && dmStart >= shave_cut && req_end <= shave_end) {
                int overlaps_pre_mem = (g_pre_mem && g_pre_size > 0 &&
                                        req_end > g_pre_mem_phys && dmStart < g_pre_mem_phys + (long)g_pre_size);
                if (se->real_len == 0 || overlaps_pre_mem) {
                    flush_map_run();
                    int32_t rf = map_flex_aligned(addr, orig_addr_in, len, prot, maxPageSize);
                    if (rf == 0 && *addr) {
                        g_shave_tail_mapped = 1;
                        record_flex_vmap(*addr, len, dmStart, prot, se->type);
                        api_ring_push("%s(shave-tail-direct phys=0x%lx len=0x%zx prot=0x%x fl=0x%x in=%p) = 0 va=%p c=%p",
                                      mname, dmStart, len, prot, flags, orig_addr_in, *addr, caller);
                        STAGE("arena shave tail-only flex map OK (protected display phys): phys=0x%lx len=0x%zx -> va=%p caller=%p",
                              dmStart, len, *addr, caller);
                        return 0;
                    }
                    *addr = orig_addr_in;
                }
            }
        }
    }

    int32_t r = call_orig_map(addr, len, type, prot, flags, dmStart, maxPageSize, name, map_kind);
    if (r != 0 && !g_own_alloc && g_shave_n > 0 && addr) {
        long req_end = dmStart + (long)len;
        for (int si = 0; si < g_shave_n; si++) {
            ShaveEntry *se = &g_shaves[si];
            if (!se->orig_len) continue;
            long shave_cut = se->phys + (long)se->real_len;
            long shave_end = se->phys + (long)se->orig_len;
            if (dmStart >= shave_cut && req_end <= shave_end) {
                flush_map_run();
                int32_t rf = map_flex_aligned(addr, orig_addr_in, len, prot, maxPageSize);
                if (rf == 0 && *addr) {
                    g_shave_tail_mapped = 1;
                    record_flex_vmap(*addr, len, dmStart, prot, se->type);
                    api_ring_push("%s(shave-tail phys=0x%lx len=0x%zx prot=0x%x fl=0x%x in=%p) = 0 va=%p c=%p",
                                  mname, dmStart, len, prot, flags, orig_addr_in, *addr, caller);
                    STAGE("arena shave tail-only flex map OK: phys=0x%lx len=0x%zx prot=0x%x flags=0x%x in=%p -> va=%p caller=%p",
                          dmStart, len, prot, flags, orig_addr_in, *addr, caller);
                    return 0;
                }
                *addr = orig_addr_in;
            }
        }
    }
    if (!g_own_alloc) {
        uintptr_t va_out = (r == 0 && addr && *addr) ? (uintptr_t)(*addr) : 0;
        uint32_t mcnt = __atomic_add_fetch(&g_dbg_maps, 1u, __ATOMIC_RELAXED);
        api_ring_push("%s(phys=0x%lx len=0x%zx prot=0x%x fl=0x%x in=%p) = 0x%08x va=0x%lx c=%p",
                      mname, dmStart, len, prot, flags, orig_addr_in, (unsigned)r, (unsigned long)va_out, caller);
        if (r == 0 && len == 0x100000u && map_kind != 1) {
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
        if (r != 0 || len >= 0x100000u || mcnt <= 48u)
            STAGE("game %s(phys=0x%lx len=0x%zx prot=0x%x flags=0x%x align=0x%zx in=%p) = 0x%08x va=%p caller=%p",
                  mname, dmStart, len, prot, flags, maxPageSize, orig_addr_in, (unsigned)r, (void *)va_out, caller);
    }
    return r;
}

static int32_t handle_game_map(void **addr, size_t len, int type, int prot, int flags, long dmStart, size_t maxPageSize,
                               const char *name, int map_kind, void *caller)
{
    int tid = scePthreadGetthreadid();
    int slot = tid_guard_enter(g_in_map_tids, tid);
    int32_t r = handle_game_map_inner(addr, len, type, prot, flags, dmStart, maxPageSize, name, map_kind, caller);
    tid_guard_leave(g_in_map_tids, slot);
    return r;
}

int32_t sceKernelMapDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize)
{
    return handle_game_map(addr, len, -1, prot, flags, dmStart, maxPageSize, NULL, 0, __builtin_return_address(0));
}
int32_t sceKernelMapNamedDirectMemory_hook(void **addr, size_t len, int prot, int flags, long dmStart, size_t maxPageSize, const char *name)
{
    return handle_game_map(addr, len, -1, prot, flags, dmStart, maxPageSize, name, 1, __builtin_return_address(0));
}
int32_t sceKernelMapDirectMemory2_hook(void **addr, size_t len, int type, int prot, int flags, long dmStart, size_t maxPageSize)
{
    int tid = scePthreadGetthreadid();
    if (g_own_alloc > 0 || tid_guard_active(g_in_map_tids, tid)) {
        return HOOK_CONTINUE(sceKernelMapDirectMemory2,
                             int32_t (*)(void **, size_t, int, int, int, long, size_t),
                             addr, len, type, prot, flags, dmStart, maxPageSize);
    }
    return handle_game_map(addr, len, type, prot, flags, dmStart, maxPageSize, NULL, 2, __builtin_return_address(0));
}

static void debug_hooks_install(void);

static Detour Detour_sceKernelAllocateMainDirectMemory;
static int g_main_dm_hooked = 0;
int32_t sceKernelAllocateMainDirectMemory_hook(size_t len, size_t align, int type, long *phys)
{
    return sceKernelAllocateDirectMemory_hook(0, (long)sceKernelGetDirectMemorySize(), len, align, type, phys);
}

static Detour Detour_sceKernelReleaseDirectMemory;
static int g_rel_dm_hooked = 0;
int32_t sceKernelReleaseDirectMemory_hook(off_t start, size_t len)
{
    int is_ours = (g_own_alloc > 0) ||
                  (g_pre_mem && start >= (off_t)g_pre_mem_phys && start < (off_t)(g_pre_mem_phys + (long)g_pre_size)) ||
                  (g_pre_work && start == (off_t)g_pre_work_phys) ||
                  (g_pre_gpu && start == (off_t)g_pre_gpu_phys) ||
                  (g_dump_mem && start == (off_t)g_dump_phys);
    if (!is_ours && g_shave_n > 0) {
        long rel_s = (long)start;
        long rel_e = rel_s + (long)len;
        for (int si = 0; si < g_shave_n; si++) {
            ShaveEntry *se = &g_shaves[si];
            if (!se->orig_len) continue;
            long se_end = se->phys + (long)se->orig_len;
            if (rel_s <= se->phys && rel_e >= se_end) {
                size_t real_len = se->real_len;
                long real_phys = se->phys;
                se->orig_len = 0;
                se->real_len = 0;
                if (si == g_shave_n - 1) { g_shave_orig_len = 0; g_shave_real_len = 0; }
                if (real_len == 0) {
                    __atomic_add_fetch(&g_dbg_releases, 1u, __ATOMIC_RELAXED);
                    api_ring_push("ReleaseDM(shave-syn phys=0x%lx len=0x%zx) = 0", rel_s, len);
                    return 0;
                }
                if (rel_s == real_phys && len > real_len) len = real_len;
                break;
            }
        }
    }
    int32_t r = HOOK_CONTINUE(sceKernelReleaseDirectMemory, int32_t (*)(off_t, size_t), start, len);
    if (!is_ours) {
        void *caller = __builtin_return_address(0);
        uint32_t rcnt = __atomic_add_fetch(&g_dbg_releases, 1u, __ATOMIC_RELAXED);
        api_ring_push("ReleaseDM(phys=0x%lx len=0x%zx) = 0x%08x c=%p", (long)start, len, (unsigned)r, caller);
        if (r != 0 || len >= 0x100000u || rcnt <= 32u)
            STAGE("game ReleaseDirectMemory(phys=0x%lx len=0x%zx) = 0x%08x caller=%p", (long)start, len, (unsigned)r, caller);
    }
    return r;
}

typedef struct {
    void *start;
    off_t offset;
    size_t length;
    uint8_t protection;
    uint8_t type;
    int16_t reserved;
    int32_t operation;
} OrbisBatchMapEntry;
_Static_assert(sizeof(OrbisBatchMapEntry) == 0x20, "OrbisBatchMapEntry must be 32 bytes");

static Detour Detour_sceKernelBatchMap;
static int g_batch_map_hooked = 0;
static Detour Detour_sceKernelBatchMap2;
static int g_batch_map2_hooked = 0;

static int32_t call_orig_batch_map(void *entries, int numEntries, int *numOut, int flags, int is_bm2)
{
    if (is_bm2 && g_batch_map2_hooked)
        return HOOK_CONTINUE(sceKernelBatchMap2, int32_t (*)(void *, int, int *, int), entries, numEntries, numOut, flags);
    if (!is_bm2 && g_batch_map_hooked)
        return HOOK_CONTINUE(sceKernelBatchMap, int32_t (*)(void *, int, int *, int), entries, numEntries, numOut, flags);
    if (g_batch_map2_hooked)
        return HOOK_CONTINUE(sceKernelBatchMap2, int32_t (*)(void *, int, int *, int), entries, numEntries, numOut, flags);
    return HOOK_CONTINUE(sceKernelBatchMap, int32_t (*)(void *, int, int *, int), entries, numEntries, numOut, flags);
}

static ShaveEntry *find_shave_for_batch_entry(const OrbisBatchMapEntry *e, int *kind_out)
{
    if (!e || e->operation != 0 /*ORBIS_KERNEL_MAP_OP_MAP_DIRECT*/ || e->length == 0) return NULL;
    long dmStart = (long)e->offset;
    long req_end = dmStart + (long)e->length;
    for (int si = 0; si < g_shave_n; si++) {
        ShaveEntry *se = &g_shaves[si];
        if (!se->orig_len) continue;
        long shave_cut = se->phys + (long)se->real_len;
        long shave_end = se->phys + (long)se->orig_len;
        if (se->real_len > 0 && dmStart >= se->phys && dmStart < shave_cut && req_end > shave_cut && req_end <= shave_end) {
            if (kind_out) *kind_out = 1; // straddle
            return se;
        }
        if (dmStart >= shave_cut && req_end <= shave_end) {
            if (kind_out) *kind_out = 2; // tail-only
            return se;
        }
    }
    return NULL;
}

static int32_t handle_game_batch_map(void *entries, int numEntries, int *numOut, int flags, int is_bm2, void *caller)
{
    const char *bname = is_bm2 ? "BatchMap2" : "BatchMap";
    if (!g_own_alloc) flush_map_run();
    if (g_shave_orig_len > 0 && g_shave_n == 0) {
        record_shave_entry(g_shave_phys, g_shave_real_len, g_shave_orig_len, g_shave_type);
    } else if (g_shave_orig_len == 0 && g_shave_n > 0) {
        g_shave_n = 0;
    }

    int tid = scePthreadGetthreadid();
    int slot = tid_guard_enter(g_in_batch_tids, tid);
    OrbisBatchMapEntry *ent = (OrbisBatchMapEntry *)entries;
    int has_shave_hit = 0;
    if (!g_own_alloc && g_shave_n > 0 && ent && numEntries > 0 && numEntries <= 4096) {
        for (int i = 0; i < numEntries; i++) {
            if (find_shave_for_batch_entry(&ent[i], NULL)) {
                has_shave_hit = 1;
                break;
            }
        }
    }

    int32_t r = 0;
    int total_done = 0;
    if (!has_shave_hit) {
        r = call_orig_batch_map(entries, numEntries, numOut, flags, is_bm2);
        total_done = numOut ? *numOut : (r == 0 ? numEntries : 0);
    } else {
        int i = 0;
        while (i < numEntries) {
            int sh_kind = 0;
            ShaveEntry *se = find_shave_for_batch_entry(&ent[i], &sh_kind);
            if (!se) {
                int run_start = i;
                while (i < numEntries && !find_shave_for_batch_entry(&ent[i], NULL)) i++;
                int sub_done = 0;
                r = call_orig_batch_map(&ent[run_start], i - run_start, &sub_done, flags, is_bm2);
                if (r != 0) {
                    total_done += sub_done;
                    break;
                }
                total_done += (i - run_start);
                continue;
            }
            long dmStart = (long)ent[i].offset;
            size_t orig_len = ent[i].length;
            long shave_cut = se->phys + (long)se->real_len;
            int prot = (int)ent[i].protection;
            if (sh_kind == 1) {
                size_t real_part = (size_t)(shave_cut - dmStart);
                size_t tail_part = orig_len - real_part;
                ent[i].length = real_part;
                int sub_done = 0;
                r = call_orig_batch_map(&ent[i], 1, &sub_done, flags, is_bm2);
                ent[i].length = orig_len;
                if (r != 0 || !ent[i].start) {
                    if (r == 0) r = -1;
                    break;
                }
                g_shave_tail_mapped = 1;
                void *want_va = (uint8_t *)ent[i].start + real_part;
                int32_t rf = map_shaved_flex_tail(want_va, tail_part, shave_cut, prot, se->type,
                                                  (uintptr_t)ent[i].start, (uintptr_t)ent[i].start + orig_len, dmStart);
                api_ring_push("%s(shave-straddle idx=%d/%d phys=0x%lx len=0x%zx) = 0 va=%p rf=0x%x c=%p",
                              bname, i, numEntries, dmStart, orig_len, ent[i].start, (unsigned)rf, caller);
                STAGE("arena shave %s straddle OK: entry %d/%d phys=0x%lx real=0x%zx tail=0x%zx -> va=%p (rf=0x%08x)",
                      bname, i, numEntries, dmStart, real_part, tail_part, ent[i].start, (unsigned)rf);
                total_done++;
                i++;
            } else {
                void *orig_va = ent[i].start;
                void *got_va = orig_va;
                int flex_flags = orig_va ? 0x10 /*MAP_FIXED*/ : 0;
                g_own_alloc++;
                int32_t rf = sceKernelMapFlexibleMemory(&got_va, orig_len, prot, flex_flags);
                if (rf != 0 && flex_flags != 0) {
                    got_va = orig_va;
                    rf = sceKernelMapFlexibleMemory(&got_va, orig_len, prot, 0);
                }
                g_own_alloc--;
                if (rf != 0 || !got_va) {
                    r = rf ? rf : -1;
                    break;
                }
                ent[i].start = got_va;
                g_shave_tail_mapped = 1;
                record_flex_vmap_ex(got_va, orig_len, dmStart, prot, se->type,
                                    (uintptr_t)got_va, (uintptr_t)got_va + orig_len, dmStart);
                api_ring_push("%s(shave-tail idx=%d/%d phys=0x%lx len=0x%zx) = 0 va=%p c=%p",
                              bname, i, numEntries, dmStart, orig_len, got_va, caller);
                STAGE("arena shave %s tail-only OK: entry %d/%d phys=0x%lx len=0x%zx -> va=%p",
                      bname, i, numEntries, dmStart, orig_len, got_va);
                total_done++;
                i++;
            }
        }
        if (numOut) *numOut = total_done;
    }
    tid_guard_leave(g_in_batch_tids, slot);

    if (!g_own_alloc) {
        if (r != 0) __atomic_add_fetch(&g_dbg_batch_fail, 1u, __ATOMIC_RELAXED);
        uint32_t bcnt = __atomic_add_fetch(&g_dbg_batch_maps, 1u, __ATOMIC_RELAXED);
        int n_done = numOut ? *numOut : total_done;
        long e0_phys = (ent && numEntries > 0) ? (long)ent[0].offset : 0L;
        size_t e0_len = (ent && numEntries > 0) ? ent[0].length : 0u;
        void *e0_va = (ent && numEntries > 0) ? ent[0].start : NULL;
        int e0_op = (ent && numEntries > 0) ? ent[0].operation : -1;
        api_ring_push("%s(n=%d out=%d fl=0x%x e0:op=%d phys=0x%lx len=0x%zx va=%p) = 0x%08x c=%p",
                      bname, numEntries, n_done, flags, e0_op, e0_phys, e0_len, e0_va, (unsigned)r, caller);
        if (r != 0 || bcnt <= 24u || has_shave_hit)
            STAGE("game %s(n=%d out=%d flags=0x%x e0:op=%d phys=0x%lx len=0x%zx va=%p) = 0x%08x caller=%p",
                  bname, numEntries, n_done, flags, e0_op, e0_phys, e0_len, e0_va, (unsigned)r, caller);
    }
    return r;
}

int32_t sceKernelBatchMap_hook(void *entries, int numEntries, int *numOut, int flags)
{
    int tid = scePthreadGetthreadid();
    if (g_own_alloc > 0 || tid_guard_active(g_in_batch_tids, tid)) {
        return HOOK_CONTINUE(sceKernelBatchMap, int32_t (*)(void *, int, int *, int), entries, numEntries, numOut, flags);
    }
    return handle_game_batch_map(entries, numEntries, numOut, flags, 0, __builtin_return_address(0));
}

int32_t sceKernelBatchMap2_hook(void *entries, int numEntries, int *numOut, int flags)
{
    int tid = scePthreadGetthreadid();
    if (g_own_alloc > 0 || tid_guard_active(g_in_batch_tids, tid)) {
        return HOOK_CONTINUE(sceKernelBatchMap2, int32_t (*)(void *, int, int *, int), entries, numEntries, numOut, flags);
    }
    return handle_game_batch_map(entries, numEntries, numOut, flags, 1, __builtin_return_address(0));
}

static Detour Detour_sceKernelVirtualQuery;
static int g_vquery_hooked = 0;
int32_t sceKernelVirtualQuery_hook(const void *addr, int flags, OrbisKernelVirtualQueryInfo *info, size_t infoSize)
{
    int32_t r = HOOK_CONTINUE(sceKernelVirtualQuery,
                              int32_t (*)(const void *, int, OrbisKernelVirtualQueryInfo *, size_t),
                              addr, flags, info, infoSize);
    if (r == 0 && !g_in_crash && !g_own_alloc && info && infoSize >= sizeof(OrbisKernelVirtualQueryInfo)) {
        uintptr_t q_va = (uintptr_t)addr;
        uintptr_t start_va = (uintptr_t)info->unk01;
        uint32_t fn = __atomic_load_n(&g_flex_vmap_n, __ATOMIC_ACQUIRE);
        for (uint32_t i = 0; i < fn && i < FLEX_VMAP_MAX; i++) {
            if (g_flex_vmap[i].phys_start < 0) continue; // skip internal alloc_flex_gpu buffers
            uintptr_t avs = g_flex_vmap[i].arena_va_start;
            uintptr_t ave = g_flex_vmap[i].arena_va_end;
            if ((q_va >= avs && q_va < ave) || (start_va >= avs && start_va < ave)) {
                info->unk01 = (void *)avs;
                info->unk02 = (void *)ave;
                info->offset = (off_t)g_flex_vmap[i].arena_phys_start;
                info->unk04 = g_flex_vmap[i].prot;
                info->unk05 = g_flex_vmap[i].type;
                info->isFlexibleMemory = 0;
                info->isDirectMemory = 1;
                info->isCommitted = 1;
                break;
            }
        }
    }
    return r;
}

static void debug_hooks_install(void)
{
    HOOK32(sceKernelAllocateDirectMemory);
    HOOK32(sceKernelAvailableDirectMemorySize);
    HOOK32(sceKernelMapDirectMemory);
    HOOK32(sceKernelMapNamedDirectMemory);
    g_dm_hooks = 1;
    crash_load_modules();
    // Hook libkernel DirectMemory / BatchMap / VirtualQuery entrypoints directly via their resolved PLT/GOT symbols
    // (on PS4 firmware 11.00+, sceKernelGetModuleList only enumerates user modules and omits libkernel.sprx).
    if (!g_main_dm_hooked &&
        vcfg_hook_install(&Detour_sceKernelAllocateMainDirectMemory,
                          (uint64_t)(uintptr_t)&sceKernelAllocateMainDirectMemory,
                          (void *)&sceKernelAllocateMainDirectMemory_hook, "sceKernelAllocateMainDirectMemory"))
        g_main_dm_hooked = 1;
    if (!g_map2_dm_hooked &&
        vcfg_hook_install(&Detour_sceKernelMapDirectMemory2,
                          (uint64_t)(uintptr_t)&sceKernelMapDirectMemory2,
                          (void *)&sceKernelMapDirectMemory2_hook, "sceKernelMapDirectMemory2"))
        g_map2_dm_hooked = 1;
    if (!g_rel_dm_hooked &&
        vcfg_hook_install(&Detour_sceKernelReleaseDirectMemory,
                          (uint64_t)(uintptr_t)&sceKernelReleaseDirectMemory,
                          (void *)&sceKernelReleaseDirectMemory_hook, "sceKernelReleaseDirectMemory"))
        g_rel_dm_hooked = 1;
    if (!g_batch_map_hooked &&
        vcfg_hook_install(&Detour_sceKernelBatchMap,
                          (uint64_t)(uintptr_t)&sceKernelBatchMap,
                          (void *)&sceKernelBatchMap_hook, "sceKernelBatchMap"))
        g_batch_map_hooked = 1;
    if (!g_batch_map2_hooked &&
        vcfg_hook_install(&Detour_sceKernelBatchMap2,
                          (uint64_t)(uintptr_t)&sceKernelBatchMap2,
                          (void *)&sceKernelBatchMap2_hook, "sceKernelBatchMap2"))
        g_batch_map2_hooked = 1;
    if (!g_vquery_hooked &&
        vcfg_hook_install(&Detour_sceKernelVirtualQuery,
                          (uint64_t)(uintptr_t)&sceKernelVirtualQuery,
                          (void *)&sceKernelVirtualQuery_hook, "sceKernelVirtualQuery"))
        g_vquery_hooked = 1;
    crash_handlers_install();
    HOOK32(sceVideoOutOpen);
    HOOK32(sceVideoOutClose);
    g_dbg_hooks = 1;
    STAGE("debug hooks + crash signal handlers installed (always-on logger + Allocate/Available/Map/Release/BatchMap/VQuery trace, main_dm=%d map2=%d rel=%d bm=%d/%d vq=%d)",
          g_main_dm_hooked, g_map2_dm_hooked, g_rel_dm_hooked, g_batch_map_hooked, g_batch_map2_hooked, g_vquery_hooked);
}
static void debug_hooks_remove(void)
{
    if (g_vquery_hooked) { Detour_RestoreFunction(&Detour_sceKernelVirtualQuery); g_vquery_hooked = 0; }
    if (g_main_dm_hooked) { Detour_RestoreFunction(&Detour_sceKernelAllocateMainDirectMemory); g_main_dm_hooked = 0; }
    if (g_map2_dm_hooked) { Detour_RestoreFunction(&Detour_sceKernelMapDirectMemory2); g_map2_dm_hooked = 0; }
    if (g_rel_dm_hooked) { Detour_RestoreFunction(&Detour_sceKernelReleaseDirectMemory); g_rel_dm_hooked = 0; }
    if (g_batch_map_hooked) { Detour_RestoreFunction(&Detour_sceKernelBatchMap); g_batch_map_hooked = 0; }
    if (g_batch_map2_hooked) { Detour_RestoreFunction(&Detour_sceKernelBatchMap2); g_batch_map2_hooked = 0; }
    if (g_dm_hooks) {
        UNHOOK(sceKernelAllocateDirectMemory);
        UNHOOK(sceKernelAvailableDirectMemorySize);
        UNHOOK(sceKernelMapDirectMemory);
        UNHOOK(sceKernelMapNamedDirectMemory);
        g_dm_hooks = 0;
    }
    if (!g_dbg_hooks) return;
    UNHOOK(sceVideoOutOpen);
    UNHOOK(sceVideoOutClose);
    g_dbg_hooks = 0;
}
