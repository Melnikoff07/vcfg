// VCFG installer: a tiny homebrew app. Launch it once from the home screen; it
//   1) copies /app0/plugins/vcfg.prx to /data/GoldHEN/plugins/vcfg.prx (temp file + byte-exact readback + rename),
//   2) backs up /data/GoldHEN/plugins.ini and points the supported titles at vcfg.prx (other plugins in those
//      sections are kept; only older vcfg*/bb_probe* lines of the same section are replaced),
//   3) creates /data/bb_probe/vcfg.ini with FG enabled if it does not exist,
//   4) shows a notification and exits. Log: /data/vcfg_installer.log. No game files are touched.
// Launch the installer while no game is running (GoldHEN loads plugins at the next game start).
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
#include <orbis/libkernel.h>
#include <orbis/Sysmodule.h>
#include <orbis/SystemService.h>

#define PLUGIN_SRC "/app0/plugins/vcfg.prx"
#define PLUGIN_DIR "/data/GoldHEN/plugins"
#define PLUGIN_DST "/data/GoldHEN/plugins/vcfg.prx"
#define INI "/data/GoldHEN/plugins.ini"
#define CFG_DIR "/data/bb_probe"
#define CFG "/data/bb_probe/vcfg.ini"
#define LOG "/data/vcfg_installer.log"

// Titles verified on hardware with this plugin (see docs/COMPATIBILITY.md).
static const char *k_titles[] = { "CUSA07439" };

