// VCFG launcher, console side: video output, controller, files. Drawing and screens live in ui.c / app.c.
// Settings files (read by the plugins at the next game start):
//   new engine:     /data/bb_probe/vcfg.ini              enabled, preset, sharp, enhance, overlay, opacity
//   classic engine: /data/bb_probe/config-<TITLE>.ini    framegen, preset, sharpness, enhance, overlay, menu_opacity
//   launcher:       /data/bb_probe/vcfg_launcher.ini     defaults and overlay style
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdarg.h>
#include <dirent.h>
#include <orbis/libkernel.h>
#include <orbis/VideoOut.h>
#include <orbis/Pad.h>
#include <orbis/UserService.h>
#include <orbis/Sysmodule.h>
#include <orbis/SystemService.h>
#include "app.h"
#include <GoldHEN.h>
#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_PNG
#define STBI_NO_STDIO_FAILURE_STRINGS
#include "../third_party/stb_image.h"

#define W 1920
#define H 1080
#define PLUG "/data/GoldHEN/plugins/"
#define INI "/data/GoldHEN/plugins.ini"
#define CFGDIR "/data/bb_probe"

typedef struct { const char *id, *prx; int engine, testing; } Supported;
// Titles verified on hardware (docs/COMPATIBILITY.md); the classic engine is the older v1.16 plugin line.
static const Supported k_sup[] = {
    { "CUSA07439", "vcfg7.prx", ENGINE_NEW, 0 },
    { "CUSA08519", "vcfg_classic8.prx", ENGINE_CLASSIC, 0 },
    { "CUSA00411", "vcfg_classic8.prx", ENGINE_CLASSIC, 0 },
    { "CUSA00207", "vcfg_classic8.prx", ENGINE_CLASSIC_BB, 0 },
};
#define NSUP (int)(sizeof(k_sup) / sizeof(k_sup[0]))
static const char *k_files[] = { "vcfg7.prx", "vcfg_classic8.prx" };
static const char *k_exp_prx = "vcfg_classic8.prx";   // engine for experimental (not verified) titles

static FILE *g_log;
static void vlog(const char *fmt, ...)
{
    if (!g_log) return;
    va_list a; va_start(a, fmt); vfprintf(g_log, fmt, a); va_end(a); fflush(g_log);
}
static unsigned char *read_all(const char *path, size_t *n)
{
    *n = 0; FILE *f = fopen(path, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); long len = ftell(f); rewind(f);
    if (len < 0 || len > (48L << 20)) { fclose(f); return NULL; }
    unsigned char *b = malloc((size_t)len + 1); if (!b) { fclose(f); return NULL; }
    size_t got = fread(b, 1, (size_t)len, f); fclose(f);
    if (got != (size_t)len) { free(b); return NULL; }
    b[len] = 0; *n = (size_t)len; return b;
}
static int write_all(const char *path, const void *d, size_t n)
{
    FILE *f = fopen(path, "wb"); if (!f) return -1;
    size_t w = fwrite(d, 1, n, f); int c = fclose(f); return (w == n && !c) ? 0 : -1;
}
static int write_verified(const char *path, const void *d, size_t n)
{
    char tmp[256]; snprintf(tmp, sizeof(tmp), "%s.vcfg_tmp", path);
    if (write_all(tmp, d, n)) return -1;
    size_t m = 0; unsigned char *b = read_all(tmp, &m); int ok = b && m == n && !memcmp(b, d, n); free(b);
    if (!ok || rename(tmp, path)) { remove(tmp); return -1; }
    return 0;
}

