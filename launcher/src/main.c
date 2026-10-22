// VCFG launcher, console side: video output, controller, files. Drawing and screens live in ui.c / app.c.
// Settings files (read by the plugins at the next game start):
//   new engine:     /data/bb_probe/vcfg.ini              enabled, preset, sharp, enhance, overlay, opacity
//   classic engine: /data/bb_probe/config-<TITLE>.ini    framegen, preset, sharpness, enhance, overlay, menu_opacity, two_buffer, present_copy
//   launcher:       /data/bb_probe/vcfg_launcher.ini     defaults and overlay style
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdarg.h>
#include <dirent.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/time.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <orbis/libkernel.h>
#include <orbis/VideoOut.h>
#include <orbis/Pad.h>
#include <orbis/UserService.h>
#include <orbis/Sysmodule.h>
#include <orbis/SystemService.h>
#include "app.h"
#include "../../overlay/menu_combo.h"
#include "../../overlay/title_families.h"
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
    { "CUSA08519", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA00411", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA00207", "vcfg_classic51.prx", ENGINE_CLASSIC_BB, 0 },
    { "CUSA17419", "vcfg_alt_16.prx", ENGINE_CLASSIC_ALT, 0 },
    { "CUSA00918", "vcfg_alt_16.prx", ENGINE_CLASSIC_ALT, 0 },
    { "CUSA04529", "vcfg_alt_16.prx", ENGINE_CLASSIC_ALT, 0 },
    { "CUSA11456", "vcfg_alt_16.prx", ENGINE_CLASSIC_ALT, 0 },
    { "CUSA05571", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA08809", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA00557", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA09171", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA13801", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA14209", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA12054", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA16734", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA36842", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA01015", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA33388", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA34384", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA15778", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA00083", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA02320", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA03388", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA02299", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA09311", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA18534", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA10123", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA08364", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA08344", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA10454", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA11100", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA15090", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA18723", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA36375", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA06548", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA10237", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA11260", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA32946", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA28561", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA01154", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA04841", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA17722", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA32836", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
    { "CUSA01113", "vcfg_classic51.prx", ENGINE_CLASSIC, 0 },
};
#define NSUP (int)(sizeof(k_sup) / sizeof(k_sup[0]))
static const char *k_files[] = { "vcfg16.prx", "vcfg_classic51.prx", "vcfg_alt_16.prx" };
// Double-buffered titles that run best with 2-BUFFER present copy + virtual flip queue: AUTO = 2-BUFFER.
static int auto_2buf(const char *id)
{
    return vcfg_in_family(id, "CUSA05571") || vcfg_in_family(id, "CUSA08809") ||
           vcfg_in_family(id, "CUSA17419") || vcfg_in_family(id, "CUSA14209") ||
           vcfg_in_family(id, "CUSA36375") || vcfg_in_family(id, "CUSA08519") ||
           vcfg_in_family(id, "CUSA11456") || vcfg_in_family(id, "CUSA01015") ||
           vcfg_in_family(id, "CUSA15778") || vcfg_in_family(id, "CUSA00083") ||
           vcfg_in_family(id, "CUSA02299") || vcfg_in_family(id, "CUSA08344") ||
           vcfg_in_family(id, "CUSA10454") || vcfg_in_family(id, "CUSA11100") ||
           vcfg_in_family(id, "CUSA15090") || vcfg_in_family(id, "CUSA18723");
}
static int auto_alt(const char *id)
{
    return vcfg_in_family(id, "CUSA17419") || vcfg_in_family(id, "CUSA04529") || vcfg_in_family(id, "CUSA11456");
}
static const Supported *sup_for_title(const char *id)
{
    const char *fam = vcfg_title_family(id);
    for (int i = 0; i < NSUP; i++) if (!strcmp(fam, k_sup[i].id)) return &k_sup[i];
    return NULL;
}
#define MAPMAX 512   // plugins.ini sections we manage: the verified table + regional aliases + every game in the list (App.games)
#define ENGFILE CFGDIR "/vcfg_engines.ini"   // the player's engine choice per game: CUSAxxxxx=<SEL_*>, AUTO not written
#define BUFFILE CFGDIR "/vcfg_buffers.ini"   // the player's buffer mode choice per game: CUSAxxxxx=<BUF_*>, AUTO not written
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
    FILE *f = fopen(path, "wb");
    if (!f) { remove(path); f = fopen(path, "wb"); }
    if (!f) return -1;
    size_t w = fwrite(d, 1, n, f); int c = fclose(f); return (w == n && !c) ? 0 : -1;
}
static int write_verified(const char *path, const void *d, size_t n)
{
    char tmp[256]; snprintf(tmp, sizeof(tmp), "%s.vcfg_tmp", path);
    if (write_all(tmp, d, n)) return -1;
    size_t m = 0; unsigned char *b = read_all(tmp, &m); int ok = b && m == n && !memcmp(b, d, n); free(b);
    if (!ok) { remove(tmp); return -1; }
    if (rename(tmp, path)) { remove(path); if (rename(tmp, path)) { remove(tmp); return -1; } }
    return 0;
}