static FILE *g_log;
static void logf(const char *fmt, ...)
{
    if (!g_log) return;
    va_list a; va_start(a, fmt); vfprintf(g_log, fmt, a); va_end(a); fflush(g_log);
}
static void notify(const char *text)
{
    OrbisNotificationRequest r; memset(&r, 0, sizeof(r));
    r.type = NotificationRequest; r.targetId = -1; r.useIconImageUri = 1;
    snprintf(r.message, sizeof(r.message), "%s", text);
    snprintf(r.iconUri, sizeof(r.iconUri), "cxml://psnotification/tex_icon_system");
    sceKernelSendNotificationRequest(0, &r, sizeof(r), 0);
    logf("NOTIFY %s\n", text);
}
static unsigned char *read_all(const char *path, size_t *n)
{
    *n = 0; FILE *f = fopen(path, "rb"); if (!f) return NULL;
    if (fseek(f, 0, SEEK_END)) { fclose(f); return NULL; }
    long len = ftell(f); if (len < 0 || len > (64L << 20)) { fclose(f); return NULL; }
    rewind(f); unsigned char *b = malloc((size_t)len + 1); if (!b) { fclose(f); return NULL; }
    size_t got = fread(b, 1, (size_t)len, f); fclose(f);
    if (got != (size_t)len) { free(b); return NULL; }
    b[len] = 0; *n = (size_t)len; return b;
}
static int write_all(const char *path, const void *data, size_t n)
{
    FILE *f = fopen(path, "wb"); if (!f) return -1;
    size_t w = fwrite(data, 1, n, f); int c = fclose(f);
    return (w == n && c == 0) ? 0 : -1;
}
// Write to path.tmp, verify by reading back, then rename over path.
static int write_verified(const char *path, const void *data, size_t n)
{
    char tmp[256]; snprintf(tmp, sizeof(tmp), "%s.vcfg_tmp", path);
    if (write_all(tmp, data, n)) { logf("write failed %s\n", tmp); return -1; }
    size_t m = 0; unsigned char *back = read_all(tmp, &m);
    int same = back && m == n && memcmp(back, data, n) == 0; free(back);
    if (!same) { logf("readback mismatch %s\n", tmp); remove(tmp); return -1; }
    if (rename(tmp, path)) { logf("rename failed %s -> %s\n", tmp, path); remove(tmp); return -1; }
    return 0;
}
static int is_our_plugin_line(const char *line)
{
    return strstr(line, PLUGIN_DIR "/vcfg") != NULL || strstr(line, PLUGIN_DIR "/bb_probe") != NULL;
}
// Rebuild plugins.ini text: for each supported title, keep foreign lines of its section, drop our older lines and
// add the vcfg line; append missing sections before [default] (if any) so [default] stays last.
static char *update_ini(const char *old, size_t *out_n)
{
    size_t cap = strlen(old) + 4096; char *out = malloc(cap); if (!out) return NULL;
    size_t o = 0; const char *p = old; int in_title = 0; int done[sizeof(k_titles) / sizeof(k_titles[0])] = {0};
    const size_t nt = sizeof(k_titles) / sizeof(k_titles[0]);
#define EMIT(s) do { size_t L = strlen(s); if (o + L + 2 >= cap) { free(out); return NULL; } memcpy(out + o, s, L); o += L; } while (0)
    char line[512];
    while (*p) {
        size_t L = strcspn(p, "\r\n"); if (L >= sizeof(line)) L = sizeof(line) - 1;
        memcpy(line, p, L); line[L] = 0; p += strcspn(p, "\r\n"); while (*p == '\r' || *p == '\n') p++;
        char *s = line; while (*s == ' ' || *s == '\t') s++;
        if (*s == '[') {
            if (!strncmp(s, "[default]", 9)) {           // add missing sections before [default]
                for (size_t t = 0; t < nt; t++) if (!done[t]) {
                    char b[96]; snprintf(b, sizeof(b), "[%s]\n" PLUGIN_DST "\n", k_titles[t]); EMIT(b); done[t] = 1;
                }
            }
            in_title = -1;
            for (size_t t = 0; t < nt; t++) {
                char h[24]; snprintf(h, sizeof(h), "[%s]", k_titles[t]);
                if (!strncmp(s, h, strlen(h))) in_title = (int)t;
            }
            EMIT(line); EMIT("\n");
            if (in_title >= 0 && !done[in_title]) { EMIT(PLUGIN_DST "\n"); done[in_title] = 1; }
            continue;
        }
        if (in_title >= 0 && is_our_plugin_line(s)) continue;   // older vcfg/bb_probe line of a supported title
        if (*s == 0) continue;
        EMIT(line); EMIT("\n");
    }
    for (size_t t = 0; t < nt; t++) if (!done[t]) {
        char b[96]; snprintf(b, sizeof(b), "[%s]\n" PLUGIN_DST "\n", k_titles[t]); EMIT(b);
    }
#undef EMIT
    out[o] = 0; *out_n = o; return out;
}
static int install(char *msg, size_t msgn)
{
    size_t n = 0; unsigned char *prx = read_all(PLUGIN_SRC, &n);
    if (!prx || n < 1024) { snprintf(msg, msgn, "VCFG: plugin missing in the package"); return -1; }
    sceKernelMkdir(PLUGIN_DIR, 0777); sceKernelMkdir(CFG_DIR, 0777);
    if (write_verified(PLUGIN_DST, prx, n)) { free(prx); snprintf(msg, msgn, "VCFG: cannot write %s", PLUGIN_DST); return -1; }
    free(prx); logf("plugin copied %zu bytes\n", n);

    size_t in = 0; unsigned char *ini = read_all(INI, &in);
    if (!ini) { ini = (unsigned char *)strdup(""); in = 0; logf("plugins.ini missing, creating\n"); }
    else {
        char bk[128]; snprintf(bk, sizeof(bk), INI ".vcfg_backup_%lld", (long long)time(NULL));
        if (write_all(bk, ini, in)) { free(ini); snprintf(msg, msgn, "VCFG: cannot back up plugins.ini"); return -1; }
        logf("backup %s\n", bk);
    }
    size_t un = 0; char *upd = update_ini((const char *)ini, &un); free(ini);
    if (!upd) { snprintf(msg, msgn, "VCFG: plugins.ini too large"); return -1; }
    int rc = write_verified(INI, upd, un); logf("plugins.ini written rc=%d:\n%s\n", rc, upd); free(upd);
    if (rc) { snprintf(msg, msgn, "VCFG: cannot write plugins.ini"); return -1; }

    FILE *c = fopen(CFG, "rb");
    if (c) fclose(c); else { const char *d = "enabled=1\n"; write_all(CFG, d, strlen(d)); logf("created %s\n", CFG); }
    snprintf(msg, msgn, "VCFG installed for %s. Start the game, wait ~20 s, hold L3+R3 for 3 s to open the menu.",
             k_titles[0]);
    return 0;
}
int main(void)
{
    g_log = fopen(LOG, "w");
    logf("VCFG installer start\n");
    char msg[256]; int rc = install(msg, sizeof(msg));
    notify(msg); logf("result=%d\n", rc);
    sceKernelUsleep(3000000);   // let the notification appear before the app closes
    if (g_log) { fclose(g_log); g_log = NULL; }
    // Exit through the system service (the CRT exit path crashed in an earlier app on this firmware).
    if (sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_SYSTEM_SERVICE) == 0) sceSystemServiceLoadExec("exit", NULL);
    for (;;) sceKernelUsleep(1000000);
}