// ---- game metadata from the console (title from param.sfo, icon0.png) ----
static int sfo_title(const unsigned char *b, size_t n, char *out, size_t on)
{
    if (n < 20 || memcmp(b, "\0PSF", 4)) return 0;
    uint32_t keys = *(const uint32_t *)(b + 8), data = *(const uint32_t *)(b + 12), cnt = *(const uint32_t *)(b + 16);
    for (uint32_t i = 0; i < cnt && 20 + i * 16 + 16 <= n; i++) {
        const unsigned char *e = b + 20 + i * 16;
        uint16_t ko = *(const uint16_t *)e; uint32_t len = *(const uint32_t *)(e + 4), doff = *(const uint32_t *)(e + 12);
        if (keys + ko >= n || data + doff + len > n) continue;
        if (!strcmp((const char *)b + keys + ko, "TITLE")) {
            size_t k = 0; for (; k + 1 < on && k < len && b[data + doff + k]; k++) out[k] = (char)b[data + doff + k];
            out[k] = 0; return k > 0;
        }
    }
    return 0;
}
// The app sandbox cannot read these folders (drwx------ root); main() wraps the calls in a GoldHEN jailbreak.
static void load_meta(Game *g)
{
    char p[128]; size_t n = 0;
    snprintf(p, sizeof(p), "/system_data/priv/appmeta/%s/param.sfo", g->id);
    unsigned char *b = read_all(p, &n);
    if (!b) { snprintf(p, sizeof(p), "/user/appmeta/%s/param.sfo", g->id); b = read_all(p, &n); }
    if (b) { g->installed = 1; sfo_title(b, n, g->title, sizeof(g->title)); free(b); }
    snprintf(p, sizeof(p), "/user/appmeta/%s/icon0.png", g->id);
    int w, h, ch; unsigned char *img = stbi_load(p, &w, &h, &ch, 4);
    if (img) { g->icon = img; g->iw = w; g->ih = h; g->installed = 1; }
    if (!g->installed) { snprintf(p, sizeof(p), "/user/app/%s/app.json", g->id); FILE *f = fopen(p, "rb"); if (f) { fclose(f); g->installed = 1; } }
    vlog("game %s installed=%d title='%s' icon=%dx%d\n", g->id, g->installed, g->title, g->iw, g->ih);
}

// ---- settings files ----
static int ini_int(const char *text, const char *key, int def)
{
    size_t kl = strlen(key);
    for (const char *p = text; p && *p; ) {
        while (*p == ' ' || *p == '\t') p++;
        if (!strncmp(p, key, kl)) { const char *q = p + kl; while (*q == ' ') q++; if (*q == '=') return atoi(q + 1); }
        p = strchr(p, '\n'); if (p) p++;
    }
    return def;
}
static void load_settings(App *a)
{
    size_t n; char path[96];
    unsigned char *l = read_all(CFGDIR "/vcfg_launcher.ini", &n);
    const char *t = l ? (const char *)l : "";
    a->defaults = (Settings){ ini_int(t, "fg", 1), ini_int(t, "preset", 0), ini_int(t, "sharp", 40), ini_int(t, "enhance", 1), ini_int(t, "overlay", 0) };
    a->overlay_pos = ini_int(t, "overlay_pos", 4); a->overlay_opacity = ini_int(t, "overlay_opacity", 80);
    free(l);
    for (int i = 0; i < a->ngames; i++) {
        Game *g = &a->games[i]; g->s = a->defaults; if (g->testing || g->experimental) g->s.fg = 0;
        if (g->engine == ENGINE_NEW) {
            unsigned char *b = read_all(CFGDIR "/vcfg.ini", &n); if (!b) continue;
            const char *s = (const char *)b;
            g->s = (Settings){ ini_int(s, "enabled", 1), ini_int(s, "preset", 0), ini_int(s, "sharp", 40), ini_int(s, "enhance", 1), ini_int(s, "overlay", 0) };
            free(b);
        } else {
            snprintf(path, sizeof(path), CFGDIR "/config-%s.ini", g->id);
            unsigned char *b = read_all(path, &n); if (!b) continue;
            const char *s = (const char *)b;
            g->s = (Settings){ ini_int(s, "framegen", g->experimental ? 0 : 1), ini_int(s, "preset", 0), ini_int(s, "sharpness", 40), ini_int(s, "enhance", 1), ini_int(s, "overlay", 0) };
            free(b);
        }
    }
}
static void save_settings(App *a)
{
    char buf[512], path[96];
    sceKernelMkdir(CFGDIR, 0777);
    snprintf(buf, sizeof(buf), "fg=%d\npreset=%d\nsharp=%d\nenhance=%d\noverlay=%d\noverlay_pos=%d\noverlay_opacity=%d\n",
             a->defaults.fg, a->defaults.preset, a->defaults.sharp, a->defaults.enhance, a->defaults.overlay, a->overlay_pos, a->overlay_opacity);
    write_all(CFGDIR "/vcfg_launcher.ini", buf, strlen(buf));
    for (int i = 0; i < a->ngames; i++) {
        Game *g = &a->games[i]; Settings *s = &g->s;
        if (g->engine == ENGINE_NEW) {
            snprintf(buf, sizeof(buf), "enabled=%d\npreset=%d\nsharp=%d\nenhance=%d\noverlay=%d\nopacity=%d\n",
                     s->fg, s->preset, s->sharp, s->enhance, s->overlay, a->overlay_opacity);
            write_all(CFGDIR "/vcfg.ini", buf, strlen(buf));
        } else {
            snprintf(path, sizeof(path), CFGDIR "/config-%s.ini", g->id);
            snprintf(buf, sizeof(buf), "framegen=%d\npreset=%d\nsharpness=%d\nenhance=%d\noverlay=%d\nmenu_opacity=%d\n",
                     s->fg, s->preset, s->sharp, s->enhance, s->overlay, a->overlay_opacity);
            write_all(path, buf, strlen(buf));
        }
    }
    vlog("settings saved\n");
}