// ---- game metadata from the console (title from param.sfo, icon0.png) ----
static int sfo_str(const unsigned char *b, size_t n, const char *key, char *out, size_t on)
{
    if (n < 20 || *(const uint32_t *)b != 0x46535000u) return 0;
    uint32_t keys = *(const uint32_t *)(b + 8), data = *(const uint32_t *)(b + 12), cnt = *(const uint32_t *)(b + 16);
    for (uint32_t i = 0; i < cnt && 20 + (i + 1) * 16 <= n; i++) {
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
// Where a title's icon can be: appmeta (internal or external sub-folder), the title's own sce_sys on its drive, an installed patch.
static const char *const k_icon_tpl[] = {
    "%s/user/appmeta/%s/icon0.png",
    "%s/user/appmeta/%s/icon0_08.png",
    "%s/user/appmeta/external/%s/icon0.png",
    "%s/user/appmeta/external/%s/icon0_08.png",
    "%s/user/app/%s/sce_sys/icon0.png",
    "%s/user/app/%s/icon0.png",
    "%s/user/patch/%s/sce_sys/icon0.png",
    "%s/system_data/priv/appmeta/%s/icon0.png",
    "%s/system_data/priv/appmeta/external/%s/icon0.png"
};
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
// Folder layout snapshot for the bug report (taken inside the jailbreak window: the report is built later without it).
// Names only. Shows where an external drive keeps its games, which neither of us can see from here.
static char g_layout[8192]; static int g_layout_n;
static void layout_put(const char *fmt, ...)
{
    if (g_layout_n >= (int)sizeof(g_layout) - 2) return;
    va_list ap; va_start(ap, fmt); int n = vsnprintf(g_layout + g_layout_n, sizeof(g_layout) - (size_t)g_layout_n, fmt, ap); va_end(ap);
    if (n > 0) g_layout_n += n < (int)sizeof(g_layout) - g_layout_n ? n : (int)sizeof(g_layout) - g_layout_n - 1;
}
static void layout_ls(const char *path)
{
    DIR *d = opendir(path);
    if (!d) { layout_put("%s: cannot open (errno %d)\n", path, errno); return; }
    layout_put("%s:", path); int cnt = 0, total = 0; struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        total++; if (cnt < 40) { layout_put(" %s", e->d_name); cnt++; }
    }
    closedir(d); layout_put(" (%d entries)\n", total);
}
static void layout_snapshot(void)
{
    g_layout_n = 0; g_layout[0] = 0;
    layout_ls("/mnt");
    layout_ls("/user/appmeta/external");
    layout_ls("/system_data/priv/appmeta/external");
    for (int r = 1; r < NROOT; r++) {
        char d[96];
        layout_ls(k_roots[r]);
        snprintf(d, sizeof(d), "%s/user", k_roots[r]); layout_ls(d);
        snprintf(d, sizeof(d), "%s/user/app", k_roots[r]); layout_ls(d);
        snprintf(d, sizeof(d), "%s/user/appmeta", k_roots[r]); layout_ls(d);
    }
    for (int r = 1; r < NROOT; r++) {   // what a game folder on an external drive holds, and the other top folders
        char d[160];
        for (int i = 0; i < NSUP; i++) {
            snprintf(d, sizeof(d), "%s/user/app/%s", k_roots[r], k_sup[i].id); DIR *x = opendir(d); if (x) { closedir(x); layout_ls(d); }
            snprintf(d, sizeof(d), "%s/user/patch/%s", k_roots[r], k_sup[i].id); x = opendir(d); if (x) { closedir(x); layout_ls(d); }
        }
        snprintf(d, sizeof(d), "%s/system_data", k_roots[r]); layout_ls(d);
        snprintf(d, sizeof(d), "%s/user/addcont", k_roots[r]); layout_ls(d);
    }
    // Where each supported title is found: 1 = folder exists.
    static const char *const subs[] = { "user/app", "user/appmeta", "user/patch" };
    for (int i = 0; i < NSUP; i++) {
        layout_put("%s:", k_sup[i].id);
        for (int r = 0; r < NROOT; r++) for (int t = 0; t < 3; t++) {
            char d[160]; snprintf(d, sizeof(d), "%s/%s/%s", k_roots[r], subs[t], k_sup[i].id);
            DIR *x = opendir(d); layout_put(" %s=%d", d, x ? 1 : 0); if (x) closedir(x);
        }
        layout_put("\n");
    }
}

static void load_meta(Game *g)
{
    char p[160]; size_t n = 0; unsigned char *b = NULL;
    for (int r = 0; r < NROOT && !b; r++) {
        snprintf(p, sizeof(p), "%s/user/appmeta/%s/param.sfo", k_roots[r], g->id); b = read_all(p, &n);
    }
    if (!b) { snprintf(p, sizeof(p), "/user/appmeta/external/%s/param.sfo", g->id); b = read_all(p, &n); }
    for (int r = 0; r < NROOT && !b; r++) {   // extended storage keeps no appmeta on /mnt/ext*: check game sce_sys or internal appmeta/external
        snprintf(p, sizeof(p), "%s/user/app/%s/sce_sys/param.sfo", k_roots[r], g->id); b = read_all(p, &n);
    }
    if (!b) { snprintf(p, sizeof(p), "/system_data/priv/appmeta/%s/param.sfo", g->id); b = read_all(p, &n); }
    if (!b) { snprintf(p, sizeof(p), "/system_data/priv/appmeta/external/%s/param.sfo", g->id); b = read_all(p, &n); }
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
    // Extended storage (external HDD) has an empty user/appmeta, so a non-empty game folder there is the only sign of an installed game.
    for (int r = 1; r < NROOT && !g->installed; r++) {
        char d[160]; snprintf(d, sizeof(d), "%s/user/app/%s", k_roots[r], g->id);
        DIR *x = opendir(d);
        if (x) { struct dirent *e; while ((e = readdir(x))) if (e->d_name[0] != '.') { g->installed = 1; break; } closedir(x); }
    }
    // Last resort: a settings file (only written for installed games, see save_settings).
    if (!g->installed) { snprintf(p, sizeof(p), CFGDIR "/config-%s.ini", g->id); FILE *f = fopen(p, "rb"); if (f) { fclose(f); g->installed = 1; } }
    vlog("game %s installed=%d version=%s title='%s' icon=%dx%d\n", g->id, g->installed, g->version, g->title, g->iw, g->ih);
}

// ---- settings files ----
static int ini_int(const char *text, const char *key, int def)
{
    int res = def;
    size_t kl = strlen(key);
    for (const char *p = text; p && *p; ) {
        while (*p == ' ' || *p == '\t') p++;
        if (!strncmp(p, key, kl)) { const char *q = p + kl; while (*q == ' ') q++; if (*q == '=') res = atoi(q + 1); }
        p = strchr(p, '\n'); if (p) p++;
    }
    return res;
}
static void ini_str(const char *text, const char *key, char *out, size_t cap, const char *def)
{
    snprintf(out, cap, "%s", def ? def : "");
    if (!text) return;
    size_t kl = strlen(key);
    for (const char *p = text; *p;) {
        while (*p == ' ' || *p == '\t') p++;
        if (!strncmp(p, key, kl) && (p[kl] == '=' || p[kl] == ' ')) {
            p += kl;
            while (*p == ' ' || *p == '\t' || *p == '=') p++;
            size_t i = 0;
            while (p[i] && p[i] != '\r' && p[i] != '\n' && p[i] != ';' && p[i] != '#') i++;
            while (i > 0 && (p[i - 1] == ' ' || p[i - 1] == '\t')) i--;
            if (i >= cap) i = cap - 1;
            memcpy(out, p, i);
            out[i] = 0;
            return;
        }
        while (*p && *p != '\n') p++;
        if (*p == '\n') p++;
    }
}
static int file_exists(const char *p) { FILE *f = fopen(p, "rb"); if (f) fclose(f); return f != NULL; }
static const int k_dev_locks[4] = { 30, 20, 60, 0 };
static const int k_dev_tiles[7] = { -1, 10, 14, 13, 9, 12, 11 };
static int lock_to_dev(int lk)
{
    for (int i = 0; i < 4; i++) if (k_dev_locks[i] == lk) return i;
    return 0;
}
static int tile_to_dev(int ti)
{
    for (int i = 1; i < 7; i++) if (k_dev_tiles[i] == ti) return i;
    return 0;
}
static int kv_uint_after(const char *s, const char *key, unsigned def)
{
    const char *p = strstr(s, key);
    if (!p) return (int)def;
    return atoi(p + strlen(key));
}
static void add_tail_lines(DevCfg *d, const char *src)
{
    if (!src || !*src) return;
    const char *lines[64];
    int lens[64];
    int cnt = 0;
    const char *p = src;
    while (*p && cnt < 64) {
        while (*p == '\r' || *p == '\n' || *p == ' ') p++;
        if (!*p) break;
        const char *e = p;
        while (*e && *e != '\r' && *e != '\n') e++;
        if (e - p >= 4) {
            lines[cnt] = p;
            lens[cnt] = (int)(e - p);
            cnt++;
        }
        p = e;
    }
    int start = cnt > 4 ? cnt - 4 : 0;
    for (int i = start; i < cnt && d->n_tail < 4; i++) {
        int l = lens[i] < 78 ? lens[i] : 78;
        memcpy(d->log_tail[d->n_tail], lines[i], (size_t)l);
        d->log_tail[d->n_tail][l] = 0;
        d->n_tail++;
    }
}
static int count_substr(const char *s, const char *needle)
{
    if (!s || !needle || !needle[0]) return 0;
    int c = 0;
    size_t l = strlen(needle);
    const char *p = s;
    while ((p = strstr(p, needle)) != NULL) { c++; p += l; }
    return c;
}
static void refresh_dev_status(Game *g)
{
    char path[96]; size_t n = 0, bn = 0, tn = 0;
    DevCfg *d = &g->dev;
    snprintf(path, sizeof(path), CFGDIR "/safe-%s.state", g->id);
    unsigned char *sb = read_all(path, &n);
    int raw_safe = (sb && n >= 1 && sb[0] >= '0' && sb[0] <= '2') ? (sb[0] - '0') : 0;
    free(sb);
    if (raw_safe > 0) d->had_watchdog = raw_safe;
    d->safe_state = raw_safe > 0 ? raw_safe : d->had_watchdog;

    snprintf(path, sizeof(path), CFGDIR "/adapt-%s.ini", g->id);
    d->has_adapt = file_exists(path);
    snprintf(path, sizeof(path), CFGDIR "/crash-%s.txt", g->id);
    unsigned char *cb = read_all(path, &n);
    d->has_crash = cb != NULL;

    snprintf(path, sizeof(path), CFGDIR "/boot-%s.txt", g->id);
    unsigned char *bb = read_all(path, &bn);
    if (!bb || bn < 8) {
        free(bb);
        snprintf(path, sizeof(path), CFGDIR "/boot-%s.prev.txt", g->id);
        bb = read_all(path, &bn);
    }
    snprintf(path, sizeof(path), CFGDIR "/trace-%s.txt", g->id);
    unsigned char *tb = read_all(path, &tn);
    if (!tb || tn < 8) {
        free(tb);
        snprintf(path, sizeof(path), CFGDIR "/trace-%s.prev.txt", g->id);
        tb = read_all(path, &tn);
    }

    d->diag_kind      = DIAG_NONE;
    d->rec_row        = 0;
    d->rec_mask       = 0;
    d->rec_applied    = 0;
    d->rec_manual     = 0;
    d->rec_eng_sel    = SEL_AUTO;
    d->rec_buf_sel    = BUF_AUTO;
    d->rec_shave_mode = 0;
    d->rec_reg_mode   = 0;
    d->rec_pace_order = 0;
    d->rec_post_mid   = 0;
    d->rec_fps_lock   = 0;
    d->rec_tile_mode  = 0;
    d->n_tail         = 0;

    snprintf(d->diag_cause, sizeof(d->diag_cause), "NO CRASH RECORDED");
    snprintf(d->diag_stage, sizeof(d->diag_stage), "READY FOR TEST");
    snprintf(d->diag_advice, sizeof(d->diag_advice), "AUTO (ALGORITHM) PROFILE");
    snprintf(d->diag_detail, sizeof(d->diag_detail),
             "No crash or error log found for %s. Press R2 to ensure the recommended AUTO profile is active, then launch with SQUARE.",
             g->id);
    snprintf(d->step1, sizeof(d->step1), "1. Press R2 to apply the recommended Advisor profile.");
    snprintf(d->step2, sizeof(d->step2), "2. Press SQUARE to launch %s and test.", g->id);
    snprintf(d->step3, sizeof(d->step3), "3. If an issue occurs, return here and press R2.");

    const char *cs = cb ? (const char *)cb : "";
    const char *bs = bb ? (const char *)bb : "";
    const char *ts = tb ? (const char *)tb : "";

    if (cb) add_tail_lines(d, cs);
    if (d->n_tail < 4 && tb) add_tail_lines(d, ts);
    if (d->n_tail < 4 && bb) add_tail_lines(d, bs);

    // Deterministic log-based telemetry (never depends on live UI sliders so R2 is 100% idempotent):
    int logged_shave   = kv_uint_after(bs, "shave_mode=", 0);
    int logged_reg     = kv_uint_after(bs, "reg_mode=", 0);
    int logged_pace    = kv_uint_after(bs, "pace_order=", 0);
    int is_2b_log      = (strstr(bs, "num=2") != NULL || strstr(ts, "count=2") != NULL ||
                          strstr(ts, "double-buffered game") != NULL || strstr(ts, "1x1_gv_fallback") != NULL ||
                          strstr(bs, " 1x1 ") != NULL || auto_2buf(g->id));
    int multi_reg      = count_substr(bs, "RegisterBuffers enter:") + count_substr(ts, "RegisterBuffers handle=");
    int has_shave_log  = (strstr(bs, "arena shave OK") != NULL || strstr(bs, "shave=1") != NULL);
    int has_vout_only  = (strstr(ts, "flips via sceVideoOutSubmitFlip") != NULL || strstr(ts, "vout_flip") != NULL);
    int has_60fps_inert= (strstr(ts, "60FPS NATIVE") != NULL || strstr(ts, "detected_60fps=1") != NULL);
    int has_hdr_inert  = (strstr(ts, "HDR/FMT") != NULL);
    unsigned pace_to   = (unsigned)kv_uint_after(ts, "pace timeouts ", 0);
    unsigned missed_vb = (unsigned)kv_uint_after(ts, "missed ", 0);
    int fg_ran         = (strstr(ts, "mode4 #") != NULL || strstr(ts, "vout fg #") != NULL ||
                          strstr(bs, "gnm #100") != NULL || strstr(bs, "flip #100") != NULL);

    if (cb) {
        const char *sig = strstr(cs, "SIGBUS") ? "SIGBUS" :
                          strstr(cs, "SIGILL") ? "SIGILL" :
                          strstr(cs, "SIGABRT") ? "SIGABRT" : "SIGSEGV";
        unsigned flips = (unsigned)kv_uint_after(cs, "flips=", 0);
        int in_sub     = kv_uint_after(cs, "in_submit=", 0);
        int afail      = kv_uint_after(cs, "alloc_fail=", 0);
        int oom = afail > 0 || strstr(cs, "AllocateDirectMemory") || strstr(cs, "MapDirectMemory") ||
                  strstr(bs, "dmem_FAIL") || strstr(ts, "gpu_init: no memory") || strstr(bs, "[LOWMEM] valve=0");
        if (oom) {
            d->diag_kind      = DIAG_OOM_BOOT;
            d->rec_manual     = 1;
            d->rec_eng_sel    = (g->engine == ENGINE_NEW) ? SEL_NEW : SEL_ALT;
            d->rec_buf_sel    = is_2b_log ? BUF_2BUF : BUF_AUTO;
            d->rec_shave_mode = (flips == 0 && logged_shave >= 1) ? 2 : (has_shave_log ? 1 : 2);
            d->rec_reg_mode   = (flips == 0) ? 2 : 1;
            d->rec_pace_order = is_2b_log ? 3 : 2;
            d->rec_post_mid   = 1;
            d->rec_row        = 2;
            snprintf(d->diag_cause, sizeof(d->diag_cause), "%s: GPU / DIRECT MEM OOM", sig);
            snprintf(d->diag_stage, sizeof(d->diag_stage), "VRAM EXHAUSTED AT FLIP %u", flips);
            snprintf(d->diag_advice, sizeof(d->diag_advice), "ALT + %s + %s (R2: APPLY ALL)",
                     d->rec_shave_mode == 1 ? "VALVE ONLY" : "LATE (F120)",
                     d->rec_reg_mode == 1 ? "TOP GROUP" : "DEFER REG");
            snprintf(d->diag_detail, sizeof(d->diag_detail),
                     "DirectMemory conflict (%s at flip %u, alloc_fail=%d). Press R2 to apply ALT low-VRAM engine + %s + %s + MID ONLY.",
                     sig, flips, afail,
                     d->rec_shave_mode == 1 ? "VALVE ONLY" : "LATE (F120)",
                     d->rec_reg_mode == 1 ? "TOP GROUP" : "DEFERRED (F120)");
            snprintf(d->step1, sizeof(d->step1), "1. Press R2 to apply the full Low-VRAM Advisor profile.");
            snprintf(d->step2, sizeof(d->step2), "2. Sets ALT Engine + %s + %s.",
                     d->rec_shave_mode == 1 ? "VALVE ONLY" : "LATE (F120)",
                     d->rec_reg_mode == 1 ? "TOP GROUP" : "DEFERRED");
            snprintf(d->step3, sizeof(d->step3), "3. Press SQUARE to re-test the game.");
        } else if (flips == 0 || strstr(cs, "RegisterBuffers") || strstr(cs, "BatchMap")) {
            d->diag_kind      = DIAG_BOOT_EARLY;
            d->rec_manual     = 1;
            d->rec_eng_sel    = (g->engine == ENGINE_NEW) ? SEL_NEW : SEL_ALT;
            d->rec_buf_sel    = is_2b_log ? BUF_2BUF : BUF_AUTO;
            d->rec_shave_mode = (strstr(cs, "RegisterBuffers") || strstr(bs, "BatchMap")) ? 1 : 2;
            d->rec_reg_mode   = (logged_reg == 1) ? 2 : 1;
            d->rec_pace_order = is_2b_log ? 3 : 2;
            d->rec_post_mid   = 1;
            d->rec_row        = (d->rec_shave_mode == 2) ? 2 : 3;
            snprintf(d->diag_cause, sizeof(d->diag_cause), "%s BEFORE FIRST FRAME", sig);
            snprintf(d->diag_stage, sizeof(d->diag_stage), "EARLY BOOT / BUFFER REG HOOK");
            snprintf(d->diag_advice, sizeof(d->diag_advice), "ALT + %s + %s (R2: APPLY ALL)",
                     d->rec_shave_mode == 2 ? "LATE (F120)" : "VALVE ONLY",
                     d->rec_reg_mode == 2 ? "DEFER (F120)" : "TOP GROUP");
            snprintf(d->diag_detail, sizeof(d->diag_detail),
                     "Crash (%s) during engine init before frame 1. Press R2 to isolate display buffers in %s and use %s memory hook.",
                     sig,
                     d->rec_reg_mode == 2 ? "DEFERRED (F120)" : "TOP GROUP (16-N)",
                     d->rec_shave_mode == 2 ? "LATE (F120)" : "VALVE ONLY");
            snprintf(d->step1, sizeof(d->step1), "1. Press R2 to apply the full Boot-Safe Advisor profile.");
            snprintf(d->step2, sizeof(d->step2), "2. Sets ALT + %s + %s.",
                     d->rec_shave_mode == 2 ? "LATE (F120)" : "VALVE ONLY",
                     d->rec_reg_mode == 2 ? "DEFERRED (F120)" : "TOP GROUP");
            snprintf(d->step3, sizeof(d->step3), "3. Press SQUARE to test boot stability.");
        } else if (in_sub || strstr(cs, "SubmitAndFlip") || strstr(cs, "SubmitDone") ||
                   strstr(cs, "VideoOutSubmitFlip") || strstr(cs, "WaitOnAddress")) {
            d->diag_kind      = DIAG_FLIP_STALL;
            d->rec_manual     = 1;
            d->rec_eng_sel    = SEL_AUTO;
            d->rec_buf_sel    = is_2b_log ? BUF_2BUF : BUF_AUTO;
            d->rec_shave_mode = 1;
            d->rec_reg_mode   = 1;
            d->rec_pace_order = (logged_pace == 2 || is_2b_log) ? 3 : 2;
            d->rec_post_mid   = 1;
            d->rec_row        = 4;
            snprintf(d->diag_cause, sizeof(d->diag_cause), "%s IN FLIP SUBMIT (F%u)", sig, flips);
            snprintf(d->diag_stage, sizeof(d->diag_stage), "SUBMIT / VBLANK PACER CONFLICT");
            snprintf(d->diag_advice, sizeof(d->diag_advice), "%s + TOP GROUP + MID ONLY (R2)",
                     d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->diag_detail, sizeof(d->diag_detail),
                     "Crash (%s at flip %u, in_submit=%d) inside flip submission. Press R2 to configure %s pacing + TOP GROUP + MID ONLY.",
                     sig, flips, in_sub, d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->step1, sizeof(d->step1), "1. Press R2 to apply the full Flip-Safe Advisor profile.");
            snprintf(d->step2, sizeof(d->step2), "2. Sets %s + TOP GROUP + VALVE ONLY.",
                     d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->step3, sizeof(d->step3), "3. Press SQUARE to launch.");
        } else {
            d->diag_kind      = DIAG_REG_CONFLICT;
            d->rec_manual     = 1;
            d->rec_eng_sel    = (g->engine == ENGINE_NEW) ? SEL_NEW : SEL_ALT;
            d->rec_buf_sel    = is_2b_log ? BUF_2BUF : BUF_AUTO;
            d->rec_shave_mode = 1;
            d->rec_reg_mode   = 1;
            d->rec_pace_order = is_2b_log ? 3 : 2;
            d->rec_post_mid   = 1;
            d->rec_row        = 3;
            snprintf(d->diag_cause, sizeof(d->diag_cause), "%s AT FLIP %u", sig, flips);
            snprintf(d->diag_stage, sizeof(d->diag_stage), "MID-GAME ARENA / REG CONFLICT");
            snprintf(d->diag_advice, sizeof(d->diag_advice), "ALT + VALVE ONLY + TOP GROUP (R2)");
            snprintf(d->diag_detail, sizeof(d->diag_detail),
                     "Mid-session fault (%s at flip %u). Engine re-allocated display buffers or hit arena limits. Press R2 to apply full isolation profile.",
                     sig, flips);
            snprintf(d->step1, sizeof(d->step1), "1. Press R2 to apply VALVE ONLY + TOP GROUP (16-N).");
            snprintf(d->step2, sizeof(d->step2), "2. Sets %s pacing + MID ONLY post target.",
                     d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->step3, sizeof(d->step3), "3. Press SQUARE to verify stability.");
        }
    } else if (d->safe_state > 0) {
        d->diag_kind      = DIAG_BOOT_EARLY;
        d->rec_manual     = 1;
        d->rec_eng_sel    = (g->engine == ENGINE_NEW) ? SEL_NEW : SEL_ALT;
        d->rec_buf_sel    = is_2b_log ? BUF_2BUF : BUF_AUTO;
        d->rec_shave_mode = (multi_reg >= 2) ? 1 : 2;
        d->rec_reg_mode   = 1;
        d->rec_pace_order = is_2b_log ? 3 : 2;
        d->rec_post_mid   = 1;
        d->rec_row        = 2;
        snprintf(d->diag_cause, sizeof(d->diag_cause),
                 d->safe_state >= 2 ? "REPEATED BOOT ABORT -> PASSIVE" : "PREVIOUS BOOT STOPPED EARLY");
        snprintf(d->diag_stage, sizeof(d->diag_stage),
                 d->safe_state >= 2 ? "FG PAUSED BY SAFE WATCHDOG" : "AUTO-DEFERRED TO FRAME 120");
        snprintf(d->diag_advice, sizeof(d->diag_advice), "ALT + %s + TOP GROUP (R2: APPLY)",
                 d->rec_shave_mode == 2 ? "LATE (F120)" : "VALVE ONLY");
        snprintf(d->diag_detail, sizeof(d->diag_detail),
                 "Watchdog detected %s before frame 120 (safe_state=%d). Press R2 to apply the full Boot-Safe Advisor profile and clear watchdog.",
                 d->safe_state >= 2 ? "2+ incomplete boots" : "an incomplete boot", d->safe_state);
        snprintf(d->step1, sizeof(d->step1), "1. Press R2 to apply ALT + %s + TOP GROUP.",
                 d->rec_shave_mode == 2 ? "LATE (F120)" : "VALVE ONLY");
        snprintf(d->step2, sizeof(d->step2), "2. Or press TRIANGLE to restore AUTO (ALGORITHM).");
        snprintf(d->step3, sizeof(d->step3), "3. Press SQUARE to launch and test.");
    } else if (strstr(bs, "dmem_FAIL") || strstr(ts, "gpu_init: no memory") || strstr(ts, "work memory: none") || strstr(bs, "alloc_dm FAIL")) {
        d->diag_kind      = DIAG_OOM_BOOT;
        d->rec_manual     = 1;
        d->rec_eng_sel    = SEL_ALT;
        d->rec_buf_sel    = is_2b_log ? BUF_2BUF : BUF_AUTO;
        d->rec_shave_mode = 0;
        d->rec_reg_mode   = 1;
        d->rec_pace_order = is_2b_log ? 3 : 2;
        d->rec_post_mid   = 1;
        d->rec_row        = 1;
        snprintf(d->diag_cause, sizeof(d->diag_cause), "GPU WORK MEMORY ALLOC FAILED");
        snprintf(d->diag_stage, sizeof(d->diag_stage), "NOT ENOUGH FREE DIRECT MEMORY");
        snprintf(d->diag_advice, sizeof(d->diag_advice), "ALT ENGINE + VALVE+SHAVE + TOP GROUP (R2)");
        snprintf(d->diag_detail, sizeof(d->diag_detail),
                 "Game booted without crashing, but VCFG could not allocate DirectMemory for optical flow textures (dmem_FAIL in log).");
        snprintf(d->step1, sizeof(d->step1), "1. Press R2 to set ALT Engine + VALVE+SHAVE + TOP GROUP.");
        snprintf(d->step2, sizeof(d->step2), "2. Enables MID ONLY post target to save memory.");
        snprintf(d->step3, sizeof(d->step3), "3. Re-launch with SQUARE.");
    } else if (is_2b_log && !fg_ran &&
               (strstr(ts, "inert") || strstr(ts, "no mid") || strstr(bs, "pcopy=0") || strstr(bs, "want_pcopy=0") || strstr(bs, "two_buffer=0"))) {
        d->diag_kind      = DIAG_2BUF_NEEDED;
        d->rec_manual     = 1;
        d->rec_eng_sel    = auto_alt(g->id) ? SEL_ALT : SEL_CLASSIC;
        d->rec_buf_sel    = BUF_2BUF;
        d->rec_shave_mode = 1;
        d->rec_reg_mode   = 1;
        d->rec_pace_order = 3;
        d->rec_post_mid   = 1;
        d->rec_row        = 1;
        snprintf(d->diag_cause, sizeof(d->diag_cause), "2-BUFFER / 1X1 FLIP WITHOUT PCOPY");
        snprintf(d->diag_stage, sizeof(d->diag_stage), "ONLY 2 DISPLAY BUFFERS REGISTERED");
        snprintf(d->diag_advice, sizeof(d->diag_advice), "2-BUFFER PCOPY + TOP GROUP + TIMER (R2)");
        snprintf(d->diag_detail, sizeof(d->diag_detail),
                 "Log shows a 2-buffer swapchain without extra mid-buffer slots. Press R2 to configure 2-BUFFER present copy + TOP GROUP + TIMER pacing.");
        snprintf(d->step1, sizeof(d->step1), "1. Press R2 to apply 2-BUFFER + TOP GROUP + TIMER.");
        snprintf(d->step2, sizeof(d->step2), "2. Sets VALVE ONLY + MID ONLY automatically.");
        snprintf(d->step3, sizeof(d->step3), "3. Press SQUARE to launch.");
    } else if (fg_ran) {
        if (strstr(ts, "STALL SNAPSHOT") || pace_to > 10 || missed_vb > 40) {
            d->diag_kind      = DIAG_FLIP_STALL;
            d->rec_manual     = 1;
            d->rec_eng_sel    = SEL_AUTO;
            d->rec_buf_sel    = is_2b_log ? BUF_2BUF : BUF_AUTO;
            d->rec_shave_mode = (multi_reg >= 2) ? 1 : 0;
            d->rec_reg_mode   = 1;
            d->rec_pace_order = (logged_pace == 2 || is_2b_log) ? 3 : 2;
            d->rec_post_mid   = 1;
            d->rec_row        = 4;
            snprintf(d->diag_cause, sizeof(d->diag_cause), "FLIP PACING CONTENTION (%u TO, %u MISS)", pace_to, missed_vb);
            snprintf(d->diag_stage, sizeof(d->diag_stage), "FG ACTIVE WITH CADENCE STALLS");
            snprintf(d->diag_advice, sizeof(d->diag_advice), "%s + TOP GROUP + MID ONLY (R2)",
                     d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->diag_detail, sizeof(d->diag_detail),
                     "FG ran, but trace recorded %u pace timeouts and %u missed vblanks. Press R2 to apply %s pacing + TOP GROUP + MID ONLY.",
                     pace_to, missed_vb, d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->step1, sizeof(d->step1), "1. Press R2 to apply %s + TOP GROUP + MID ONLY.",
                     d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->step2, sizeof(d->step2), "2. Eliminates flip queue contention on loading/cutscenes.");
            snprintf(d->step3, sizeof(d->step3), "3. Press SQUARE to launch.");
        } else {
            d->diag_kind = DIAG_OK;
            if (g->experimental && is_2b_log) {
                d->rec_manual     = 1;
                d->rec_eng_sel    = auto_alt(g->id) ? SEL_ALT : SEL_CLASSIC;
                d->rec_buf_sel    = BUF_2BUF;
                d->rec_shave_mode = 1;
                d->rec_reg_mode   = 1;
                d->rec_pace_order = 3;
                d->rec_post_mid   = 1;
            }
            snprintf(d->diag_cause, sizeof(d->diag_cause), "LAST SESSION RAN NORMALLY");
            snprintf(d->diag_stage, sizeof(d->diag_stage), "FG PIPELINE ACTIVE & VERIFIED");
            snprintf(d->diag_advice, sizeof(d->diag_advice), "OPTIMAL PROFILE VERIFIED (R2: SYNC)");
            snprintf(d->diag_detail, sizeof(d->diag_detail),
                     "Last session of %s reached steady-state 60Hz frame generation with no crashes. Press R2 to ensure the verified profile is synced.",
                     g->id);
            snprintf(d->step1, sizeof(d->step1), "1. Press R2 to sync the verified Advisor profile.");
            snprintf(d->step2, sizeof(d->step2), "2. Garbled blocks? Cycle Tile Mode (0x0D / 0x0E / LINEAR).");
            snprintf(d->step3, sizeof(d->step3), "3. Uncapped 35-45 FPS? Set FPS Lock -> 30 FPS.");
        }
    } else if (bb || tb) {
        d->diag_kind = DIAG_INERT;
        if (has_hdr_inert) {
            d->rec_manual = 0;
            snprintf(d->diag_cause, sizeof(d->diag_cause), "PS4 HDR10 OUTPUT IS ENABLED");
            snprintf(d->diag_stage, sizeof(d->diag_stage), "10-BIT PQ SWAPCHAIN (0x88740000)");
            snprintf(d->diag_advice, sizeof(d->diag_advice), "DISABLE HDR IN PS4 VIDEO SETTINGS");
            snprintf(d->diag_detail, sizeof(d->diag_detail),
                     "Game registered a 10-bit HDR10 display buffer. Turn off HDR in PS4 Settings -> Sound and Screen -> Video Output Settings to run 60Hz FG.");
            snprintf(d->step1, sizeof(d->step1), "1. Open PS4 Settings -> Sound and Screen -> Video Output.");
            snprintf(d->step2, sizeof(d->step2), "2. Set HDR -> Off (switches game to 8-bit SDR).");
            snprintf(d->step3, sizeof(d->step3), "3. Launch %s again with SQUARE.", g->id);
        } else if (has_60fps_inert) {
            d->rec_manual     = 1;
            d->rec_eng_sel    = SEL_AUTO;
            d->rec_buf_sel    = is_2b_log ? BUF_2BUF : BUF_AUTO;
            d->rec_shave_mode = 1;
            d->rec_reg_mode   = 1;
            d->rec_pace_order = 2;
            d->rec_post_mid   = 1;
            d->rec_fps_lock   = 0;
            d->rec_row        = 6;
            snprintf(d->diag_cause, sizeof(d->diag_cause), "UNLOCKED / 60 FPS NATIVE DETECTED");
            snprintf(d->diag_stage, sizeof(d->diag_stage), "FG AUTO-PAUSED ABOVE 48 FPS");
            snprintf(d->diag_advice, sizeof(d->diag_advice), "30 FPS LOCK + POST-SUBMIT (R2)");
            snprintf(d->diag_detail, sizeof(d->diag_detail),
                     "Game ran unlocked above 48 FPS so native 60Hz bypass engaged. Press R2 to lock game cadence to 30 FPS and generate 60Hz FG.");
            snprintf(d->step1, sizeof(d->step1), "1. Press R2 to lock game to 30 FPS + POST-SUBMIT.");
            snprintf(d->step2, sizeof(d->step2), "2. Sets TOP GROUP + MID ONLY automatically.");
            snprintf(d->step3, sizeof(d->step3), "3. Press SQUARE to launch.");
        } else {
            d->rec_manual     = 1;
            d->rec_eng_sel    = auto_alt(g->id) ? SEL_ALT : SEL_CLASSIC;
            d->rec_buf_sel    = is_2b_log ? BUF_2BUF : BUF_AUTO;
            d->rec_shave_mode = 1;
            d->rec_reg_mode   = 1;
            d->rec_pace_order = is_2b_log ? 3 : 2;
            d->rec_post_mid   = 1;
            d->rec_row        = (multi_reg >= 2) ? 3 : 4;
            snprintf(d->diag_cause, sizeof(d->diag_cause),
                     multi_reg >= 2 ? "LOADING SCREEN BUFFER RE-REG" :
                     (has_vout_only ? "DIRECT VIDEOOUT FLIP PATH" : "NO FG FRAMES GENERATED YET"));
            snprintf(d->diag_stage, sizeof(d->diag_stage),
                     multi_reg >= 2 ? "STREAMING SWAPCHAIN RESET" : "GAME EXITED EARLY OR INERT PATH");
            snprintf(d->diag_advice, sizeof(d->diag_advice), "TOP GROUP + %s + MID ONLY (R2)",
                     d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->diag_detail, sizeof(d->diag_detail),
                     "Boot log exists for %s, but no FG frames were recorded. Press R2 to apply TOP GROUP (16-N) + %s pacing + VALVE ONLY.",
                     g->id, d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->step1, sizeof(d->step1), "1. Press R2 to apply TOP GROUP + %s + VALVE ONLY.",
                     d->rec_pace_order == 3 ? "TIMER (PAIR)" : "POST-SUBMIT");
            snprintf(d->step2, sizeof(d->step2), "2. Isolates loading-screen buffer resets and flip pacing.");
            snprintf(d->step3, sizeof(d->step3), "3. Press SQUARE to launch and play past the menu.");
        }
    }

    // Compare current settings against the full Advisor target recipe (rec_*):
    int mask = 0;
    if (d->rec_manual == 0) {
        if (d->manual_mode != 0) {
            if (g->eng_sel != SEL_AUTO || g->buf_sel != BUF_AUTO) mask |= (1 << 1);
            if (d->shave_mode != 0) mask |= (1 << 2);
            if (d->reg_mode   != 0) mask |= (1 << 3);
            if (d->pace_order != 0) mask |= (1 << 4);
            if (d->post_mid   != 0) mask |= (1 << 5);
            if (d->fps_lock   != 0) mask |= (1 << 6);
            if (d->tile_mode  != 0) mask |= (1 << 7);
            if (!mask) mask = (1 << 1);
        }
    } else {
        int cur_eng = d->manual_mode ? g->eng_sel : SEL_AUTO;
        int cur_buf = d->manual_mode ? g->buf_sel : BUF_AUTO;
        int cur_shv = d->manual_mode ? d->shave_mode : 0;
        int cur_reg = d->manual_mode ? d->reg_mode   : 0;
        int cur_pac = d->manual_mode ? d->pace_order : 0;
        int cur_pst = d->manual_mode ? d->post_mid   : 0;
        int cur_lck = d->manual_mode ? d->fps_lock   : 0;
        int cur_til = d->manual_mode ? d->tile_mode  : 0;
        if (cur_eng != d->rec_eng_sel || cur_buf != d->rec_buf_sel) mask |= (1 << 1);
        if (cur_shv != d->rec_shave_mode) mask |= (1 << 2);
        if (cur_reg != d->rec_reg_mode)   mask |= (1 << 3);
        if (cur_pac != d->rec_pace_order) mask |= (1 << 4);
        if (cur_pst != d->rec_post_mid)   mask |= (1 << 5);
        if (cur_lck != d->rec_fps_lock)   mask |= (1 << 6);
        if (cur_til != d->rec_tile_mode)  mask |= (1 << 7);
        if (!d->manual_mode && !mask) mask = (1 << 1);
    }
    d->rec_mask    = mask;
    d->rec_applied = (mask == 0) ? 1 : 0;
    if (d->rec_applied) {
        d->rec_row = 0;
    } else if (d->rec_row == 0 || !(mask & (1 << d->rec_row))) {
        for (int i = 1; i <= 7; i++) {
            if (mask & (1 << i)) { d->rec_row = i; break; }
        }
    }

    free(cb); free(bb); free(tb);
}
static void load_settings(App *a)
{
    size_t n; char path[96];
    unsigned char *l = read_all(CFGDIR "/vcfg_launcher.ini", &n);
    const char *t = l ? (const char *)l : "";
    a->defaults = (Settings){ ini_int(t, "fg", 1), ini_int(t, "preset", 0), ini_int(t, "sharp", 40), ini_int(t, "enhance", 1), ini_int(t, "overlay", 0) };
    a->overlay_pos = ini_int(t, "overlay_pos", 4); a->overlay_opacity = ini_int(t, "overlay_opacity", 80);
    a->menu_combo = menu_combo_sanitize((uint32_t)ini_int(t, "menu_combo", (int)MENU_COMBO_DEFAULT));   // preset or recorded mask, else L3 + R3
    a->dark_theme = ini_int(t, "dark_theme", 1);
    a->net_telemetry = ini_int(t, "auto_send", 1);
    ini_str(t, "telemetry_host", a->telemetry_host, sizeof(a->telemetry_host), "62.60.230.108");
    if (!a->telemetry_host[0]) snprintf(a->telemetry_host, sizeof(a->telemetry_host), "62.60.230.108");
    a->telemetry_port = ini_int(t, "telemetry_port", 8765);
    if (a->telemetry_port <= 0) a->telemetry_port = 8765;
    free(l);
    unsigned char *tl = read_all(CFGDIR "/telemetry.ini", &n);
    if (!tl) tl = read_all("/data/vcfg_telemetry.ini", &n);
    if (tl) {
        const char *ts = (const char *)tl;
        char thost[64] = "";
        ini_str(ts, "host", thost, sizeof(thost), "");
        if (!thost[0]) ini_str(ts, "telemetry_host", thost, sizeof(thost), "");
        if (thost[0]) snprintf(a->telemetry_host, sizeof(a->telemetry_host), "%s", thost);
        int tp = ini_int(ts, "port", ini_int(ts, "telemetry_port", 0));
        if (tp > 0) a->telemetry_port = tp;
        a->net_telemetry = ini_int(ts, "auto_send", a->net_telemetry);
        free(tl);
    }
    unsigned char *e = read_all(ENGFILE, &n);
    for (int i = 0; i < a->ngames; i++) {
        int v = e ? ini_int((const char *)e, a->games[i].id, SEL_AUTO) : SEL_AUTO;
        a->games[i].eng_sel = (v >= 0 && v < SEL_COUNT) ? v : SEL_AUTO;
    }
    free(e);
    unsigned char *bf = read_all(BUFFILE, &n);
    for (int i = 0; i < a->ngames; i++) {
        int v = bf ? ini_int((const char *)bf, a->games[i].id, BUF_AUTO) : BUF_AUTO;
        a->games[i].buf_sel = (v >= 0 && v < BUF_COUNT) ? v : BUF_AUTO;
    }
    free(bf);
    for (int i = 0; i < a->ngames; i++) {
        Game *g = &a->games[i]; g->s = a->defaults; if (g->testing || g->experimental) g->s.fg = 0;
        memset(&g->dev, 0, sizeof(g->dev));
        if (g->eng_sel != SEL_AUTO || g->buf_sel != BUF_AUTO) g->dev.manual_mode = 1;
        snprintf(path, sizeof(path), CFGDIR "/adapt-%s.ini", g->id);
        unsigned char *ab = read_all(path, &n);
        if (ab) {
            const char *as = (const char *)ab;
            g->dev.shave_mode = ini_int(as, "shave_mode", g->dev.shave_mode);
            g->dev.reg_mode   = ini_int(as, "reg_mode", g->dev.reg_mode);
            g->dev.pace_order = ini_int(as, "pace_order", g->dev.pace_order);
            free(ab);
        }
        if (uses_vcfg_ini(g)) {
            unsigned char *b = read_all(CFGDIR "/vcfg.ini", &n);
            if (b) {
                const char *s = (const char *)b;
                g->s = (Settings){ ini_int(s, "enabled", 1), ini_int(s, "preset", 0), ini_int(s, "sharp", 40), ini_int(s, "enhance", 1), ini_int(s, "overlay", 0) };
                free(b);
            }
        } else {
            snprintf(path, sizeof(path), CFGDIR "/config-%s.ini", g->id);
            unsigned char *b = read_all(path, &n);
            if (b) {
                const char *s = (const char *)b;
                g->s = (Settings){ ini_int(s, "framegen", g->experimental ? 0 : 1), ini_int(s, "preset", 0), ini_int(s, "sharpness", 40), ini_int(s, "enhance", 1), ini_int(s, "overlay", 0) };
                int mm = ini_int(s, "manual_mode", 0);
                if (mm > 0) g->dev.manual_mode = 1;
                if (g->dev.manual_mode) {
                    int sm = ini_int(s, "shave_mode", 0); if (sm > g->dev.shave_mode) g->dev.shave_mode = sm;
                    int rm = ini_int(s, "reg_mode", 0);   if (rm > g->dev.reg_mode)   g->dev.reg_mode   = rm;
                    int po = ini_int(s, "pace_order", 0); if (po > 0) g->dev.pace_order = po;
                    int pm = ini_int(s, "post_mid", 0);   if (pm > 0) g->dev.post_mid   = pm;
                    g->dev.fps_lock   = lock_to_dev(ini_int(s, "fps_lock", 30));
                    g->dev.tile_mode  = tile_to_dev(ini_int(s, "tile_idx", -1));
                }
                free(b);
            }
        }
        refresh_dev_status(g);
    }
}
static void save_settings(App *a)
{
    char buf[768], path[96];
    sceKernelMkdir(CFGDIR, 0777);
    const unsigned combo = menu_combo_sanitize(a->menu_combo);   // validated mask, written to every file the plugins read
    snprintf(buf, sizeof(buf),
             "fg=%d\npreset=%d\nsharp=%d\nenhance=%d\noverlay=%d\noverlay_pos=%d\noverlay_opacity=%d\nmenu_combo=%u\n"
             "dark_theme=%d\nauto_send=%d\ntelemetry_host=%s\ntelemetry_port=%d\n",
             a->defaults.fg, a->defaults.preset, a->defaults.sharp, a->defaults.enhance, a->defaults.overlay,
             a->overlay_pos, a->overlay_opacity, combo, a->dark_theme,
             a->net_telemetry, a->telemetry_host, a->telemetry_port > 0 ? a->telemetry_port : 8765);
    write_all(CFGDIR "/vcfg_launcher.ini", buf, strlen(buf));
    for (int i = 0; i < a->ngames; i++) {
        if (!a->games[i].dev.manual_mode) {
            a->games[i].eng_sel = SEL_AUTO;
            a->games[i].buf_sel = BUF_AUTO;
        }
    }
    {
        static char eb[MAPMAX * 16]; size_t en = 0;
        for (int i = 0; i < a->ngames; i++)
            if (a->games[i].eng_sel != SEL_AUTO && en + 16 < sizeof(eb))
                en += (size_t)snprintf(eb + en, sizeof(eb) - en, "%s=%d\n", a->games[i].id, a->games[i].eng_sel);
        if (en) write_all(ENGFILE, eb, en); else remove(ENGFILE);
    }
    {
        static char bb[MAPMAX * 16]; size_t bn = 0;
        for (int i = 0; i < a->ngames; i++)
            if (a->games[i].buf_sel != BUF_AUTO && bn + 16 < sizeof(bb))
                bn += (size_t)snprintf(bb + bn, sizeof(bb) - bn, "%s=%d\n", a->games[i].id, a->games[i].buf_sel);
        if (bn) write_all(BUFFILE, bb, bn); else remove(BUFFILE);
    }
    for (int i = 0; i < a->ngames; i++) {
        Game *g = &a->games[i]; Settings *s = &g->s; DevCfg *d = &g->dev;
        if (!g->installed) continue;
        if (uses_vcfg_ini(g)) {
            snprintf(buf, sizeof(buf), "enabled=%d\npreset=%d\nsharp=%d\nenhance=%d\noverlay=%d\nopacity=%d\nmenu_combo=%u\n",
                     s->fg, s->preset, s->sharp, s->enhance, s->overlay, a->overlay_opacity, combo);
            write_all(CFGDIR "/vcfg.ini", buf, strlen(buf));
        } else {
            int bmode = game_buf(g);
            int pcopy = d->manual_mode ? ((g->buf_sel == BUF_2BUF) ? 1 : (g->buf_sel == BUF_STD) ? 2 : 0) : 0;
            int sm = d->manual_mode ? d->shave_mode : 0;
            int rm = d->manual_mode ? d->reg_mode   : 0;
            int po = d->manual_mode ? d->pace_order : 0;
            int pm = d->manual_mode ? d->post_mid   : 0;
            int lk = d->manual_mode ? k_dev_locks[d->fps_lock >= 0 && d->fps_lock < 4 ? d->fps_lock : 0] : 30;
            int ti = d->manual_mode ? k_dev_tiles[d->tile_mode >= 0 && d->tile_mode < 7 ? d->tile_mode : 0] : -1;
            snprintf(path, sizeof(path), CFGDIR "/config-%s.ini", g->id);
            snprintf(buf, sizeof(buf),
                     "framegen=%d\npreset=%d\nsharpness=%d\nenhance=%d\noverlay=%d\nmenu_opacity=%d\nmenu_combo=%u\n"
                     "manual_mode=%d\ntwo_buffer=%d\npresent_copy=%d\nshave_mode=%d\nreg_mode=%d\npace_order=%d\npost_mid=%d\nfps_lock=%d\ntile_idx=%d\n",
                     s->fg, s->preset, s->sharp, s->enhance, s->overlay, a->overlay_opacity, combo,
                     d->manual_mode, bmode == BUF_2BUF, pcopy, sm, rm, po, pm, lk, ti);
            write_all(path, buf, strlen(buf));
            snprintf(path, sizeof(path), CFGDIR "/tile_%s", g->id);
            if (ti >= 0) { char tb[16]; snprintf(tb, sizeof(tb), "%d\n", ti); write_all(path, tb, strlen(tb)); }
            else remove(path);
            if (s->fg) {
                snprintf(path, sizeof(path), CFGDIR "/safe-%s.state", g->id); remove(path);
                snprintf(path, sizeof(path), CFGDIR "/safe-%s.boot", g->id); remove(path);
                refresh_dev_status(g);
            }
        }
    }
    vlog("settings saved\n");
}
static void addlog(App *a, const char *msg);
static void remap_if_changed(App *a);
static void do_reset_dev(App *a)
{
    if (!a->ngames) return;
    Game *g = &a->games[a->sel];
    char path[96];
    g->eng_sel = SEL_AUTO;
    g->buf_sel = BUF_AUTO;
    memset(&g->dev, 0, sizeof(g->dev));
    snprintf(path, sizeof(path), CFGDIR "/safe-%s.state", g->id); remove(path);
    snprintf(path, sizeof(path), CFGDIR "/safe-%s.boot", g->id);  remove(path);
    snprintf(path, sizeof(path), CFGDIR "/adapt-%s.ini", g->id);  remove(path);
    snprintf(path, sizeof(path), CFGDIR "/crash-%s.txt", g->id);  remove(path);
    snprintf(path, sizeof(path), CFGDIR "/tile_%s", g->id);       remove(path);
    save_settings(a);
    remap_if_changed(a);
    refresh_dev_status(g);
    snprintf(a->status, sizeof(a->status), "AUTO (ALGORITHM) RESTORED FOR %s", g->id);
    addlog(a, a->status);
}
static void do_apply_advice(App *a)
{
    if (!a->ngames) return;
    Game *g = &a->games[a->sel];
    DevCfg *d = &g->dev;
    char path[96];
    refresh_dev_status(g);
    if (d->rec_applied && g->s.fg) {
        snprintf(a->status, sizeof(a->status), "ADVISOR PROFILE ALREADY ACTIVE FOR %s (NO CHANGES)", g->id);
        addlog(a, a->status);
        return;
    }
    int focus_row = d->rec_row > 0 ? d->rec_row : 0;
    d->manual_mode = d->rec_manual;
    g->eng_sel     = d->rec_eng_sel;
    g->buf_sel     = d->rec_buf_sel;
    d->shave_mode  = d->rec_shave_mode;
    d->reg_mode    = d->rec_reg_mode;
    d->pace_order  = d->rec_pace_order;
    d->post_mid    = d->rec_post_mid;
    d->fps_lock    = d->rec_fps_lock;
    d->tile_mode   = d->rec_tile_mode;
    g->s.fg = 1;
    if (focus_row >= 1 && focus_row <= 7) a->row = focus_row;
    snprintf(path, sizeof(path), CFGDIR "/safe-%s.state", g->id); remove(path);
    snprintf(path, sizeof(path), CFGDIR "/safe-%s.boot", g->id);  remove(path);
    if (!d->manual_mode) {
        snprintf(path, sizeof(path), CFGDIR "/adapt-%s.ini", g->id); remove(path);
        snprintf(path, sizeof(path), CFGDIR "/tile_%s", g->id);      remove(path);
    }
    save_settings(a);
    remap_if_changed(a);
    refresh_dev_status(g);
    snprintf(a->status, sizeof(a->status),
             d->manual_mode ? "ADVISOR PROFILE APPLIED & SAVED FOR %s!" : "ADVISOR AUTO PROFILE APPLIED FOR %s!",
             g->id);
    addlog(a, a->status);
}

// ---- plugins.ini: point our titles at our files; keep foreign lines; [default] stays last ----
// Verified titles always get their plugin. Experimental titles get the selected engine only while switched on;
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
    for (unsigned ai = 0; ai < sizeof(k_vcfg_title_aliases) / sizeof(k_vcfg_title_aliases[0]) && g_nmap < MAPMAX; ai++) {
        const char *id = k_vcfg_title_aliases[ai].id;
        const Supported *sup = sup_for_title(id);
        if (!sup) continue;
        int dup = 0;
        for (int k = 0; k < g_nmap; k++) if (!strcmp(g_map[k].id, id)) { dup = 1; break; }
        if (dup) continue;
        Map *m = &g_map[g_nmap++]; snprintf(m->id, sizeof(m->id), "%s", id);
        const Game *g = find_game(a, id);
        if (!g) g = find_game(a, sup->id);
        m->prx = sup->testing ? NULL : g ? prx_of(game_engine(g)) : sup->prx; m->keep_empty = 1;
    }
    for (int i = 0; i < a->ngames && g_nmap < MAPMAX; i++) {
        const Game *g = &a->games[i]; if (!g->experimental) continue;
        int dup = 0;
        for (int k = 0; k < g_nmap; k++) if (!strcmp(g_map[k].id, g->id)) { dup = 1; break; }
        if (dup) continue;
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
// "Turned off" marker: the app installs and maps the plugin by itself at every start, so a plain Square (remove from plugins.ini) was undone
// by the next start. While this file exists the app neither installs nor maps anything; Cross (install) removes it.
#define OFFFILE CFGDIR "/vcfg_off"
static int is_off(void) { FILE *f = fopen(OFFFILE, "rb"); if (f) { fclose(f); return 1; } return 0; }
static void set_off(int on) { if (on) write_all(OFFFILE, "1\n", 2); else remove(OFFFILE); }
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
static int clean_dir(const char *dir, const char *const *prefixes, int np, const char *suffix);
static uint64_t exp_sig(const App *a);
static uint64_t g_exp_written;
static void do_install(App *a)
{
    set_off(0);
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
    // Clear stale self-adapt / safe-watchdog / legacy experiment files from previous plugin versions on install/update:
    static const char *const stale_pfx[] = {
        "adapt-", "safe-", "state-", "vcfg_ver_", "tile_",
        "cut_blocks_", "postfix_", "gridmix_", "bench_capture", "a20_"
    };
    clean_dir(CFGDIR, stale_pfx, (int)(sizeof(stale_pfx) / sizeof(stale_pfx[0])), NULL);
    remove(ENGFILE);
    remove(BUFFILE);
    for (int i = 0; i < a->ngames; i++) {
        a->games[i].eng_sel = SEL_AUTO;
        a->games[i].buf_sel = BUF_AUTO;
        memset(&a->games[i].dev, 0, sizeof(a->games[i].dev));
    }
    save_settings(a);
    do_ini(a, 1);
    g_exp_written = exp_sig(a);
    a->plugin_state = plugin_state();
}

// Delete every VCFG file: the plugins.ini mapping (backed up first), the plugin files, settings, traces and logs. The app stays off until Cross.
static int clean_dir(const char *dir, const char *const *prefixes, int np, const char *suffix)
{
    DIR *d = opendir(dir); if (!d) return 0; int n = 0; struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        int hit = (np <= 0);
        for (int i = 0; i < np; i++) if (!strncmp(e->d_name, prefixes[i], strlen(prefixes[i]))) hit = 1;
        if (hit && suffix) { size_t l = strlen(e->d_name), q = strlen(suffix); if (l < q || strcmp(e->d_name + l - q, suffix)) hit = 0; }
        if (!hit) continue;
        if (!strcmp(e->d_name, "telemetry.ini")) continue;
        char p[192]; snprintf(p, sizeof(p), "%s/%s", dir, e->d_name);
        if (remove(p) == 0) n++;
    }
    closedir(d); return n;
}
static void do_clean(App *a)
{
    do_ini(a, 0);
    static const char *const plug[] = { "vcfg", "bb_probe" };
    int np = clean_dir("/data/GoldHEN/plugins", plug, 2, ".prx");
    static const char *const bak[] = { "plugins.ini.vcfg_backup_" };
    clean_dir("/data/GoldHEN", bak, 1, NULL);
    int nc = clean_dir(CFGDIR, NULL, 0, NULL);
    remove("/data/vcfg_report.txt");
    set_off(1);
    a->debug_log = 0;
    a->defaults = (Settings){ 1, 0, 40, 1, 0 };
    a->overlay_pos = 4;
    a->overlay_opacity = 80;
    a->menu_combo = MENU_COMBO_DEFAULT;
    a->dark_theme = 1;
    for (int i = 0; i < a->ngames; i++) {
        Game *g = &a->games[i];
        g->eng_sel = SEL_AUTO;
        g->buf_sel = BUF_AUTO;
        memset(&g->dev, 0, sizeof(g->dev));
        g->s = a->defaults;
        if (g->testing || g->experimental) g->s.fg = 0;
        refresh_dev_status(g);
    }
    char m[96]; snprintf(m, sizeof(m), "WIPED %d PLUGINS + %d FILES & RESET RAM TO FACTORY ZERO.", np, nc); addlog(a, m);
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
    if (is_off()) return;   // turned off: plugins.ini stays without VCFG
    size_t n = 0, un = 0;
    unsigned char *ini = read_all(INI, &n);
    build_map(a);
    char *u = ini_update(ini ? (char *)ini : "", 1, &un);
    int differs = (!ini || !u || un != n || memcmp(u, ini, n) != 0);
    free(ini);
    free(u);
    if (!differs && exp_sig(a) == g_exp_written) return;
    if (!do_ini(a, 1)) g_exp_written = exp_sig(a);
}

// ---- debug log switch and bug report ----
#define VCFG_STR2(x) #x
#define VCFG_STR(x) VCFG_STR2(x)
#ifdef VCFG_VER
#define VCFG_VERSION VCFG_STR(VCFG_VER)   // build_pkg.ps1 passes -DVCFG_VER=<version>
#else
#define VCFG_VERSION "dev"
#endif
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
    int frame = strstr(t, "first SubmitAndFlip") || strstr(t, "first VideoOutSubmitFlip") || strstr(t, "A22_TITLE") || strstr(t, "gnm #1 ");
    int gpu = strstr(t, "gpu_init: ok") || strstr(t, "A20_ALLOC");
    int post = strstr(t, "inject #") != NULL || strstr(t, "vout inject #") != NULL;
    int fg = strstr(t, "mode4 #") || strstr(t, "vout fg #") || strstr(t, "kind=1") ||
             (post && !strstr(t, "inert:") && !strstr(t, "framegen=0") && strstr(t, "extra buffer(s)"));
    int menu = strstr(t, "menu opened") || strstr(t, "A20_MENU open=1");
    bprintf(r, "%s, %s: %s | %s | first frame %s, GPU memory %s, FG frames %s, OSD/post injects %s, menu used %s\n", id, which, v, secs,
            frame ? "yes" : "NO", gpu ? "yes" : "no", fg ? "yes" : "no", post ? "yes" : "no", menu ? "yes" : "no");
    static const char *keys[] = { "STALL SNAPSHOT", "inert", "FAIL", "fail", "stop", "NATIVE_ONLY", "refus", "no memory", "dcb_unreadable" };
    int shown = 0;
    for (unsigned k = 0; k < sizeof(keys) / sizeof(keys[0]) && shown < 4; k++)
        if (find_line(t, keys[k], line, sizeof(line))) { bprintf(r, "    reason? %s\n", line); shown++; }
    const char *end = strstr(t, "---- end of trace ----");
    if (end && end > t) {   // last log line before the end marker: where the game start stopped if it crashed
        const char *e = end - 1, *b = e; while (b > t && b[-1] != '\n') b--;
        size_t l = (size_t)(e - b); if (l >= sizeof(line)) l = sizeof(line) - 1; memcpy(line, b, l); line[l] = 0;
        bprintf(r, "    last line: %s\n", line);
    }
}
// Every trace-<TITLE>[.prev].txt and boot-<TITLE>[.prev].txt in CFGDIR, sorted by title ID (not only the app's game list).
typedef struct { char id[12]; int prev; } TraceFile;
static int list_traces(TraceFile *out, int cap, int *err)
{
    *err = 0;
    DIR *d = opendir(CFGDIR);
    if (!d) { *err = errno; return 0; }
    int n = 0; struct dirent *e;
    while ((e = readdir(d)) && n < cap) {
        size_t pfx = 0;
        if (!strncmp(e->d_name, "trace-", 6)) pfx = 6;
        else if (!strncmp(e->d_name, "boot-", 5)) pfx = 5;
        else continue;
        size_t l = strlen(e->d_name); int prev = 0; const char *suf = ".txt";
        if (l > 9 && !strcmp(e->d_name + l - 9, ".prev.txt")) { prev = 1; suf = ".prev.txt"; }
        else if (l <= 4 || strcmp(e->d_name + l - 4, ".txt")) continue;
        size_t idl = l - pfx - strlen(suf);
        if (idl < 4 || idl >= sizeof(out[0].id)) continue;
        char id[12];
        memcpy(id, e->d_name + pfx, idl); id[idl] = 0;
        int dup = 0;
        for (int k = 0; k < n; k++) if (!strcmp(out[k].id, id) && out[k].prev == prev) { dup = 1; break; }
        if (dup) continue;
        memcpy(out[n].id, id, sizeof(out[n].id)); out[n].prev = prev; n++;
    }
    closedir(d);
    for (int i = 1; i < n; i++) for (int j = i; j > 0; j--) {
        int c = strcmp(out[j].id, out[j - 1].id);
        if (c > 0 || (c == 0 && out[j].prev >= out[j - 1].prev)) break;
        TraceFile t = out[j]; out[j] = out[j - 1]; out[j - 1] = t;
    }
    return n;
}
// Last non-empty line of a text buffer (used for boot-<TITLE>.txt: the last stage reached before a start stopped).
static int last_line(const char *t, size_t n, char *out, size_t on)
{
    while (n && (t[n - 1] == '\n' || t[n - 1] == '\r')) n--;
    if (!n) return 0;
    size_t b = n; while (b && t[b - 1] != '\n') b--;
    size_t l = n - b; if (l >= on) l = on - 1;
    memcpy(out, t + b, l); out[l] = 0; return 1;
}
static uint32_t fnv1a_buf(const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < len; i++) h = (h ^ p[i]) * 16777619u;
    return h ? h : 1u;
}

