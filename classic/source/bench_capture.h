// Benchmark-only capture. One triplet in flight; no scratch surface is registered
// with VideoOut. Every submission keeps the game's index, mode, argument and tail.
#pragma once
#define BENCH_SURFACE 0x800000u
#define BENCH_DCB_BYTES 0x2000u
#define BENCH_DCB_OFF 0x1000u
_Static_assert(BENCH_DCB_OFF + 3u * BENCH_DCB_BYTES <= 0x10000u, "bench descriptors/DCBs exceed their allocation");
_Static_assert(DCB8_SZ + 2u * 256u + 256u + 64u < BENCH_DCB_BYTES, "bench DCB has no append margin");
enum { BENCH_IDLE, BENCH_GT, BENCH_B, BENCH_WAIT, BENCH_WRITE, BENCH_DONE, BENCH_STOP };
static uint32_t g_bench_state, g_bench_interval = 240, g_bench_limit = 6, g_bench_width = 1920;
static uint32_t g_bench_saved, g_bench_attempt, g_bench_next = 240, g_bench_first, g_bench_cookie;
static uint32_t g_bench_frames[3], g_bench_indices[3];
static uint64_t g_bench_times[3];
static size_t g_bench_plane;
static uint8_t *g_bench_surfaces;
static uint32_t *g_bench_tables;
static int g_bench_started, g_bench_timing;
static int g_bench_wait_go;  // marker start_flip == 1: sampling starts when LOG_DIR/bench_go appears (the owner is in the scene)
static fg_config_t g_bench_profile;
static VoAttr g_bench_attr;
static uint32_t g_bench_hud;
static char g_bench_dir[160];

static void bench_stop(const char *reason)
{
    __atomic_store_n(&g_bench_state, BENCH_STOP, __ATOMIC_RELEASE);
    BLOG("BENCH STOP %s", reason);
    klog("[bb_probe] BENCH STOP %s; native flips continue, allocations retained\n", reason);
    if (g_flips > 0) lg("BENCH STOP %s; native flips continue, allocations retained\n", reason);  // lg() opens stdio files: only once the game runs
}

static int bench_is_30hz_title(void)
{
    return !strcmp(g_title_id, "CUSA03041") || !strcmp(g_title_id, "CUSA05571") || !strcmp(g_title_id, "CUSA08519");
}

static int bench_bootstrap(void)
{
    if (strcmp(g_title_id, "CUSA00557") && strcmp(g_title_id, "CUSA09171") && !bench_is_30hz_title()) {
        klog("[bb_probe] BENCH inert: title not on the capture allowlist\n");
        return 0;
    }
    // raw kernel I/O (like read_mode_raw): stdio inside plugin_load crashes the game
    int mfd = sceKernelOpen(LOG_DIR "/bench_capture", 0 /*O_RDONLY*/, 0);
    if (mfd < 0) { klog("[bb_probe] BENCH inert: bench_capture marker missing\n"); return 0; }
    unsigned k = 240, m = 6, w = 1920, s = 0;  // s: first sample not before this flip (time to reach gameplay)
    char mb[64] = {0};
    long mn = (long)sceKernelRead(mfd, mb, sizeof(mb) - 1);
    sceKernelClose(mfd);
    if (mn < 0) mn = 0;
    mb[mn] = 0;
    int n = sscanf(mb, "%u %u %u %u", &k, &m, &w, &s);
    if (n < 2 || k < 6 || k > 3600 || m < 1 || m > 64 || (n >= 3 && w != 960 && w != 1920) || s > 1000000u) {
        klog("[bb_probe] BENCH inert: expected K M [width [start_flip]], K=6..3600 M=1..64 width=960/1920\n"); return 0;
    }
    g_bench_interval = k; g_bench_limit = m; g_bench_width = w; g_bench_next = s > 120u ? s : (k > 120 ? k : 120);
    g_bench_wait_go = (s == 1u);
    g_bench_enabled = 1;
    klog("[bb_probe] BENCH build=capture-v1 K=%u M=%u requested_width=%u; no generated flips\n", k, m, w);
    return 1;
}