// ---- plugins.ini: point our titles at our files; keep foreign lines; [default] stays last ----
// Verified titles always get their plugin. Experimental titles get the classic engine only while switched on;
// switched off, their section is dropped when nothing else is left in it (an empty section would block [default]).
typedef struct { char id[12]; const char *prx; int keep_empty; } Map;
static Map g_map[48]; static int g_nmap;
static void build_map(const App *a)
{
    g_nmap = 0;
    for (int t = 0; t < NSUP; t++) {
        Map *m = &g_map[g_nmap++]; snprintf(m->id, sizeof(m->id), "%s", k_sup[t].id);
        m->prx = k_sup[t].testing ? NULL : k_sup[t].prx; m->keep_empty = 1;
    }
    for (int i = 0; i < a->ngames && g_nmap < 48; i++) {
        const Game *g = &a->games[i]; if (!g->experimental) continue;
        Map *m = &g_map[g_nmap++]; snprintf(m->id, sizeof(m->id), "%s", g->id);
        m->prx = g->s.fg ? k_exp_prx : NULL; m->keep_empty = 0;
    }
}
static int ours(const char *l) { return strstr(l, PLUG "vcfg") || strstr(l, PLUG "bb_probe"); }
static int map_of(const char *hdr)
{
    for (int t = 0; t < g_nmap; t++) { char h[24]; snprintf(h, sizeof(h), "[%s]", g_map[t].id); if (!strncmp(hdr, h, strlen(h))) return t; }
    return -1;
}
typedef struct { char *o; size_t n, cap; int bad; } Out;
static void emit(Out *w, const char *s)
{
    size_t L = strlen(s); if (w->bad || w->n + L + 2 >= w->cap) { w->bad = 1; return; }
    memcpy(w->o + w->n, s, L); w->n += L;
}
static void emit_new(Out *w, int t)
{
    char b[128]; if (!g_map[t].prx && !g_map[t].keep_empty) return;
    if (g_map[t].prx) snprintf(b, sizeof(b), "[%s]\n" PLUG "%s\n", g_map[t].id, g_map[t].prx); else snprintf(b, sizeof(b), "[%s]\n", g_map[t].id);
    emit(w, b);
}
typedef struct { char hdr[512]; char body[8192]; size_t bn; int have, cur; } Section;
static void sec_add(Section *s, const char *line)
{
    size_t l = strlen(line); if (s->bn + l + 1 < sizeof(s->body)) { memcpy(s->body + s->bn, line, l); s->bn += l; s->body[s->bn++] = '\n'; }
}
// Writes the buffered section: header + kept lines (+ our line); dropped when it ended up empty and may go.
static void sec_flush(Out *w, Section *s, int install, int *done)
{
    if (s->have) {
        int dup = s->cur >= 0 && done[s->cur];   // already written before [default]
        if (s->cur >= 0 && install && g_map[s->cur].prx && !dup) { char b[96]; snprintf(b, sizeof(b), PLUG "%s", g_map[s->cur].prx); sec_add(s, b); }
        if (!(s->cur >= 0 && !s->bn && (dup || !g_map[s->cur].keep_empty))) { emit(w, s->hdr); emit(w, "\n"); s->body[s->bn] = 0; emit(w, s->body); }
        if (s->cur >= 0) done[s->cur] = 1;
    }
    s->have = 0; s->bn = 0; s->cur = -1;
}
static char *ini_update(const char *old, int install, size_t *outn)
{
    size_t cap = strlen(old) + 8192;
    Out w = { malloc(cap), 0, cap, 0 }; if (!w.o) return NULL;
    static Section sec; memset(&sec, 0, sizeof(sec)); sec.cur = -1;
    int done[48] = {0}, defaults_seen = 0; char line[512];
    for (const char *p = old; *p; ) {
        size_t L = strcspn(p, "\r\n"); if (L >= sizeof(line)) L = sizeof(line) - 1;
        memcpy(line, p, L); line[L] = 0; p += strcspn(p, "\r\n"); while (*p == '\r' || *p == '\n') p++;
        char *s = line; while (*s == ' ' || *s == '\t') s++;
        if (!*s) continue;
        if (*s == '[') {
            sec_flush(&w, &sec, install, done);
            if (install && !strncmp(s, "[default]", 9) && !defaults_seen) {
                defaults_seen = 1;
                for (int t = 0; t < g_nmap; t++) if (!done[t]) { emit_new(&w, t); done[t] = 1; }
            }
            snprintf(sec.hdr, sizeof(sec.hdr), "%s", line); sec.have = 1; sec.cur = map_of(s);
            continue;
        }
        if (sec.cur >= 0 && ours(s)) continue;
        if (!sec.have) { emit(&w, line); emit(&w, "\n"); continue; }
        sec_add(&sec, line);
    }
    sec_flush(&w, &sec, install, done);
    if (install) for (int t = 0; t < g_nmap; t++) if (!done[t]) emit_new(&w, t);
    if (w.bad) { free(w.o); return NULL; }
    w.o[w.n] = 0; *outn = w.n; return w.o;
}
static void addlog(App *a, const char *msg)
{
    if (a->nlog == 6) { memmove(a->log[0], a->log[1], sizeof(a->log[0]) * 5); a->nlog = 5; }
    snprintf(a->log[a->nlog++], sizeof(a->log[0]), "%s", msg); vlog("%s\n", msg);
}
static int plugin_state(void)
{
    int st = 1;
    for (int i = 0; i < (int)(sizeof(k_files) / sizeof(k_files[0])); i++) {
        char src[96], dst[96]; size_t n1, n2;
        snprintf(src, sizeof(src), "/app0/plugins/%s", k_files[i]); snprintf(dst, sizeof(dst), PLUG "%s", k_files[i]);
        unsigned char *a = read_all(src, &n1), *b = read_all(dst, &n2);
        if (!b) st = 0; else if (st && (!a || n1 != n2 || memcmp(a, b, n1))) st = 2;
        free(a); free(b);
        if (!st) break;
    }
    size_t n; unsigned char *ini = read_all(INI, &n);
    if (st == 1 && (!ini || !strstr((char *)ini, PLUG "vcfg7.prx"))) st = 2;
    free(ini);
    return st;
}
static int do_ini(App *a, int install)
{
    size_t n = 0; unsigned char *ini = read_all(INI, &n);
    if (!ini) { ini = (unsigned char *)strdup(""); n = 0; }
    else { char bk[128]; snprintf(bk, sizeof(bk), INI ".vcfg_backup_%lld", (long long)time(NULL)); if (write_all(bk, ini, n)) { free(ini); addlog(a, "CANNOT BACK UP PLUGINS.INI"); return -1; } }
    build_map(a);
    size_t un = 0; char *u = ini_update((char *)ini, install, &un); free(ini);
    if (!u) { addlog(a, "PLUGINS.INI TOO LARGE"); return -1; }
    int rc = write_verified(INI, u, un); vlog("plugins.ini rc=%d\n%s\n", rc, u); free(u);
    addlog(a, rc ? "CANNOT WRITE PLUGINS.INI" : (install ? "PLUGINS.INI BACKED UP, GAMES MAPPED" : "VCFG REMOVED FROM PLUGINS.INI"));
    return rc;
}
static void do_install(App *a)
{
    sceKernelMkdir("/data/GoldHEN/plugins", 0777); sceKernelMkdir(CFGDIR, 0777);
    for (int i = 0; i < (int)(sizeof(k_files) / sizeof(k_files[0])); i++) {
        char src[96], dst[96], m[96]; size_t n;
        snprintf(src, sizeof(src), "/app0/plugins/%s", k_files[i]); snprintf(dst, sizeof(dst), PLUG "%s", k_files[i]);
        unsigned char *b = read_all(src, &n);
        if (!b) { snprintf(m, sizeof(m), "MISSING IN PACKAGE: %s", k_files[i]); addlog(a, m); return; }
        int rc = write_verified(dst, b, n); free(b);
        snprintf(m, sizeof(m), rc ? "CANNOT WRITE %s" : "COPIED %s (%zu KB)", k_files[i], n / 1024); addlog(a, m);
        if (rc) return;
    }
    do_ini(a, 1);
    a->plugin_state = plugin_state();
}