static int net_send_report(const char *host, int port, const char *title_id, const char *kind,
                           const char *body, size_t len, char *id_out, size_t id_cap)
{
    if (id_out && id_cap) id_out[0] = 0;
    if (!host || !host[0] || !body || !len) return -1;
    if (port <= 0 || port > 65535) port = 8765;

    struct sockaddr_in sa;
    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, host, &sa.sin_addr) != 1) return -2;

    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return -3;

    // Non-blocking connect with 2.5s timeout so offline VPS never freezes the UI
    int fl = fcntl(s, F_GETFL, 0);
    if (fl >= 0) fcntl(s, F_SETFL, fl | O_NONBLOCK);
    int rc = connect(s, (struct sockaddr *)&sa, sizeof(sa));
    if (rc < 0 && errno != EINPROGRESS) { close(s); return -4; }
    if (rc < 0) {
        fd_set wfds; FD_ZERO(&wfds); FD_SET(s, &wfds);
        struct timeval tv = { 2, 500000 };
        if (select(s + 1, NULL, &wfds, NULL, &tv) <= 0) { close(s); return -5; }
        int so_err = 0; socklen_t slen = sizeof(so_err);
        if (getsockopt(s, SOL_SOCKET, SO_ERROR, &so_err, &slen) < 0 || so_err != 0) { close(s); return -6; }
    }
    if (fl >= 0) fcntl(s, F_SETFL, fl & ~O_NONBLOCK);
    struct timeval tv_io = { 3, 0 };
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv_io, sizeof(tv_io));
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv_io, sizeof(tv_io));

    size_t send_len = len > (500u << 10) ? (500u << 10) : len;
    char hdr[512];
    int hlen = snprintf(hdr, sizeof(hdr),
                        "POST /api/v1/report HTTP/1.1\r\n"
                        "Host: %s:%d\r\n"
                        "User-Agent: VCFG-PS4/%s\r\n"
                        "Content-Type: text/plain; charset=utf-8\r\n"
                        "X-VCFG-Title: %s\r\n"
                        "X-VCFG-Kind: %s\r\n"
                        "X-VCFG-Version: %s\r\n"
                        "Content-Length: %zu\r\n"
                        "Connection: close\r\n\r\n",
                        host, port, VCFG_VERSION,
                        (title_id && title_id[0]) ? title_id : "GLOBAL",
                        (kind && kind[0]) ? kind : "manual",
                        VCFG_VERSION, send_len);
    if (send(s, hdr, (size_t)hlen, 0) != hlen) { close(s); return -7; }
    size_t off = 0;
    while (off < send_len) {
        size_t chunk = send_len - off > 16384 ? 16384 : send_len - off;
        ssize_t nw = send(s, body + off, chunk, 0);
        if (nw <= 0) { close(s); return -8; }
        off += (size_t)nw;
    }

    char resp[512];
    ssize_t nr = recv(s, resp, sizeof(resp) - 1, 0);
    close(s);
    if (nr <= 0) return -9;
    resp[nr] = 0;
    if (!strstr(resp, "200")) return -10;
    const char *hash = strchr(resp, '#');
    if (hash && id_out && id_cap >= 8) {
        size_t k = 0;
        hash++;
        while (hash[k] && ((hash[k] >= '0' && hash[k] <= '9') || (hash[k] >= 'A' && hash[k] <= 'F') || (hash[k] >= 'a' && hash[k] <= 'f')) && k + 1 < id_cap && k < 8) {
            id_out[k] = hash[k];
            k++;
        }
        id_out[k] = 0;
    }
    return 0;
}

