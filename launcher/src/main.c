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
#include <errno.h>
#include <orbis/libkernel.h>
#include <orbis/VideoOut.h>
#include <orbis/Pad.h>
#include <orbis/UserService.h>
#include <orbis/Sysmodule.h>
#include <orbis/SystemService.h>
#include "app.h"
#include "../../overlay/menu_combo.h"
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
    { "CUSA07439", "vcfg16.prx", ENGINE_NEW, 0 },
    { "CUSA08519", "vcfg_classic42.prx", ENGINE_CLASSIC, 0 },
    { "CUSA00411", "vcfg_classic42.prx", ENGINE_CLASSIC, 0 },
    { "CUSA00207", "vcfg_classic42.prx", ENGINE_CLASSIC_BB, 0 },
};
#define NSUP (int)(sizeof(k_sup) / sizeof(k_sup[0]))
static const char *k_files[] = { "vcfg16.prx", "vcfg_classic42.prx", "vcfg_alt_02.prx" };
// Double-buffered titles that run best with the 2-BUFFER engine (virtual flip queue, tested on hardware 20.10): AUTO = 2-BUFFER.
static const char *const k_auto_2buf[] = { "CUSA05571", "CUSA08809" };
static int auto_2buf(const char *id)
{
    for (unsigned i = 0; i < sizeof(k_auto_2buf) / sizeof(k_auto_2buf[0]); i++) if (!strcmp(id, k_auto_2buf[i])) return 1;
    return 0;
}
static const char *const k_auto_alt[] = { "CUSA17419", "CUSA00918", "CUSA11456" };
static int auto_alt(const char *id)
{
    for (unsigned i = 0; i < sizeof(k_auto_alt) / sizeof(k_auto_alt[0]); i++) if (!strcmp(id, k_auto_alt[i])) return 1;
    return 0;
}
#define MAPMAX 176   // plugins.ini sections we manage: the verified table + every game in the list (App.games)
#define ENGFILE CFGDIR "/vcfg_engines.ini"   // the player's engine choice per game: CUSAxxxxx=<SEL_*>, AUTO not written
static const char *prx_of(int engine)
{
    if (engine == ENGINE_NEW) return k_files[0];
    if (engine == ENGINE_CLASSIC_ALT) return k_files[2];
    return k_files[1];
}
static const Game *find_game(const App *a, const char *id)
{
    for (int i = 0; i < a->ngames; i++) if (!strcmp(a->games[i].id, id)) return &a->games[i];
    return NULL;
}
// The new engine keeps its settings in one vcfg.ini (its verified title); any other game uses config-<TITLE>.ini.
static int uses_vcfg_ini(const Game *g) { return g->engine == ENGINE_NEW && game_engine(g) == ENGINE_NEW; }

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
static int sfo_str(const unsigned char *b, size_t n, const char *key, char *out, size_t on)
{
    if (n < 20 || memcmp(b, "\0PSF", 4)) return 0;
    uint32_t keys = *(const uint32_t *)(b + 8), data = *(const uint32_t *)(b + 12), cnt = *(const uint32_t *)(b + 16);
    for (uint32_t i = 0; i < cnt && 20 + i * 16 + 16 <= n; i++) {
        const unsigned char *e = b + 20 + i * 16;
        uint16_t ko = *(const uint16_t *)e; uint32_t len = *(const uint32_t *)(e + 4), doff = *(const uint32_t *)(e + 12);
        if (keys + ko >= n || data + doff + len > n) continue;
        if (!strcmp((const char *)b + keys + ko, key)) {
            size_t k = 0; for (; k + 1 < on && k < len && b[data + doff + k]; k++) out[k] = (char)b[data + doff + k];
            out[k] = 0; return k > 0;
        }
    }
    return 0;
}
// The app sandbox cannot read these folders (drwx------ root); main() wraps the calls in a GoldHEN jailbreak.
static const char *const k_roots[] = { "", "/mnt/ext0", "/mnt/ext1", "/mnt/ext2", "/mnt/ext3" };   // internal drive, then extended storage (external HDD)
#define NROOT ((int)(sizeof(k_roots) / sizeof(k_roots[0])))
// What the game scan saw on each root, for the bug report (an external drive that cannot be listed, a full list).
typedef struct { int listed, err, found, added, known, not_installed, full; } ScanRoot;
static ScanRoot g_scan[NROOT];
// Where a title's icon can be: appmeta (all internal titles), the title's own sce_sys on its drive, an installed patch.
static const char *const k_icon_tpl[] = { "%s/user/appmeta/%s/icon0.png", "%s/user/appmeta/%s/icon0_08.png", "%s/user/app/%s/sce_sys/icon0.png",
                                          "%s/user/app/%s/icon0.png", "%s/user/patch/%s/sce_sys/icon0.png", "%s/system_data/priv/appmeta/%s/icon0.png" };