// Experimental titles live in plugins.ini only while switched on: rewrite it when a switch changed.
static uint64_t exp_sig(const App *a)
{
    uint64_t h = 1469598103934665603ull;
    for (int i = 0; i < a->ngames; i++)
        if (a->games[i].experimental) { h ^= (uint64_t)(a->games[i].s.fg != 0) + (uint64_t)i * 31u; h *= 1099511628211ull; }
    return h;
}
static uint64_t g_exp_written;
static void remap_if_changed(App *a)
{
    if (exp_sig(a) == g_exp_written) return;
    if (!do_ini(a, 1)) g_exp_written = exp_sig(a);
}

// ---- video ----
static int g_vo = -1; static void *g_fb[2]; static OrbisKernelEqueue g_q; static int g_cur;
static int video_init(void)
{
    g_vo = sceVideoOutOpen(0xff, 0, 0, NULL); if (g_vo < 0) { vlog("VideoOutOpen 0x%08x\n", g_vo); return -1; }
    size_t sz = 0x800000; off_t phys;
    for (int i = 0; i < 2; i++) {
        if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), sz, 0x200000, 3, &phys)) return -2;
        if (sceKernelMapDirectMemory(&g_fb[i], sz, 0x33, 0, phys, 0x200000)) return -3;
        memset(g_fb[i], 0, sz);
    }
    OrbisVideoOutBufferAttribute attr; memset(&attr, 0, sizeof(attr));
    sceVideoOutSetBufferAttribute(&attr, 0x80000000, 1 /*linear*/, 0, W, H, W);
    if (sceVideoOutRegisterBuffers(g_vo, 0, g_fb, 2, &attr) < 0) return -4;
    sceKernelCreateEqueue(&g_q, "vcfg_flip"); sceVideoOutAddFlipEvent(g_q, g_vo, NULL);
    sceVideoOutSetFlipRate(g_vo, 0);
    return 0;
}
static void present(const uint32_t *px)
{
    memcpy(g_fb[g_cur], px, (size_t)W * H * 4);
    sceVideoOutSubmitFlip(g_vo, g_cur, 1, 0);
    OrbisKernelEvent ev; int n = 0; OrbisKernelUseconds to = 100000; sceKernelWaitEqueue(g_q, &ev, 1, &n, &to);
    g_cur ^= 1;
}