static void check_auto_send_crashes(App *a)
{
    if (!a->net_telemetry || !a->telemetry_host[0]) return;
    size_t sn = 0;
    unsigned char *sent_raw = read_all(CFGDIR "/sent_crashes.ini", &sn);
    char sent_buf[2048];
    if (sent_raw && sn < sizeof(sent_buf)) { memcpy(sent_buf, sent_raw, sn); sent_buf[sn] = 0; }
    else sent_buf[0] = 0;
    free(sent_raw);

    int sent_any = 0;
    char last_id[16] = "", last_tid[16] = "";
    for (int i = 0; i < a->ngames; i++) {
        Game *g = &a->games[i];
        char cp[96]; snprintf(cp, sizeof(cp), CFGDIR "/crash-%s.txt", g->id);
        size_t cn = 0;
        unsigned char *cb = read_all(cp, &cn);
        if (!cb || !cn) { free(cb); continue; }
        int h_val = (int)(fnv1a_buf(cb, cn) & 0x7FFFFFFFu);
        free(cb);
        if (ini_int(sent_buf, g->id, 0) == h_val) continue;

        Buf b = { malloc(256u << 10), 0, 256u << 10 };
        if (!b.b) break;
        bprintf(&b, "VCFG AUTO CRASH REPORT\nTitle: %s\nVersion: %s\n", g->id, VCFG_VERSION);
        bfile(&b, "crash dump", cp, 32u << 10);
        char p[96];
        snprintf(p, sizeof(p), CFGDIR "/boot-%s.txt", g->id);
        if (file_exists(p)) bfile(&b, "boot stages", p, 32u << 10);
        snprintf(p, sizeof(p), CFGDIR "/boot-%s.prev.txt", g->id);
        if (file_exists(p)) bfile(&b, "boot stages (prev)", p, 24u << 10);
        snprintf(p, sizeof(p), CFGDIR "/trace-%s.txt", g->id);
        if (file_exists(p)) bfile(&b, "flight recorder", p, 48u << 10);
        snprintf(p, sizeof(p), CFGDIR "/trace-%s.prev.txt", g->id);
        if (file_exists(p)) bfile(&b, "flight recorder (prev)", p, 24u << 10);
        snprintf(p, sizeof(p), CFGDIR "/adapt-%s.ini", g->id);
        if (file_exists(p)) bfile(&b, "adapt recipe", p, 8u << 10);
        snprintf(p, sizeof(p), CFGDIR "/config-%s.ini", g->id);
        if (file_exists(p)) bfile(&b, "game config", p, 8u << 10);

        char rep_id[16] = "";
        int rc = net_send_report(a->telemetry_host, a->telemetry_port, g->id, "crash", b.b, b.n, rep_id, sizeof(rep_id));
        free(b.b);
        vlog("auto-send crash %s -> rc=%d id=%s\n", g->id, rc, rep_id);
        if (rc == 0) {
            size_t cur = strlen(sent_buf);
            if (cur + 32 >= sizeof(sent_buf)) {
                const char *nl = strchr(sent_buf + 512, '\n');
                if (nl) {
                    size_t rem = strlen(nl + 1);
                    memmove(sent_buf, nl + 1, rem + 1);
                    cur = rem;
                } else {
                    sent_buf[0] = 0;
                    cur = 0;
                }
            }
            if (cur + 32 < sizeof(sent_buf))
                snprintf(sent_buf + cur, sizeof(sent_buf) - cur, "%s=%d\n", g->id, h_val);
            snprintf(last_id, sizeof(last_id), "%s", rep_id);
            snprintf(last_tid, sizeof(last_tid), "%s", g->id);
            sent_any++;
        } else {
            break; // VPS unreachable right now, stop trying remaining files
        }
    }
    if (sent_any > 0) {
        write_all(CFGDIR "/sent_crashes.ini", sent_buf, strlen(sent_buf));
        char msg[96];
        snprintf(msg, sizeof(msg), "AUTO-SENT CRASH %s TO VPS (#%s)", last_tid, last_id[0] ? last_id : "OK");
        addlog(a, msg);
        snprintf(a->status, sizeof(a->status), "%s", msg);
    }
}