// Debug log aid: what is in a folder (names only), to learn the layout of extended storage from a tester's log.
static void vlog_dir(const char *path)
{
    DIR *d = opendir(path);
    if (!d) { vlog("  ls %s: cannot open\n", path); return; }
    char line[400]; int len = snprintf(line, sizeof(line), "  ls %s:", path); int cnt = 0; struct dirent *e;
    while ((e = readdir(d)) && cnt < 24) {
        if (e->d_name[0] == '.') continue;
        len += snprintf(line + len, sizeof(line) - (size_t)len, " %s", e->d_name); cnt++;
        if (len > (int)sizeof(line) - 40) break;
    }
    closedir(d); vlog("%s\n", line);
}
static void load_meta(Game *g)
{
    char p[160]; size_t n = 0; unsigned char *b = NULL;
    for (int r = 0; r < NROOT && !b; r++) {
        snprintf(p, sizeof(p), "%s/user/appmeta/%s/param.sfo", k_roots[r], g->id); b = read_all(p, &n);
    }
    if (!b) { snprintf(p, sizeof(p), "/system_data/priv/appmeta/%s/param.sfo", g->id); b = read_all(p, &n); }
    if (b) { g->installed = 1; sfo_str(b, n, "TITLE", g->title, sizeof(g->title)); sfo_str(b, n, "APP_VER", g->version, sizeof(g->version)); free(b); }
    for (int r = 0; r < NROOT; r++) {   // an installed update has the newer APP_VER
        snprintf(p, sizeof(p), "%s/user/patch/%s/sce_sys/param.sfo", k_roots[r], g->id);
        if ((b = read_all(p, &n))) { sfo_str(b, n, "APP_VER", g->version, sizeof(g->version)); free(b); break; }
    }
    for (int r = 0; r < NROOT && !g->icon; r++) {
        for (int t = 0; t < (int)(sizeof(k_icon_tpl) / sizeof(k_icon_tpl[0])) && !g->icon; t++) {
            if (r && strstr(k_icon_tpl[t], "system_data")) continue;     // internal only
            snprintf(p, sizeof(p), k_icon_tpl[t], k_roots[r], g->id);
            int w, h, ch; unsigned char *img = stbi_load(p, &w, &h, &ch, 4);
            if (img) { g->icon = img; g->iw = w; g->ih = h; g->installed = 1; vlog("game %s icon from %s\n", g->id, p); }
        }
    }
    if (!g->icon) {   // no picture anywhere: record the folder layout for the log (load_meta runs once per title)
        for (int r = 0; r < NROOT; r++) {
            char d[160];
            snprintf(d, sizeof(d), "%s/user", k_roots[r]); vlog_dir(d);
            snprintf(d, sizeof(d), "%s/user/appmeta/%s", k_roots[r], g->id); vlog_dir(d);
            snprintf(d, sizeof(d), "%s/user/app/%s", k_roots[r], g->id); vlog_dir(d);
        }
    }
    for (int r = 0; r < NROOT && !g->installed; r++) {
        snprintf(p, sizeof(p), "%s/user/app/%s/app.json", k_roots[r], g->id); FILE *f = fopen(p, "rb"); if (f) { fclose(f); g->installed = 1; }
    }
    // Last resort: a settings file (only written for installed games, see save_settings).
    if (!g->installed) { snprintf(p, sizeof(p), CFGDIR "/config-%s.ini", g->id); FILE *f = fopen(p, "rb"); if (f) { fclose(f); g->installed = 1; } }
    vlog("game %s installed=%d version=%s title='%s' icon=%dx%d\n", g->id, g->installed, g->version, g->title, g->iw, g->ih);
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
    a->menu_combo = menu_combo_sanitize((uint32_t)ini_int(t, "menu_combo", (int)MENU_COMBO_DEFAULT));   // preset or recorded mask, else L3 + R3
    free(l);
    unsigned char *e = read_all(ENGFILE, &n);
    for (int i = 0; i < a->ngames; i++) {
        int v = e ? ini_int((const char *)e, a->games[i].id, SEL_AUTO) : SEL_AUTO;
        a->games[i].eng_sel = (v >= 0 && v < SEL_COUNT) ? v : SEL_AUTO;
    }
    free(e);
    for (int i = 0; i < a->ngames; i++) {
        Game *g = &a->games[i]; g->s = a->defaults; if (g->testing || g->experimental) g->s.fg = 0;
        if (uses_vcfg_ini(g)) {
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
    const unsigned combo = menu_combo_sanitize(a->menu_combo);   // validated mask, written to every file the plugins read
    snprintf(buf, sizeof(buf), "fg=%d\npreset=%d\nsharp=%d\nenhance=%d\noverlay=%d\noverlay_pos=%d\noverlay_opacity=%d\nmenu_combo=%u\n",
             a->defaults.fg, a->defaults.preset, a->defaults.sharp, a->defaults.enhance, a->defaults.overlay, a->overlay_pos, a->overlay_opacity, combo);
    write_all(CFGDIR "/vcfg_launcher.ini", buf, strlen(buf));
    {
        static char eb[MAPMAX * 16]; size_t en = 0;
        for (int i = 0; i < a->ngames; i++)
            if (a->games[i].eng_sel != SEL_AUTO && en + 16 < sizeof(eb))
                en += (size_t)snprintf(eb + en, sizeof(eb) - en, "%s=%d\n", a->games[i].id, a->games[i].eng_sel);
        if (en) write_all(ENGFILE, eb, en); else remove(ENGFILE);
    }
    for (int i = 0; i < a->ngames; i++) {
        Game *g = &a->games[i]; Settings *s = &g->s;
        if (!g->installed) continue;
        if (uses_vcfg_ini(g)) {
            snprintf(buf, sizeof(buf), "enabled=%d\npreset=%d\nsharp=%d\nenhance=%d\noverlay=%d\nopacity=%d\nmenu_combo=%u\n",
                     s->fg, s->preset, s->sharp, s->enhance, s->overlay, a->overlay_opacity, combo);
            write_all(CFGDIR "/vcfg.ini", buf, strlen(buf));
        } else {
            snprintf(path, sizeof(path), CFGDIR "/config-%s.ini", g->id);
            snprintf(buf, sizeof(buf), "framegen=%d\npreset=%d\nsharpness=%d\nenhance=%d\noverlay=%d\nmenu_opacity=%d\nmenu_combo=%u\ntwo_buffer=%d\n",
                     s->fg, s->preset, s->sharp, s->enhance, s->overlay, a->overlay_opacity, combo, game_engine(g) == ENGINE_CLASSIC_2BUF);
            write_all(path, buf, strlen(buf));
        }
    }
    vlog("settings saved\n");
}

// ---- plugins.ini: point our titles at our files; keep foreign lines; [default] stays last ----
// Verified titles always get their plugin. Experimental titles get the classic engine only while switched on;
// switched off, their section is dropped when nothing else is left in it (an empty section would block [default]).
typedef struct { char id[12]; const char *prx; int keep_empty; } Map;
static Map g_map[MAPMAX]; static int g_nmap;
static void build_map(const App *a)
{
    g_nmap = 0;
    for (int t = 0; t < NSUP; t++) {
        Map *m = &g_map[g_nmap++]; snprintf(m->id, sizeof(m->id), "%s", k_sup[t].id);
        const Game *g = find_game(a, k_sup[t].id);   // the player's engine choice, if the game is listed
        m->prx = k_sup[t].testing ? NULL : g ? prx_of(game_engine(g)) : k_sup[t].prx; m->keep_empty = 1;
    }
    for (int i = 0; i < a->ngames && g_nmap < MAPMAX; i++) {
        const Game *g = &a->games[i]; if (!g->experimental) continue;
        Map *m = &g_map[g_nmap++]; snprintf(m->id, sizeof(m->id), "%s", g->id);
        m->prx = g->s.fg ? prx_of(game_engine(g)) : NULL; m->keep_empty = 0;
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
    static int done[MAPMAX]; memset(done, 0, sizeof(done)); int defaults_seen = 0; char line[512];
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
    if (st == 1 && (!ini || !strstr((char *)ini, PLUG "vcfg16.prx"))) st = 2;
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
    for (int i = 0; i < a->ngames; i++) {
        if (a->games[i].experimental) { h ^= (uint64_t)(a->games[i].s.fg != 0) + (uint64_t)i * 31u; h *= 1099511628211ull; }
        h ^= (uint64_t)a->games[i].eng_sel * 7u + (uint64_t)i * 131u; h *= 1099511628211ull;   // engine choice changes the mapping
    }
    return h;
}
static uint64_t g_exp_written;
static void remap_if_changed(App *a)
{
    if (exp_sig(a) == g_exp_written) return;
    if (!do_ini(a, 1)) g_exp_written = exp_sig(a);
}

// ---- debug log switch and bug report ----
// The plugins write /data/bb_probe/probe.log only while CFGDIR/debug_log exists (one file per game start, the
// previous one is kept as probe.prev.log). The report collects what a bug report needs into one text file.
#define VCFG_STR2(x) #x
#define VCFG_STR(x) VCFG_STR2(x)
#ifdef VCFG_VER
#define VCFG_VERSION VCFG_STR(VCFG_VER)   // build_pkg.ps1 passes -DVCFG_VER=<version>
#else
#define VCFG_VERSION "dev"
#endif
static int file_exists(const char *p) { FILE *f = fopen(p, "rb"); if (f) fclose(f); return f != NULL; }
static void do_debug_toggle(App *a)
{
    a->debug_log = !a->debug_log;
    int rc = a->debug_log ? write_all(CFGDIR "/debug_log", "1\n", 2) : remove(CFGDIR "/debug_log");
    addlog(a, rc ? "CANNOT CHANGE THE DEBUG LOG SWITCH" : a->debug_log ? "DEBUG LOG ON: THE NEXT GAME START WRITES A LOG" : "DEBUG LOG OFF");
    if (rc) a->debug_log = !a->debug_log;
}
typedef struct { char *b; size_t n, cap; } Buf;
static void bput(Buf *r, const char *s, size_t n)
{
    if (!r->b || r->n + n + 1 > r->cap) return;
    memcpy(r->b + r->n, s, n); r->n += n; r->b[r->n] = 0;
}
static void bprintf(Buf *r, const char *fmt, ...)
{
    char t[512]; va_list v; va_start(v, fmt); int n = vsnprintf(t, sizeof(t), fmt, v); va_end(v);
    if (n > 0) bput(r, t, (size_t)n < sizeof(t) ? (size_t)n : sizeof(t) - 1);
}
static void bfile(Buf *r, const char *title, const char *path, size_t tail)
{
    size_t n = 0; unsigned char *d = read_all(path, &n);
    bprintf(r, "\n==== %s (%s, %zu bytes%s) ====\n", title, path, n, d && n > tail ? ", last part" : "");
    if (!d) { bprintf(r, "(missing)\n"); return; }
    size_t off = n > tail ? n - tail : 0;
    if (off) while (off < n && d[off - 1] != '\n') off++;   // start at a line
    bput(r, (const char *)d + off, n - off);
    free(d);
}
// One paragraph per trace: how far the game start got, whether FG ran, the first refusal / failure lines, the end.
static const char *find_line(const char *t, const char *key, char *out, size_t on)
{
    const char *h = strstr(t, key); if (!h) return NULL;
    const char *b = h; while (b > t && b[-1] != '\n') b--;
    size_t l = strcspn(b, "\n"); if (l >= on) l = on - 1;
    memcpy(out, b, l); out[l] = 0; return out;
}
static void summarize(Buf *r, const char *id, const char *which, const char *t)
{
    char v[24] = "?", secs[40] = "?", line[208];
    find_line(t, "version: ", v, sizeof(v)); find_line(t, "seconds since start: ", secs, sizeof(secs));
    int frame = strstr(t, "first SubmitAndFlip") || strstr(t, "A22_TITLE") || strstr(t, "gnm #1 ");
    int gpu = strstr(t, "gpu_init: ok") || strstr(t, "A20_ALLOC");
    int fg = strstr(t, "mode4 #") || strstr(t, "kind=1");
    int post = strstr(t, "inject #") != NULL;   // our commands were injected (OSD / post pass): not proof of generated frames
    int menu = strstr(t, "menu opened") || strstr(t, "A20_MENU open=1");
    bprintf(r, "%s, %s: %s | %s | first frame %s, GPU memory %s, FG frames %s, OSD/post injects %s, menu used %s\n", id, which, v, secs,
            frame ? "yes" : "NO", gpu ? "yes" : "no", fg ? "yes" : "no", post ? "yes" : "no", menu ? "yes" : "no");
    static const char *keys[] = { "inert", "FAIL", "fail", "stop", "NATIVE_ONLY", "refus", "no memory", "dcb_unreadable" };
    int shown = 0;
    for (unsigned k = 0; k < sizeof(keys) / sizeof(keys[0]) && shown < 4; k++)
        if (find_line(t, keys[k], line, sizeof(line))) { bprintf(r, "    reason? %s\n", line); shown++; }
    const char *end = strstr(t, "---- end of trace ----");
    if (end && end > t) {   // last log line before the end marker: where the game start stopped if it crashed
        const char *e = end - 1, *b = e; while (b > t && b[-1] != '\n') b--;
        size_t l = (size_t)(e - b); if (l >= sizeof(line)) l = sizeof(line) - 1; memcpy(line, b, l); line[l] = 0;
        bprintf(r, "    last line: %s\n", line);
    }
    if (!frame) bprintf(r, "    => stopped before the first frame was shown (loading): see the first lines of this trace\n");
}

// Last line of a text file (the classic boot stage file: how far a game start got before a crash).
static int last_line(const char *t, size_t n, char *out, size_t on)
{
    while (n && (t[n - 1] == '\n' || t[n - 1] == '\r')) n--;
    if (!n) return 0;
    size_t b = n; while (b && t[b - 1] != '\n') b--;
    size_t l = n - b; if (l >= on) l = on - 1;
    memcpy(out, t + b, l); out[l] = 0; return 1;
}

// Trace files in CFGDIR for any title: also games the list does not show (external drive not listed, list full,
// removed game), so the summary does not depend on the game scan.
typedef struct { char id[12]; int prev; } TraceFile;
static int list_traces(TraceFile *out, int cap, int *err)
{
    int n = 0; *err = 0;
    DIR *d = opendir(CFGDIR); if (!d) { *err = errno ? errno : -1; return 0; }
    struct dirent *e;
    while ((e = readdir(d)) && n < cap) {
        const char *s = e->d_name;   // trace-CUSAxxxxx.txt / trace-CUSAxxxxx.prev.txt
        if (strncmp(s, "trace-CUSA", 10) || strlen(s) < 19) continue;
        int prev;
        if (!strcmp(s + 15, ".txt")) prev = 0; else if (!strcmp(s + 15, ".prev.txt")) prev = 1; else continue;
        snprintf(out[n].id, sizeof(out[n].id), "%.9s", s + 6); out[n].prev = prev; n++;
    }
    closedir(d);
    for (int i = 1; i < n; i++) for (int j = i; j > 0; j--) {   // by title, the last start before the one before
        int c = strcmp(out[j - 1].id, out[j].id);
        if (c < 0 || (c == 0 && out[j - 1].prev <= out[j].prev)) break;
        TraceFile t = out[j]; out[j] = out[j - 1]; out[j - 1] = t;
    }
    return n;
}

static void do_report(App *a)
{
    Buf r = { malloc(4u << 20), 0, 4u << 20 };
    if (!r.b) { addlog(a, "NO MEMORY FOR THE REPORT"); return; }
    r.b[0] = 0;
    time_t now = time(NULL);
    OrbisKernelSwVersion sw; memset(&sw, 0, sizeof(sw)); sw.Size = sizeof(sw);
    int swr = sceKernelGetSystemSwVersion(&sw);
    bprintf(&r, "VCFG bug report\napp version: %s\nfirmware: %s\ntime: %lld\ndebug log: %s\nplugin state: %d\n", VCFG_VERSION,
            swr == 0 ? sw.VersionString : "?", (long long)now, a->debug_log ? "on" : "off", a->plugin_state);
    bprintf(&r, "\n==== game scan (where the app looked for installed games) ====\n");
    for (int i = 0; i < NROOT; i++) {
        const ScanRoot *s = &g_scan[i];
        if (s->listed)
            bprintf(&r, "%s/user/app: CUSA folders %d, new %d, already listed %d, not installed %d, list full %d\n", k_roots[i],
                    s->found, s->added, s->known, s->not_installed, s->full);
        else bprintf(&r, "%s/user/app: cannot list (errno %d)\n", k_roots[i], s->err);
    }
    bprintf(&r, "games in the list: %d (room for %d)\n", a->ngames, (int)(sizeof(a->games) / sizeof(a->games[0])));

    static TraceFile tf[128]; int terr = 0, nt = list_traces(tf, 128, &terr);
    bprintf(&r, "\n==== summary (flight recorder, trace-<TITLE>.txt, %d files in " CFGDIR ") ====\n", nt);
    if (terr) bprintf(&r, "READ ERROR: cannot list " CFGDIR " (errno %d)\n", terr);
    for (int k = 0; k < nt; k++) {
        char p[96]; size_t n = 0;
        snprintf(p, sizeof(p), CFGDIR "/trace-%s%s.txt", tf[k].id, tf[k].prev ? ".prev" : "");
        unsigned char *d = read_all(p, &n);
        if (!d) { bprintf(&r, "%s: READ ERROR %s\n", tf[k].id, p); continue; }
        summarize(&r, tf[k].id, tf[k].prev ? "game start before" : "last game start", (const char *)d);
        free(d);
        {   // classic engine: crash-safe early stages of that start (boot-<TITLE>.txt / .prev.txt)
            char bp[96], line[208]; size_t bn = 0;
            snprintf(bp, sizeof(bp), CFGDIR "/boot-%s%s.txt", tf[k].id, tf[k].prev ? ".prev" : "");
            unsigned char *bd = read_all(bp, &bn);
            if (bd && last_line((const char *)bd, bn, line, sizeof(line))) bprintf(&r, "    last early stage: %s\n", line);
            free(bd);
        }
        int listed = 0; for (int i = 0; i < a->ngames; i++) if (!strcmp(a->games[i].id, tf[k].id)) listed = 1;
        if (!listed && !tf[k].prev) bprintf(&r, "    (not in the app's game list)\n");
    }
    for (int i = 0; i < a->ngames; i++) {
        int has = 0; for (int k = 0; k < nt; k++) if (!strcmp(tf[k].id, a->games[i].id)) has = 1;
        if (!has) bprintf(&r, "%s: NOT COLLECTED (no trace file: the plugin has not run in this game yet, or GoldHEN did not load it;"
                          " not proof that the game was never started)\n", a->games[i].id);
    }
    bprintf(&r, "\n==== games ====\n");
    for (int i = 0; i < a->ngames; i++) {
        const Game *g = &a->games[i];
        bprintf(&r, "%s version=%s installed=%d experimental=%d fg=%d preset=%d sharp=%d enhance=%d overlay=%d engine_choice=%d engine=%d title='%s'\n", g->id,
                g->version[0] ? g->version : "?", g->installed, g->experimental, g->s.fg, g->s.preset, g->s.sharp, g->s.enhance,
                g->s.overlay, g->eng_sel, game_engine(g), g->title);
    }
    bprintf(&r, "\n==== plugin files ====\n");
    for (int i = 0; i < (int)(sizeof(k_files) / sizeof(k_files[0])); i++) {
        char p[96]; size_t n = 0; snprintf(p, sizeof(p), PLUG "%s", k_files[i]);
        unsigned char *d = read_all(p, &n); bprintf(&r, "%s %s %zu bytes\n", p, d ? "present" : "MISSING", n); free(d);
    }
    bfile(&r, "plugins.ini", INI, 64u << 10);
    bfile(&r, "launcher settings", CFGDIR "/vcfg_launcher.ini", 8u << 10);
    bfile(&r, "new engine settings", CFGDIR "/vcfg.ini", 8u << 10);
    bfile(&r, "engine choice per game", ENGFILE, 8u << 10);
    for (int i = 0; i < a->ngames; i++) {
        char p[96]; snprintf(p, sizeof(p), CFGDIR "/config-%s.ini", a->games[i].id);
        if (file_exists(p)) bfile(&r, "game settings", p, 8u << 10);
    }
    for (int k = 0; k < nt; k++) {
        char p[96]; snprintf(p, sizeof(p), CFGDIR "/trace-%s%s.txt", tf[k].id, tf[k].prev ? ".prev" : "");
        bfile(&r, tf[k].prev ? "flight recorder, game start before" : "flight recorder, last game start", p, 96u << 10);
        snprintf(p, sizeof(p), CFGDIR "/boot-%s%s.txt", tf[k].id, tf[k].prev ? ".prev" : "");
        if (file_exists(p)) bfile(&r, tf[k].prev ? "early start stages (classic), game start before" : "early start stages (classic), last game start", p, 48u << 10);
    }
    bfile(&r, "plugin log, last game start", CFGDIR "/probe.log", 1536u << 10);
    bfile(&r, "plugin log, the start before", CFGDIR "/probe.prev.log", 1024u << 10);
    bfile(&r, "app log", "/data/vcfg_launcher.log", 64u << 10);
    if (!a->debug_log) bprintf(&r, "\nNOTE: the debug log was off. Switch it on in the app (PLUGIN tab, triangle), reproduce, save again.\n");

    int rc = write_all("/data/vcfg_report.txt", r.b, r.n);
    char m[96]; snprintf(m, sizeof(m), rc ? "CANNOT WRITE /DATA/VCFG_REPORT.TXT" : "REPORT SAVED: /DATA/VCFG_REPORT.TXT (%zu KB)", r.n / 1024);
    addlog(a, m);
    // USB sticks are outside the app sandbox: write there under a GoldHEN jailbreak (like the game metadata).
    struct jailbreak_backup jb; memset(&jb, 0, sizeof(jb));
    int jrc = sys_sdk_jailbreak(&jb), usb = 0;
    for (int i = 0; i < 8 && !usb; i++) {
        char dir[32], p[64]; snprintf(dir, sizeof(dir), "/mnt/usb%d", i);
        DIR *d = opendir(dir); if (!d) continue; closedir(d);
        snprintf(p, sizeof(p), "%s/vcfg_report.txt", dir);
        if (!write_all(p, r.b, r.n)) { snprintf(m, sizeof(m), "ALSO ON THE USB STICK: %s", p); addlog(a, m); usb = 1; }
    }
    if (jrc == 0) sys_sdk_unjailbreak(&jb);
    if (!usb) addlog(a, "NO USB STICK: TAKE THE FILE OVER FTP");
    snprintf(a->status, sizeof(a->status), "ATTACH VCFG_REPORT.TXT TO THE ISSUE ON GITHUB.");
    free(r.b);
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
    static Fonts fonts; static App app; App *a = &app; a->f = &fonts; snprintf(a->version, sizeof(a->version), "%s", VCFG_VERSION);
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
    for (int r = 0; r < NROOT; r++) {
    char ap[48]; snprintf(ap, sizeof(ap), "%s/user/app", k_roots[r]);
    DIR *dir = opendir(ap);
    ScanRoot *sr = &g_scan[r];
    if (dir) {
        struct dirent *e;
        sr->listed = 1;
        while ((e = readdir(dir))) {
            if (strncmp(e->d_name, "CUSA", 4) || strlen(e->d_name) != 9) continue;
            sr->found++;
            int known = 0; for (int i = 0; i < a->ngames; i++) if (!strcmp(a->games[i].id, e->d_name)) known = 1;
            if (known) { sr->known++; continue; }
            if (a->ngames >= (int)(sizeof(a->games) / sizeof(a->games[0]))) { sr->full++; continue; }   // counted, shown in the report
            Game *g = &a->games[a->ngames++]; memset(g, 0, sizeof(*g));
            snprintf(g->id, sizeof(g->id), "%s", e->d_name);
            g->engine = auto_2buf(e->d_name) ? ENGINE_CLASSIC_2BUF : auto_alt(e->d_name) ? ENGINE_CLASSIC_ALT : ENGINE_CLASSIC;
            g->experimental = 1;
            load_meta(g);
            if (!g->installed) { a->ngames--; sr->not_installed++; } else sr->added++;
        }
        closedir(dir);
    } else { sr->err = errno; vlog("cannot list %s (errno %d)\n", ap, sr->err); }
    if (sr->listed)
        vlog("scan %s: CUSA folders %d, new %d, already listed %d, not installed %d, list full %d\n", ap, sr->found, sr->added,
             sr->known, sr->not_installed, sr->full);
    }
    if (jrc == 0) vlog("unjailbreak rc=%d\n", sys_sdk_unjailbreak(&jb));
    // Hide games that are not installed (plugins.ini keeps mapping them, see build_map).
    { int k = 0; for (int i = 0; i < a->ngames; i++) if (a->games[i].installed) a->games[k++] = a->games[i]; a->ngames = k; }
    // Installed games first, keep the table order otherwise.
#define RANK(g) ((g).installed * 2 + !(g).experimental)   // verified first, installed first
    for (int i = 1; i < a->ngames; i++) for (int j = i; j > 0 && RANK(a->games[j]) > RANK(a->games[j - 1]); j--) {
        Game t = a->games[j]; a->games[j] = a->games[j - 1]; a->games[j - 1] = t;
    }
    load_settings(a);
    a->debug_log = file_exists(CFGDIR "/debug_log");
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
        if (app_pad(a, btn)) redraw = 1;   // hotkey recording needs the held buttons, not only the presses
        if (edge) {
            int act = app_input(a, edge); redraw = 1;
            if (act == ACT_INSTALL) do_install(a);
            else if (act == ACT_REMOVE) { do_ini(a, 0); a->plugin_state = plugin_state(); }
            else if (act == ACT_DEBUG) do_debug_toggle(a);
            else if (act == ACT_REPORT) do_report(a);
            else if (act == ACT_SAVE) { save_settings(a); remap_if_changed(a); }
            else if (act == ACT_SAVE_EXIT) {
                save_settings(a); remap_if_changed(a);
                snprintf(a->status, sizeof(a->status), "SAVED. START A GAME, HOLD THE MENU HOTKEY FOR 3 S.");
                app_draw(&c, a); present(back); sceKernelUsleep(900000); quit();
            }
        }
        if (redraw) { app_draw(&c, a); present(back); redraw = 0; }
        else sceKernelUsleep(16000);
    }
}