static uint8_t *font_file(const char *name)
{
    char p[128]; size_t n; snprintf(p, sizeof(p), "/app0/assets/fonts/%s", name);
    uint8_t *b = read_all(p, &n); if (!b) vlog("font missing %s\n", p); return b;
}
static void quit(void)
{
    if (g_log) { fclose(g_log); g_log = NULL; }
    if (sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_SYSTEM_SERVICE) == 0) sceSystemServiceLoadExec("exit", NULL);
    for (;;) sceKernelUsleep(1000000);
}

int main(void)
{
    sceKernelMkdir(CFGDIR, 0777);
    g_log = fopen("/data/vcfg_launcher.log", "w");
    vlog("VCFG launcher start\n");
    static Fonts fonts; static App app; App *a = &app; a->f = &fonts;
    Arena arena = { malloc(24 << 20), 24 << 20, 0 };
    uint8_t *disp = font_file("InterDisplay-Medium.ttf"), *text = font_file("Inter-Medium.ttf"), *mono = font_file("JetBrainsMono-Medium.ttf");
    if (!arena.base || !disp || !text || !mono || !face_init(&fonts.logo, disp, 124, &arena) || !face_init(&fonts.title, text, 37, &arena) ||
        !face_init(&fonts.mono, mono, 25, &arena) || !face_init(&fonts.num, disp, 80, &arena) || !face_init(&fonts.big, disp, 150, &arena)) {
        vlog("init failed\n"); quit();
    }
    struct jailbreak_backup jb; memset(&jb, 0, sizeof(jb));
    int jrc = sys_sdk_jailbreak(&jb);           // fonts are loaded already: /app0 is not reachable while jailbroken
    vlog("jailbreak rc=%d\n", jrc);
    for (int i = 0; i < NSUP; i++) {
        Game *g = &a->games[a->ngames++];
        snprintf(g->id, sizeof(g->id), "%s", k_sup[i].id); g->engine = k_sup[i].engine; g->testing = k_sup[i].testing;
        load_meta(g);
    }
    // Every other installed game is listed as experimental (switched off until the user turns it on).
    DIR *dir = opendir("/user/app");
    if (dir) {
        struct dirent *e;
        while ((e = readdir(dir)) && a->ngames < (int)(sizeof(a->games) / sizeof(a->games[0]))) {
            if (strncmp(e->d_name, "CUSA", 4) || strlen(e->d_name) != 9) continue;
            int known = 0; for (int i = 0; i < NSUP; i++) if (!strcmp(k_sup[i].id, e->d_name)) known = 1;
            if (known) continue;
            Game *g = &a->games[a->ngames++]; memset(g, 0, sizeof(*g));
            snprintf(g->id, sizeof(g->id), "%s", e->d_name); g->engine = ENGINE_CLASSIC; g->experimental = 1;
            load_meta(g);
            if (!g->installed) a->ngames--;
        }
        closedir(dir);
    } else vlog("cannot list /user/app\n");
    if (jrc == 0) vlog("unjailbreak rc=%d\n", sys_sdk_unjailbreak(&jb));
    // Installed games first, keep the table order otherwise.
#define RANK(g) ((g).installed * 2 + !(g).experimental)   // verified first, installed first
    for (int i = 1; i < a->ngames; i++) for (int j = i; j > 0 && RANK(a->games[j]) > RANK(a->games[j - 1]); j--) {
        Game t = a->games[j]; a->games[j] = a->games[j - 1]; a->games[j - 1] = t;
    }
    load_settings(a);
    g_exp_written = exp_sig(a);
    a->plugin_state = plugin_state();
    if (a->plugin_state != 1) { addlog(a, a->plugin_state ? "UPDATING PLUGINS" : "INSTALLING PLUGINS"); do_install(a); }

    if (video_init()) { vlog("video init failed\n"); quit(); }
    int uid = 0; sceUserServiceInitialize(NULL); sceUserServiceGetInitialUser(&uid);
    scePadInit(); int pad = scePadOpen(uid, 0, 0, NULL);
    vlog("pad=%d uid=%d\n", pad, uid);

    uint32_t *back = malloc((size_t)W * H * 4); Canvas c = { back, W, H, W };
    uint32_t prev = 0; uint64_t hold_t = 0; int redraw = 1;
    for (;;) {
        OrbisPadData d; memset(&d, 0, sizeof(d));
        uint32_t btn = (pad >= 0 && scePadReadState(pad, &d) == 0 && d.connected) ? d.buttons : 0;
        uint32_t edge = btn & ~prev;
        uint64_t now = sceKernelGetProcessTime();
        uint32_t dirs = btn & (PAD_UP | PAD_DOWN | PAD_LEFT | PAD_RIGHT);
        if (edge & dirs) hold_t = now + 380000;
        else if (dirs && now > hold_t) { edge |= dirs; hold_t = now + 110000; }   // key repeat
        prev = btn;
        if (edge) {
            int act = app_input(a, edge); redraw = 1;
            if (act == ACT_INSTALL) do_install(a);
            else if (act == ACT_REMOVE) { do_ini(a, 0); a->plugin_state = plugin_state(); }
            else if (act == ACT_SAVE) { save_settings(a); remap_if_changed(a); }
            else if (act == ACT_SAVE_EXIT) {
                save_settings(a); remap_if_changed(a);
                snprintf(a->status, sizeof(a->status), "SAVED. START A GAME, HOLD L3+R3 FOR 3 S FOR THE MENU.");
                app_draw(&c, a); present(back); sceKernelUsleep(900000); quit();
            }
        }
        if (redraw) { app_draw(&c, a); present(back); redraw = 0; }
        else sceKernelUsleep(16000);
    }
}