static void do_telemetry_toggle(App *a)
{
    a->net_telemetry = !a->net_telemetry;
    save_settings(a);
    snprintf(a->status, sizeof(a->status), "NET TELEMETRY (AUTO-CRASH & REPORT UPLOAD): %s (%s)",
             a->net_telemetry ? "ON" : "OFF",
             a->telemetry_host[0] ? a->telemetry_host : "set host in /data/bb_probe/telemetry.ini");
    addlog(a, a->status);
    if (a->net_telemetry && a->telemetry_host[0]) check_auto_send_crashes(a);
}

static void do_report(App *a)
{
    Buf r = { malloc(4u << 20), 0, 4u << 20 };
    if (!r.b) { addlog(a, "OUT OF MEMORY FOR REPORT"); return; }
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
    bprintf(&r, "\n==== storage layout (names only; where an external drive keeps its games) ====\n");
    bput(&r, g_layout, strlen(g_layout));   // not bprintf("%s"): that truncates at 511 characters

    static TraceFile tf[128]; int terr = 0, nt = list_traces(tf, 128, &terr);
    bprintf(&r, "\n==== summary (flight recorder, trace-<TITLE>.txt, %d files in " CFGDIR ") ====\n", nt);
    if (terr) bprintf(&r, "READ ERROR: cannot list " CFGDIR " (errno %d)\n", terr);
    for (int k = 0; k < nt; k++) {
        char p[96]; size_t n = 0;
        snprintf(p, sizeof(p), CFGDIR "/trace-%s%s.txt", tf[k].id, tf[k].prev ? ".prev" : "");
        unsigned char *d = read_all(p, &n);
        if (d) {
            summarize(&r, tf[k].id, tf[k].prev ? "game start before" : "last game start", (const char *)d);
            free(d);
        } else {
            bprintf(&r, "%s, %s: boot-only log (no trace-%s%s.txt written before exit)\n",
                    tf[k].id, tf[k].prev ? "game start before" : "last game start", tf[k].id, tf[k].prev ? ".prev" : "");
        }
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
        bprintf(&r, "%s version=%s installed=%d experimental=%d fg=%d preset=%d sharp=%d enhance=%d overlay=%d engine_choice=%d engine=%d buf_choice=%d buf=%d title='%s'\n", g->id,
                g->version[0] ? g->version : "?", g->installed, g->experimental, g->s.fg, g->s.preset, g->s.sharp, g->s.enhance,
                g->s.overlay, g->eng_sel, game_engine(g), g->buf_sel, game_buf(g), g->title);
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
    bfile(&r, "buffer choice per game", BUFFILE, 8u << 10);
    for (int i = 0; i < a->ngames; i++) {
        char p[96]; snprintf(p, sizeof(p), CFGDIR "/config-%s.ini", a->games[i].id);
        if (file_exists(p)) bfile(&r, "game settings", p, 8u << 10);
        snprintf(p, sizeof(p), CFGDIR "/adapt-%s.ini", a->games[i].id);
        if (file_exists(p)) bfile(&r, "auto-adapted settings", p, 8u << 10);
        snprintf(p, sizeof(p), CFGDIR "/crash-%s.txt", a->games[i].id);
        if (file_exists(p)) bfile(&r, "crash dump", p, 32u << 10);
    }
    for (int k = 0; k < nt; k++) {
        char p[96]; snprintf(p, sizeof(p), CFGDIR "/trace-%s%s.txt", tf[k].id, tf[k].prev ? ".prev" : "");
        if (file_exists(p)) bfile(&r, tf[k].prev ? "flight recorder, game start before" : "flight recorder, last game start", p, 96u << 10);
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
    if (!usb) addlog(a, "NO USB STICK: SAVED TO /DATA/VCFG_REPORT.TXT");

    if (a->net_telemetry && a->telemetry_host[0]) {
        const char *tid = (a->ngames && a->sel >= 0 && a->sel < a->ngames) ? a->games[a->sel].id : "GLOBAL";
        char rep_id[16] = "";
        int nrc = net_send_report(a->telemetry_host, a->telemetry_port, tid, "manual", r.b, r.n, rep_id, sizeof(rep_id));
        if (nrc == 0) {
            snprintf(m, sizeof(m), "REPORT SENT TO VPS! ID: #%s", rep_id[0] ? rep_id : "OK");
            addlog(a, m);
            snprintf(a->status, sizeof(a->status), "%s", m);
        } else {
            snprintf(m, sizeof(m), "VPS UPLOAD FAILED (ERR %d) - SAVED LOCALLY", nrc);
            addlog(a, m);
            snprintf(a->status, sizeof(a->status), "%s", m);
        }
    } else if (!a->net_telemetry) {
        snprintf(a->status, sizeof(a->status), "SAVED /DATA/VCFG_REPORT.TXT (NET UPLOAD OFF - PRESS L2 TO ENABLE)");
    } else {
        snprintf(a->status, sizeof(a->status), "SAVED /DATA/VCFG_REPORT.TXT (SET host IN telemetry.ini FOR VPS UPLOAD)");
    }
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
static int g_uid = 0;
static void quit(void)
{
    if (g_log) { fclose(g_log); g_log = NULL; }
    if (sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_SYSTEM_SERVICE) == 0) sceSystemServiceLoadExec("exit", NULL);
    for (;;) sceKernelUsleep(1000000);
}
static void do_launch_game(App *a, Canvas *c, uint32_t *back)
{
    if (!a->ngames) return;
    Game *g = &a->games[a->sel];
    if (!g->installed) {
        snprintf(a->status, sizeof(a->status), "%s IS NOT INSTALLED.", g->id);
        return;
    }
    save_settings(a);
    remap_if_changed(a);
    snprintf(a->status, sizeof(a->status), "SAVED. LAUNCHING %s...", g->id);
    app_draw(c, a);
    present(back);

    // Must be jailbroken BEFORE calling SceLncService IPC, otherwise SceLncService rejects with 0x8094000F!
    struct jailbreak_backup jb;
    memset(&jb, 0, sizeof(jb));
    int jrc = sys_sdk_jailbreak(&jb);

    int h_sys = sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_SYSTEM_SERVICE);
    int m = sceKernelLoadStartModule("/system/common/lib/libSceSystemService.sprx", 0, NULL, 0, NULL, NULL);
    int (*p_init)(void) = NULL;
    int (*p_launch)(const char *, const char **, void *) = NULL;
    int (*p_sys)(const char *, const char **, void *) = NULL;

    if (m >= 0) {
        sceKernelDlsym(m, "sceLncUtilInitialize", (void **)&p_init);
        sceKernelDlsym(m, "sceLncUtilLaunchApp", (void **)&p_launch);
        sceKernelDlsym(m, "sceSystemServiceLaunchApp", (void **)&p_sys);
    }
    // In libSceSystemService.sprx, sceSystemServiceLaunchApp (vaddr 0x4600) calls sceLncUtilLaunchApp (vaddr 0x58b0)
    // via a near E8 call at +0x41, and sceLncUtilInitialize (vaddr 0x5890) sits 0x20 bytes before sceLncUtilLaunchApp.
    if (p_sys && (!p_launch || !p_init)) {
        const uint8_t *code = (const uint8_t *)(uintptr_t)p_sys;
        for (int off = 0x28; off <= 0x58; off++) {
            if (code[off] == 0xE8) {
                int32_t rel = 0;
                memcpy(&rel, code + off + 1, 4);
                if (rel > 0x800 && rel < 0x3000) {
                    const uint8_t *cand = code + off + 5 + rel;
                    if (cand[0] == 0x48 && cand[1] == 0x89 && cand[2] == 0xD1) {
                        if (!p_launch) p_launch = (int (*)(const char *, const char **, void *))(uintptr_t)cand;
                        const uint8_t *init_cand = cand - 0x20;
                        if (!p_init && init_cand[0] == 0x48 && init_cand[1] == 0x8D && init_cand[2] == 0x3D)
                            p_init = (int (*)(void))(uintptr_t)init_cand;
                        break;
                    }
                }
            }
        }
    }

    int init_rc = p_init ? p_init() : -1;

    // Hardware SceLncService IPC expects a 24-byte (0x18) packed parameter struct:
    typedef struct __attribute__((packed)) {
        uint32_t size;
        int32_t  user_id;
        uint32_t app_opt;
        uint64_t crash_report;
        uint32_t check_flag;
    } LncParam24;

    LncParam24 p24;
    memset(&p24, 0, sizeof(p24));
    p24.size = sizeof(p24); // 24 (0x18)
    p24.user_id = g_uid;

    LncAppParam p32;
    memset(&p32, 0, sizeof(p32));
    p32.size = sizeof(p32); // 32 (0x20)
    p32.user_id = g_uid;

    int rc = -1;
    if (p_launch) {
        rc = p_launch(g->id, NULL, &p24);
        if (rc < 0) rc = p_launch(g->id, NULL, &p32);
    }
    if (rc < 0) {
        typedef int (*LaunchAppFn)(const char *, const char **, void *);
        LaunchAppFn fn_sys = p_sys ? p_sys : (LaunchAppFn)(uintptr_t)&sceSystemServiceLaunchApp;
        rc = fn_sys(g->id, NULL, NULL);
        if (rc < 0) rc = fn_sys(g->id, NULL, &p24);
    }

    vlog("launch %s: jb=%d h_sys=%d m=%d p_init=%p(rc=0x%x) p_launch=%p rc=0x%08x\n",
         g->id, jrc, h_sys, m, (void *)(uintptr_t)p_init, (unsigned)init_rc, (void *)(uintptr_t)p_launch, (unsigned)rc);
    if (rc >= 0) {
        if (g_log) fflush(g_log);
        sceKernelUsleep(1500000);
        return;
    }
    if (jrc == 0) sys_sdk_unjailbreak(&jb);
    snprintf(a->status, sizeof(a->status), "CANNOT LAUNCH %s (0x%08X)", g->id, (unsigned)rc);
    addlog(a, a->status);
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
        snprintf(g->id, sizeof(g->id), "%s", k_sup[i].id); g->engine = k_sup[i].engine; g->def_buf = auto_2buf(k_sup[i].id) ? BUF_2BUF : BUF_STD; g->testing = k_sup[i].testing;
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
            const Supported *sup = sup_for_title(e->d_name);
            g->engine = sup ? sup->engine : (auto_alt(e->d_name) ? ENGINE_CLASSIC_ALT : ENGINE_CLASSIC);
            g->def_buf = auto_2buf(e->d_name) ? BUF_2BUF : BUF_STD;
            g->testing = sup ? sup->testing : 0;
            g->experimental = sup ? 0 : 1;
            load_meta(g);
            if (!g->installed) { a->ngames--; sr->not_installed++; } else sr->added++;
        }
        closedir(dir);
    } else { sr->err = errno; vlog("cannot list %s (errno %d)\n", ap, sr->err); }
    if (sr->listed)
        vlog("scan %s: CUSA folders %d, new %d, already listed %d, not installed %d, list full %d\n", ap, sr->found, sr->added,
             sr->known, sr->not_installed, sr->full);
    }
    layout_snapshot();
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
    if (is_off()) addlog(a, "VCFG IS TURNED OFF. CROSS TURNS IT ON.");
    else if (a->plugin_state != 1) {
        addlog(a, a->plugin_state ? "UPDATING PLUGINS" : "INSTALLING PLUGINS");
        do_install(a);
        load_settings(a);
    } else {
        remap_if_changed(a);
    }
    check_auto_send_crashes(a);

    if (video_init()) { vlog("video init failed\n"); quit(); }
    int uid = 0; sceUserServiceInitialize(NULL); sceUserServiceGetInitialUser(&uid); g_uid = uid;
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
            else if (act == ACT_REMOVE) { do_ini(a, 0); set_off(1); a->plugin_state = plugin_state(); }
            else if (act == ACT_CLEAN) do_clean(a);
            else if (act == ACT_DEBUG) do_debug_toggle(a);
            else if (act == ACT_TELEMETRY_TOGGLE) do_telemetry_toggle(a);
            else if (act == ACT_REPORT) do_report(a);
            else if (act == ACT_RESET_DEV) do_reset_dev(a);
            else if (act == ACT_APPLY_ADVICE) do_apply_advice(a);
            else if (act == ACT_SAVE) { save_settings(a); remap_if_changed(a); }
            else if (act == ACT_LAUNCH) do_launch_game(a, &c, back);
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