// alloc_dm with the error codes in bench_boot.txt (the klog line of alloc_dm is easy to miss)
static void *bench_alloc(size_t len, size_t align, int type)
{
    long phys = 0; void *addr = NULL;
    int r = sceKernelAllocateDirectMemory(0, (long)sceKernelGetDirectMemorySize(), len, align, type, &phys);
    BLOG("alloc len=0x%zx align=0x%zx type=%d -> allocate r=0x%08x", len, align, type, (unsigned)r);
    if (r == 0) { r = sceKernelMapDirectMemory(&addr, len, 0x33, 0, phys, align); BLOG("  map r=0x%08x addr=%p", (unsigned)r, addr); }
    if (r != 0 || !addr) return NULL;
    memset(addr, 0, len);
    return addr;
}

// flexible memory (separate pool from direct memory), mapped CPU rw + GPU rw
static void *bench_alloc_flex(size_t len)
{
    void *addr = NULL;
    int r = sceKernelMapFlexibleMemory(&addr, len, 0x33, 0);
    BLOG("flex len=0x%zx -> map r=0x%08x addr=%p", len, (unsigned)r, addr);
    if (r != 0 || !addr) return NULL;
    memset(addr, 0, len);
    return addr;
}

static void bench_mem_report(const char *when)
{
    long phys = 0; size_t sz = 0, fsz = 0;
    int r1 = sceKernelAvailableDirectMemorySize(0, (off_t)sceKernelGetDirectMemorySize(), 0x10000, (off_t)(uintptr_t)&phys, (size_t)(uintptr_t)&sz);
    int r2 = sceKernelAvailableFlexibleMemorySize((size_t)(uintptr_t)&fsz);
    BLOG("mem %s: direct largest free block 0x%zx at 0x%lx (r=0x%08x), flexible available 0x%zx (r=0x%08x)", when, sz, phys, (unsigned)r1, fsz, (unsigned)r2);
}

static void bench_allocate(void)
{
    bench_mem_report("before capture allocations");
    g_bench_surfaces = (uint8_t *)bench_alloc(2u * BENCH_SURFACE, 0x200000, 3);
    if (!g_bench_surfaces) g_bench_surfaces = (uint8_t *)bench_alloc(2u * BENCH_SURFACE, 0x200000, 0);
    if (!g_bench_surfaces) g_bench_surfaces = (uint8_t *)bench_alloc_flex(2u * BENCH_SURFACE);
    bench_mem_report("after surfaces");
    if (!g_bench_surfaces) { bench_stop("offscreen allocation failed"); return; }
    // the game leaves little direct memory free (33 MB for 960 wide failed with 0x80020023 on CUSA00557): narrower crops
    static const uint32_t widths[] = { 1920u, 960u, 640u, 480u, 320u, 240u };
    for (unsigned wi = 0; wi < sizeof(widths) / sizeof(widths[0]); wi++) {
        if (widths[wi] > g_bench_width) continue;
        g_bench_width = widths[wi];
        g_bench_plane = (size_t)g_bench_width * 1080u * 8u;
        size_t bytes = (4u * g_bench_plane + 0x10000u + 0xffffu) & ~(size_t)0xffffu;
        g_dump_mem = (uint8_t *)bench_alloc(bytes, 0x10000, 0);
        if (!g_dump_mem) g_dump_mem = (uint8_t *)bench_alloc(bytes, 0x10000, 3);  // onion pool exhausted: try garlic
        if (!g_dump_mem) g_dump_mem = (uint8_t *)bench_alloc_flex(bytes);          // direct memory exhausted: flexible pool
        if (g_dump_mem) {
            g_bench_tables = (uint32_t *)(g_dump_mem + 4u * g_bench_plane);
            BLOG("capture width settled at %u (plane 0x%zx)", g_bench_width, g_bench_plane);
            klog("[bb_probe] BENCH allocation width=%u height=1080 rgba16le plane=%zu bytes; scratch is never scanned out\n", g_bench_width, g_bench_plane);
            return;
        }
    }
    bench_stop("dump allocation failed");
}

static uint32_t *bench_pass(uint32_t *p, uint32_t *tb, uint32_t shader, uint32_t r1, uint32_t r2, uint32_t dx, uint32_t dy)
{
    uint64_t gb = (uint64_t)(uintptr_t)g_gpu, addr = (uint64_t)(uintptr_t)tb;
    PASS_BARRIER();
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
    *p++ = PKT3(0x76, 4, 1); *p++ = 0x207; *p++ = 64; *p++ = 1; *p++ = 1;
    *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)addr; *p++ = (uint32_t)(addr >> 32);
    SET_PASS(shader, r1, r2);
    *p++ = PKT3(0x15, 4, 1); *p++ = dx; *p++ = dy; *p++ = 1; *p++ = 1;
    PASS_BARRIER();
    return p;
}

static uint32_t *bench_pack(uint32_t *p, uint64_t surface, unsigned plane, uint32_t tidx)
{
    // Separate descriptors for A, GT, B and mid: never edit a table still on the GPU.
    uint32_t *tb = g_bench_tables + plane * 32u;
    make_t(tb, surface, 1920, 1080, 1920, tidx);
    make_v(tb + 8, (uint64_t)(uintptr_t)(g_dump_mem + plane * g_bench_plane), (uint32_t)g_bench_plane);
    tb[12] = g_bench_width; tb[13] = 1080;
    return bench_pass(p, tb, g_bench_pack_off, PACK16_RSRC1, PACK16_RSRC2, g_bench_width / 8u, 135);
}

static int bench_write(const char *path, const void *data, size_t size)
{
    char temp[256]; snprintf(temp, sizeof(temp), "%s.stage", path);
    FILE *f = fopen(temp, "wb"); if (!f) return 0;
    int ok = fwrite(data, 1, size, f) == size;
    if (fflush(f)) ok = 0;
    if (fclose(f)) ok = 0;
    return ok && rename(temp, path) == 0;
}

static void bench_io_tick(void)
{
    if (__atomic_load_n(&g_bench_state, __ATOMIC_ACQUIRE) != BENCH_WAIT || !g_gpu) return;
    if ((uint32_t)*(volatile uint64_t *)(g_gpu + DUMP_DONE_OFF) != g_bench_cookie) return;
    uint32_t expected = BENCH_WAIT;
    if (!__atomic_compare_exchange_n(&g_bench_state, &expected, BENCH_WRITE, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return;
    if (!g_bench_timing) {
        lg("BENCH discard attempt=%u: nonuniform/native-60 timing\n", g_bench_attempt);
        __atomic_store_n(&g_bench_state, BENCH_IDLE, __ATOMIC_RELEASE); return;
    }
    static const char *part[4] = { "a", "gt", "b", "mid" };
    for (unsigned i = 0; i < 4; i++) {
        char p[256]; snprintf(p, sizeof(p), "%s/bench_%04u_%s.rgba", g_bench_dir, g_bench_saved, part[i]);
        if (!bench_write(p, g_dump_mem + i * g_bench_plane, g_bench_plane)) { bench_stop("raw file write failed"); return; }
    }
    // JSON is published last: fetch ignores partial triplets without this commit marker.
    char json[2048], path[256];
    int n = snprintf(json, sizeof(json),
        "{\n\"schema\":1,\"build\":\"capture-v1\",\"source_hash\":\"%s\",\"title\":\"%s\",\"sample\":%u,\n"
        "\"width\":%u,\"height\":1080,\"source_width\":1920,\"source_height\":1080,\"crop_x\":0,\"crop_y\":0,\n"
        "\"encoding\":\"rgba16le\",\"format\":%u,\"tiling\":%u,\"pitch\":1920,\"plane_bytes\":%zu,\n"
        "\"frames\":[%u,%u,%u],\"buffers\":[%u,%u,%u],\"submit_us\":[%llu,%llu,%llu],\n"
        "\"timing_valid\":true,\"timing_domain\":\"cpu_submit_proxy\",\"history\":\"cold\",\"phase_q16\":32768,\"generated_flipped\":false,\n"
        "\"profile\":{\"preset\":0,\"sharpness\":%d,\"enhance\":%d,\"hud_protect\":%d,\"hud_rect_packed\":%u}\n}\n",
        VCFG_BENCH_SOURCE_HASH, g_title_id, g_bench_saved, g_bench_width, g_bench_attr.format, g_bench_attr.tilingMode, g_bench_plane,
        g_bench_frames[0], g_bench_frames[1], g_bench_frames[2], g_bench_indices[0], g_bench_indices[1], g_bench_indices[2],
        (unsigned long long)g_bench_times[0], (unsigned long long)g_bench_times[1], (unsigned long long)g_bench_times[2],
        g_bench_profile.sharpness, g_bench_profile.upscale, g_bench_profile.hud_protect, g_bench_hud);
    if (n <= 0 || (size_t)n >= sizeof(json)) { bench_stop("metadata overflow"); return; }
    snprintf(path, sizeof(path), "%s/bench_%04u.json", g_bench_dir, g_bench_saved);
    if (!bench_write(path, json, (size_t)n)) { bench_stop("metadata write failed"); return; }
    g_bench_saved++;
    lg("BENCH saved %u/%u dir=%s\n", g_bench_saved, g_bench_limit, g_bench_dir);
    __atomic_store_n(&g_bench_state, g_bench_saved >= g_bench_limit ? BENCH_DONE : BENCH_IDLE, __ATOMIC_RELEASE);
}

static int32_t bench_flip(uint32_t count, void **dcb, uint32_t *sizes, void **ccb, uint32_t *csizes,
                          uint32_t handle, uint32_t index, uint32_t mode, int64_t arg, uint64_t now)
{
    uint32_t state = __atomic_load_n(&g_bench_state, __ATOMIC_ACQUIRE);
    if (g_flips == 0 || g_flips == 60 || g_flips == 119 || g_flips == 120) BLOG("bench_flip flips=%u state=%u started=%d", (unsigned)g_flips, state, g_bench_started);
    if (state >= BENCH_DONE || g_flips < 120) goto native;
    if (state == BENCH_WRITE) goto native;
    if (!g_bench_started) {
        g_bench_started = 1;
        BLOG("bench_allocate begin width=%u", g_bench_width);
        bench_allocate();
        BLOG("bench_allocate done surfaces=%p dump=%p state=%u", (void *)g_bench_surfaces, (void *)g_dump_mem, __atomic_load_n(&g_bench_state, __ATOMIC_ACQUIRE));
        if (__atomic_load_n(&g_bench_state, __ATOMIC_ACQUIRE) == BENCH_STOP) goto native;
        cfg_reload_from_disk(1); g_inject_on = 8; g_dump_req = 0;
        io_start(); gpu_init();
        int bench_ff = file_flags();  // -1 = no inject file (the normal case); only letters other than d/D are a problem
        BLOG("bench check: gpu_state=%d work=%p sh8=%d pack_off=%u dump=%p mode=%d preset=%d gv=%d flags=%d",
             (int)g_gpu_state, (void *)g_work, (int)g_sh8_ok, (unsigned)g_bench_pack_off, (void *)g_dump_mem,
             (int)g_cfg.mode, (int)g_cfg.preset, (int)g_cfg.game_vectors, bench_ff);
        if (g_gpu_state != 1 || !g_work || !g_sh8_ok || !g_bench_pack_off || !g_dump_mem ||
            g_cfg.mode != 8 || g_cfg.preset != 0 || g_cfg.game_vectors || (bench_ff > 0 && (bench_ff & ~48))) {
            bench_stop("resources unavailable or profile is not mode8/QUALITY/no-vectors"); goto native;
        }
        g_bench_profile = g_cfg;
        snprintf(g_bench_dir, sizeof(g_bench_dir), LOG_DIR "/bench_%s_%016llx", g_title_id,
                 (unsigned long long)sceKernelGetProcessTimeCounter());
        if (sceKernelMkdir(g_bench_dir, 0777) != 0) { bench_stop("unique run directory creation failed"); goto native; }
    }
    if (g_bench_wait_go && state == BENCH_IDLE) {
        if ((g_flips % 30u) == 0) {
            int gfd = sceKernelOpen(LOG_DIR "/bench_go", 0 /*O_RDONLY*/, 0);
            if (gfd >= 0) { sceKernelClose(gfd); g_bench_wait_go = 0; g_bench_next = g_flips; BLOG("bench_go seen at flip %u", (unsigned)g_flips); }
        }
        if (g_bench_wait_go) goto native;
    }
    if (state == BENCH_WAIT) {
        if (g_flips - g_bench_first > 600u) bench_stop("GPU/IO completion timeout; no buffer reuse");
        goto native;
    }
    if (!g_fmt_supported || g_bw != 1920 || g_bh != 1080 || g_pitch != 1920 || g_attr.tilingMode > 1 ||
        handle != (uint32_t)g_handle || index >= (uint32_t)g_ngame || mode != 1u) {
        static unsigned logged;
        if (logged++ < 8) BLOG("scanout mismatch state=%u fmt_ok=%d bw=%u bh=%u pitch=%u tiling=%u handle=%u/%u index=%u/%d mode=%u flips=%u",
                               state, (int)g_fmt_supported, (unsigned)g_bw, (unsigned)g_bh, (unsigned)g_pitch, (unsigned)g_attr.tilingMode,
                               handle, (unsigned)g_handle, index, (int)g_ngame, mode, (unsigned)g_flips);
        if (state == BENCH_IDLE) goto native;  // between triplets a odd flip (menu, loading, other mode) only postpones the next sample
        bench_stop("unsupported/reconfigured scanout mid-triplet"); goto native;
    }
    int is_30hz = bench_is_30hz_title();
    if (state == BENCH_IDLE && (g_flips < g_bench_next ||
        (is_30hz ? (g_ema_frame_us < 26000u || g_ema_frame_us > 48000u || g_osd_real_fps_x10 < 210u || g_osd_real_fps_x10 > 350u)
                 : (g_ema_frame_us > 18500u || g_osd_real_fps_x10 < 550u || g_osd_real_fps_x10 > 620u)))) goto native;
    if (!count || count > 6 || !dcb || !sizes) { bench_stop("unsupported submission shape"); goto native; }
    for (uint32_t i = 0; i < count; i++) {
        if (!dcb[i] || !sizes[i] || (sizes[i] & 3u) || sizes[i] > (16u << 20) || !dcb_mem_ok((uintptr_t)dcb[i], sizes[i])) {
            bench_stop("invalid DCB"); goto native;
        }
    }
    uint32_t last = count - 1u, nd = sizes[last] / 4u;
    const uint32_t *tail = nd >= 64 ? (const uint32_t *)dcb[last] + nd - 64u : NULL;
    if (!tail || tail[0] != 0xC03E1000u || (tail[1] >> 8) != 0x687507u ||
        (nd == 64 && ccb && csizes && ccb[last] && csizes[last])) {
        bench_stop("unknown flip tail / flip-only CCB"); goto native;
    }
    uint32_t slot = g_flips & 3u, tidx = g_attr.tilingMode == 0 ? 10u : 8u;
    unsigned stage = state == BENCH_IDLE ? 0u : state == BENCH_GT ? 1u : 2u;
    // Three private 8 KiB DCBs live after the export tables. They are never
    // reused while a triplet is on the GPU or owned by the IO worker.
    uint32_t *mine = (uint32_t *)((uint8_t *)g_bench_tables + BENCH_DCB_OFF + stage * BENCH_DCB_BYTES), *p = mine;
    uint64_t current = g_buf[index], saved_a = (uint64_t)(uintptr_t)g_bench_surfaces;
    uint64_t mid = saved_a + BENCH_SURFACE;
    if (state == BENCH_IDLE) {
        // Previous triplet is retired and acknowledged by IO before this reset.
        memset(g_work, 0, WORK_SZ);
        g_bench_first = g_flips; g_bench_attempt++; g_bench_cookie = g_bench_attempt;
        g_bench_next = g_flips + g_bench_interval;
        *(volatile uint64_t *)(g_gpu + DUMP_DONE_OFF) = 0;
        g_bench_timing = 1;
        g_bench_attr = g_attr; g_bench_hud = g_hud_packed;
    }
    g_bench_frames[stage] = g_flips; g_bench_indices[stage] = index; g_bench_times[stage] = now;
    uint64_t dt_min = is_30hz ? 26000u : 15000u, dt_max = is_30hz ? 48000u : 18500u;
    if (stage && (g_flips != g_bench_first + stage || now - g_bench_times[stage - 1u] < dt_min ||
                  now - g_bench_times[stage - 1u] > dt_max)) g_bench_timing = 0;
    if (stage == 2) {
        uint64_t d1 = g_bench_times[1] - g_bench_times[0], d2 = now - g_bench_times[1];
        uint64_t diff_max = is_30hz ? 5000u : 1500u, sum_min = is_30hz ? 54000u : 32000u, sum_max = is_30hz ? 95000u : 35000u;
        if ((d1 > d2 ? d1 - d2 : d2 - d1) > diff_max || d1 + d2 < sum_min || d1 + d2 > sum_max) g_bench_timing = 0;
    }
    // Same cache flush sequence as the normal appended mode8 DCB.
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x0000002E;
    *p++ = PKT3(0x46, 1, 0); *p++ = 0x00000407;
    *p++ = PKT3(0x58, 6, 0); *p++ = 0x82003FC0; *p++ = 1920u * 1088u * 4u / 256u;
    *p++ = 0; *p++ = (uint32_t)(current >> 8); *p++ = (uint32_t)(current >> 40); *p++ = 0x0A;
    p = bench_pack(p, current, stage, tidx);
    if (!stage) {
        uint32_t *tb = g_bench_tables + 128;
        memset(tb, 0, 32u * sizeof(uint32_t));
        make_t(tb + 8, current, 1920, 1080, 1920, tidx);
        make_t(tb + 16, saved_a, 1920, 1080, 1920, tidx);
        p = bench_pass(p, tb, g_sh8_off[16], PCOPY8_RSRC1, PCOPY8_RSRC2, 240, 135);
        uint32_t *py = g_bench_tables + 160;
        memset(py, 0, 140u * sizeof(uint32_t));
        fill_table8(py, saved_a, saved_a, mid, 1920, tidx, 0, 0, FG8_FLAG_PYR_WARM, g_work, g_mon, NULL);
        p = bench_pass(p, py, g_sh8_off[0], PYR8_RSRC1, PYR8_RSRC2, 30, 68);
        g_pyr_last_cur_base = saved_a; g_pyr_last_w = 1920; g_pyr_last_h = 1080; g_pyr_parity = 0;
    } else if (stage == 2) {
        g_bench_offscreen = 1;
        uint32_t n = build_dcb_flow8(p, saved_a, current, mid, mid, 1920, tidx, 1920u * 1088u * 4u,
                                   slot, 1, tail, (int32_t)handle, 0, 0, 32768, 0, 0, 0, 0, 0);
        g_bench_offscreen = 0;
        if (n < 64u || n * 4u > DCB8_SZ) { bench_stop("core DCB exceeded its budget"); goto native; }
        p += n - 64u;  // append export before the game's one original flip tail
        p = bench_pack(p, mid, 3, tidx);
        uint64_t done = (uint64_t)(uintptr_t)(g_gpu + DUMP_DONE_OFF);
        *p++ = PKT3(0x47, 5, 0); *p++ = 0x00000528; *p++ = (uint32_t)done;
        *p++ = 0x40000000u | ((uint32_t)(done >> 32) & 0xffffu); *p++ = g_bench_cookie; *p++ = 0;
    }
    memcpy(p, tail, 256); p += 64;
    uint32_t bytes = (uint32_t)(p - mine) * 4u;
    if (bytes > BENCH_DCB_BYTES) { bench_stop("DCB capacity exceeded"); goto native; }
    void *ds[7], *cs[7]; uint32_t dz[7], cz[7];
    for (uint32_t i = 0; i < count; i++) { ds[i] = dcb[i]; dz[i] = sizes[i]; cs[i] = ccb ? ccb[i] : NULL; cz[i] = csizes ? csizes[i] : 0; }
    uint32_t ncount = count;
    if (nd == 64) { ds[last] = mine; dz[last] = bytes; }
    else { dz[last] -= 256; ds[count] = mine; dz[count] = bytes; cs[count] = NULL; cz[count] = 0; ncount++; }
    // Drain the CPU's WC clear of motion history and publish descriptors/DCBs
    // before handing them to the GPU. This does not wait for GPU execution.
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
    int32_t r = HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
        int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
        ncount, ds, dz, ccb ? cs : NULL, csizes ? cz : NULL, handle, index, mode, arg);
    if (r) bench_stop("native submission failed; no buffer reuse");
    else __atomic_store_n(&g_bench_state, stage == 0 ? BENCH_GT : stage == 1 ? BENCH_B : BENCH_WAIT, __ATOMIC_RELEASE);
    return r;
native:
    return HOOK_CONTINUE(sceGnmSubmitAndFlipCommandBuffers,
        int32_t (*)(uint32_t, void **, uint32_t *, void **, uint32_t *, uint32_t, uint32_t, uint32_t, int64_t),
        count, dcb, sizes, ccb, csizes, handle, index, mode, arg);
}
