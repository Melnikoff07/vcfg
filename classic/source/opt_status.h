// Effective state of the player-facing options (pure, no console API).
// Compiled into main.c and, unchanged, into the host tests (tools/tests/test_menu_status.py).
//
// Two questions, two rules:
//  * which rows the in-game menu shows: a row whose setting cannot act under the other settings is hidden
//    (opt_row_visible). Below 30 FPS needs FG on, a mode other than Fast and no fps_lock = 20; Mode, Game vectors and
//    HUD protection need FG on; Low latency needs FG on and no fps_lock = 20. The app applies the same rule.
//  * what a shown option does right now: off, active (acting now), ready (applies, waits for the game), inert (on
//    but cannot act in this game or state) or limited (acts, without part of what it does), with a short reason
//    (opt_*). The menu draws the state next to the value and the reason of the selected row in the header; probe.log
//    gets one "options effective:" line per change.
//  * the 2nd generated-frame buffer: on the CUSA18723 family it exists only when the game started with below30 = 1
//    (main.c s14_decide: shave14 gives it), and Fast needs it for 2 generated frames per pair. So below30 = 0 at boot
//    limits Fast to 1 generated frame per pair, below30 turned on later stays inert until the next start, and below30
//    turned off and saved drops the buffer at the next start. Each of these is said in the menu and in the log.
// The status never feeds back into a decision: main.c only copies the values its decisions already computed.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include "pace_policy.h"

enum { OPT_ROW_FG, OPT_ROW_MODE, OPT_ROW_SHARP, OPT_ROW_AA, OPT_ROW_COUNTER, OPT_ROW_GV, OPT_ROW_HUD, OPT_ROW_LOCK,
       OPT_ROW_B30, OPT_ROW_SHQ, OPT_ROW_OPACITY, OPT_ROW_N };

static inline int opt_row_visible(int row, int framegen, int preset, int fps_lock)
{
    switch (row) {
    case OPT_ROW_MODE: case OPT_ROW_GV: case OPT_ROW_HUD: return framegen != 0;
    case OPT_ROW_B30: return framegen != 0 && preset != 1 && fps_lock != 20;
    case OPT_ROW_SHQ: return framegen != 0 && fps_lock != 20;
    default: return row >= 0 && row < OPT_ROW_N;
    }
}
// Visible rows in menu order into ids[OPT_ROW_N]; returns how many.
static inline int opt_menu_rows(int framegen, int preset, int fps_lock, int *ids)
{
    int n = 0;
    for (int r = 0; r < OPT_ROW_N; r++) if (opt_row_visible(r, framegen, preset, fps_lock)) ids[n++] = r;
    return n;
}
// Position of row in ids (the menu index), -1 when hidden.
static inline int opt_menu_index_of(const int *ids, int n, int row)
{
    for (int i = 0; i < n; i++) if (ids[i] == row) return i;
    return -1;
}

enum { OPT_OFF, OPT_ACTIVE, OPT_READY, OPT_INERT, OPT_LIMITED };
// buf2_boot: how the 2nd generated-frame buffer was decided at game start
enum { OPT_BUF2_NA,            // not tied to below30 for this title (mode-8 allocation)
       OPT_BUF2_ON,            // shave14 gave it (the game started with below30 = 1)
       OPT_BUF2_OFF_B30,       // none: the game started with below30 = 0 (restart with below30 = 1 to get it)
       OPT_BUF2_OFF_OTHER };   // none: shave14 refused for another reason
typedef struct { int st; const char *why; } OptStat;   // why: short lowercase reason, "" when none

typedef struct {
    // settings (g_cfg)
    int framegen, preset, sharpness, upscale, hud_protect, game_vectors, fps_lock, below30, shallow_queue, post_mid,
        present_copy;
    // state the plugin's own decisions already computed (main.c copies them, never the other way round)
    int started;         // frame generation reached its first injected frame (flip 120)
    int fg_path;         // last real frame: 0 not generated, 1 SubmitAndFlip path, 2 VideoOutSubmitFlip path
    char fg_why[32];     // g_inert_reason, normalized (opt_norm_reason), "" when none
    int detected_60fps, mode8, nmid, mem_tight_b30, mem_tight, pace_timer, b30_on, shq_on, rcas_shader, mid_only,
        real_aa, gv_active, hud_rect, pcopy_on, mid_lru, buf2_boot,
        fast_pace;       // LOG_DIR/fastpace_<TITLE> read at start (FG Fast 3rd-slot cut and phases)
} OptState;

// g_inert_reason ("INERT: RES 1280x720", "60FPS NATIVE (FG OFF)") -> "res 1280x720", "60fps native (fg off)".
static inline void opt_norm_reason(char *out, size_t cap, const char *in)
{
    if (!cap) return;
    if (!in) in = "";
    const char *p = in;
    if (p[0] == 'I' && p[1] == 'N' && p[2] == 'E' && p[3] == 'R' && p[4] == 'T' && p[5] == ':') { p += 6; while (*p == ' ') p++; }
    size_t i = 0;
    for (; *p && i + 1 < cap; p++) out[i++] = (char)(*p >= 'A' && *p <= 'Z' ? *p + 32 : *p);
    out[i] = 0;
}

static inline OptStat opt_stat(int st, const char *why) { OptStat s; s.st = st; s.why = why ? why : ""; return s; }

// Frame generation itself.
static inline OptStat opt_fg(const OptState *s)
{
    if (!s->framegen) return opt_stat(OPT_OFF, s->detected_60fps ? "60 fps game" : "");
    if (s->fg_why[0]) return opt_stat(OPT_INERT, s->fg_why);
    if (s->detected_60fps && s->fps_lock != 30) return opt_stat(OPT_INERT, "60 fps game");
    if (!s->started) return opt_stat(OPT_READY, "starting");
    if (!s->fg_path) return opt_stat(OPT_READY, "waiting for buffers");
    return opt_stat(OPT_ACTIVE, "");
}
static inline int opt_fg_running(const OptState *s) { return opt_fg(s).st == OPT_ACTIVE; }

// Mode: only Fast has a live state, the number of generated frames per pair it can show (2 needs the 2nd buffer).
static inline OptStat opt_mode(const OptState *s)
{
    if (s->preset != 1 || !opt_fg_running(s)) return opt_stat(OPT_OFF, "");
    if (s->nmid >= 2) return opt_stat(OPT_ACTIVE, "up to 2 generated");
    return opt_stat(OPT_LIMITED, s->buf2_boot == OPT_BUF2_OFF_B30 ? "1 gen/pair: below30 off at boot" : "1 gen/pair: no 2nd buffer");
}

// Below 30 FPS: the regime of 3-slot pairs. Ready/active exactly where b30_available() lets it run.
static inline OptStat opt_b30(const OptState *s)
{
    if (!s->below30) return opt_stat(OPT_OFF, s->buf2_boot == OPT_BUF2_ON ? "save drops 2nd buffer" : "");
    if (!s->framegen) return opt_stat(OPT_INERT, "fg off");
    if (s->preset == 1) return opt_stat(OPT_INERT, "fast mode");
    if (s->fps_lock == 20) return opt_stat(OPT_INERT, "fps lock 20");
    if (!s->mode8) return opt_stat(OPT_INERT, "fg mode not 8");
    if (s->nmid < 2) return opt_stat(OPT_INERT, s->buf2_boot == OPT_BUF2_OFF_B30 ? "no 2nd buf, restart" : "no 2nd buffer");
    if (s->mem_tight_b30) return opt_stat(OPT_INERT, "memory tight");
    OptStat f = opt_fg(s);   // the rest is known once frames are generated (the hook copies the pacing and the path)
    if (f.st != OPT_ACTIVE) return opt_stat(f.st == OPT_READY ? OPT_READY : OPT_INERT, f.st == OPT_READY ? "fg starting" : "fg inert");
    if (s->fg_path == 2) return opt_stat(OPT_INERT, "videoout flip path");
    if (!s->pace_timer) return opt_stat(OPT_INERT, "no timer pacing");
    return s->b30_on ? opt_stat(OPT_ACTIVE, "game under 30 fps") : opt_stat(OPT_READY, "game at 30 fps");
}

// Low latency (shallow flip queue).
static inline OptStat opt_shq(const OptState *s)
{
    if (!s->shallow_queue) return opt_stat(OPT_OFF, "");
    if (!s->framegen) return opt_stat(OPT_INERT, "fg off");
    if (s->fps_lock == 20) return opt_stat(OPT_INERT, "fps lock 20");
    if (!s->mode8) return opt_stat(OPT_INERT, "fg mode not 8");
    OptStat f = opt_fg(s);
    if (f.st != OPT_ACTIVE) return opt_stat(f.st == OPT_READY ? OPT_READY : OPT_INERT, f.st == OPT_READY ? "fg starting" : "fg inert");
    if (s->fg_path == 2) return opt_stat(OPT_INERT, "videoout flip path");
    if (!s->pace_timer) return opt_stat(OPT_INERT, "no timer pacing");
    if (s->b30_on) return opt_stat(OPT_READY, "paused by below 30");
    return s->shq_on ? opt_stat(OPT_ACTIVE, "") : opt_stat(OPT_READY, "needs game < 30 ms");
}

// Which frames the post pass (rcas8) touches while frames are generated.
static inline const char *opt_post_target(const OptState *s, int aa)
{
    if (aa && s->real_aa) return "generated+real";
    return s->mid_only ? "generated" : "generated+real";
}
// Sharpness (rcas8 contrast). With frame generation on and memory tight, the first generated frame is sharpened only
// when Anti-aliasing is on (build_dcb_flow8: !g_mem_tight || upscale > 0); the 2nd generated frame of a 3-slot pair
// (Fast with 2 buffers, the below-30 regime) has no such gate and is sharpened anyway.
static inline OptStat opt_sharp(const OptState *s)
{
    if (s->sharpness <= 0) return opt_stat(OPT_OFF, "");
    if (!s->rcas_shader) return opt_stat(OPT_INERT, "no post shader");
    if (!opt_fg_running(s)) return opt_stat(OPT_ACTIVE, "real");
    if (s->mem_tight && s->upscale <= 0)
        return ((s->preset == 1 && s->nmid >= 2) || s->b30_on) ? opt_stat(OPT_LIMITED, "memory: 2nd gen frame only")
                                                                : opt_stat(OPT_INERT, "memory, needs aa");
    return opt_stat(OPT_ACTIVE, opt_post_target(s, 0));
}
static inline OptStat opt_aa(const OptState *s)
{
    if (s->upscale <= 0) return opt_stat(OPT_OFF, "");
    if (!s->rcas_shader) return opt_stat(OPT_INERT, "no post shader");
    if (!opt_fg_running(s)) return opt_stat(OPT_ACTIVE, "real");
    return opt_stat(OPT_ACTIVE, opt_post_target(s, 1));
}
static inline OptStat opt_gv(const OptState *s)
{
    if (!s->game_vectors) return opt_stat(OPT_OFF, "");
    if (!s->framegen) return opt_stat(OPT_INERT, "fg off");
    if (s->detected_60fps) return opt_stat(OPT_INERT, "60 fps game");
    if (!opt_fg_running(s)) return opt_stat(OPT_INERT, "fg inert");
    return s->gv_active ? opt_stat(OPT_ACTIVE, "") : opt_stat(OPT_READY, "calibrating");
}
// HUD protection: levels 1 and 2 do the same in the classic engine (confidence fallback + the hud_<TITLE> rectangle).
static inline OptStat opt_hud(const OptState *s)
{
    if (s->hud_protect <= 0) return opt_stat(OPT_OFF, "");
    if (!opt_fg_running(s)) return opt_stat(OPT_INERT, "fg inert");
    return opt_stat(OPT_ACTIVE, s->hud_rect ? "hud rect" : "no hud file");
}
static inline OptStat opt_lock(const OptState *s)
{
    int fg = opt_fg_running(s);
    switch (s->fps_lock) {
    case 20: return opt_stat(OPT_ACTIVE, "pace 20");
    case 30: if (s->detected_60fps && fg) return opt_stat(OPT_ACTIVE, "fg on 60 fps game");
             return fg ? opt_stat(OPT_ACTIVE, "") : opt_stat(OPT_INERT, "fg not running");
    case 60: if (s->detected_60fps) return opt_stat(OPT_ACTIVE, "keeps fg off");
             return fg ? opt_stat(OPT_INERT, "fg pairs pace") : opt_stat(OPT_ACTIVE, "pace 60");
    default: return opt_stat(OPT_OFF, "");
    }
}

// Status of a menu row (OPT_OFF without a reason for rows that have no live state).
static inline OptStat opt_row_stat(const OptState *s, int row)
{
    switch (row) {
    case OPT_ROW_FG: return opt_fg(s);
    case OPT_ROW_MODE: return opt_mode(s);
    case OPT_ROW_SHARP: return opt_sharp(s);
    case OPT_ROW_AA: return opt_aa(s);
    case OPT_ROW_GV: return opt_gv(s);
    case OPT_ROW_B30: return opt_b30(s);
    case OPT_ROW_SHQ: return opt_shq(s);
    default: return opt_stat(OPT_OFF, "");
    }
}
// Short word drawn next to the value; NULL = nothing (off, or a row without live state).
static inline const char *opt_tag(OptStat t)
{
    return t.st == OPT_ACTIVE ? "ACTIVE" : t.st == OPT_READY ? "READY" : t.st == OPT_INERT ? "INERT" : t.st == OPT_LIMITED ? "LIMITED" : 0;
}
static inline const char *opt_word(OptStat t, int applied)
{
    return t.st == OPT_ACTIVE ? (applied ? "applied" : "active") : t.st == OPT_READY ? "ready" : t.st == OPT_INERT ? "inert"
         : t.st == OPT_LIMITED ? "limited" : "off";
}
// Header line of the selected row ("BELOW 30: MEMORY TIGHT" once upper-cased by the overlay); 0 when the row has
// nothing to say (off without a reason, no live state). The overlay shows 38 characters at most.
static inline int opt_row_detail(char *out, size_t cap, int row, OptStat t)
{
    static const char *const names[OPT_ROW_N] = { "FG", "FAST", "SHARPNESS", "AA", "FPS COUNTER", "GAME VECTORS", "HUD",
                                                  "FPS LOCK", "BELOW 30", "LOW LATENCY", "OPACITY" };
    if (!cap) return 0;
    out[0] = 0;
    if (row < 0 || row >= OPT_ROW_N || (t.st == OPT_OFF && !t.why[0])) return 0;
    // the state word is on the row already: the header says the reason ("BELOW 30: MEMORY TIGHT"), or the word alone
    int k = snprintf(out, cap, "%s: %s", names[row], t.why[0] ? t.why : opt_word(t, 0));
    return k > 0;
}

static inline const char *opt_preset_name(int p) { return p == 1 ? "fast" : p == 2 ? "sharp" : "quality"; }
static inline const char *opt_aa_name(int e) { return e >= 2 ? "strong" : e == 1 ? "light" : "off"; }

// " name=value(state: why)" appended at *pos; the state is omitted for off without a reason, and for an active value
// without a reason when bare (fps_lock=30). Returns 0 when the token does not fit: it is left out whole, and so is every
// token after it (the line keeps the most important ones, well formed).
static inline int opt_tok(char *out, size_t cap, size_t *pos, const char *name, const char *value, OptStat t, int applied)
{
    if (*pos + 1 >= cap) return 0;
    size_t room = cap - *pos;
    int k;
    if ((t.st == OPT_OFF || (applied < 0 && t.st == OPT_ACTIVE)) && !t.why[0]) k = snprintf(out + *pos, room, " %s=%s", name, value);
    else if (t.st == OPT_OFF) k = snprintf(out + *pos, room, " %s=%s(%s)", name, value, t.why);
    else if (t.why[0]) k = snprintf(out + *pos, room, " %s=%s(%s: %s)", name, value, opt_word(t, applied > 0), t.why);
    else k = snprintf(out + *pos, room, " %s=%s(%s)", name, value, opt_word(t, applied > 0));
    if (k > 0 && (size_t)k < room) { *pos += (size_t)k; return 1; }
    out[*pos] = 0;
    return 0;
}
// The tokens of the "options effective:" line (no prefix, no newline), most important first. Returns the length.
static inline size_t opt_format(char *out, size_t cap, const OptState *s)
{
    if (!cap) return 0;
    out[0] = 0;
    size_t pos = 0;
    char v[16];
    int ok = 1;
#define OPT_TOK(...) do { if (ok) ok = opt_tok(__VA_ARGS__); } while (0)
    OPT_TOK(out, cap, &pos, "framegen", s->framegen ? "on" : "off", opt_fg(s), 0);
    OPT_TOK(out, cap, &pos, "preset", opt_preset_name(s->preset), opt_mode(s), 0);
    if (s->preset == 1 || s->fast_pace)   // the opt-in file acts only on FG Fast
        OPT_TOK(out, cap, &pos, "fast_pace", s->fast_pace ? "on" : "off", opt_stat(OPT_OFF, s->preset == 1 ? "" : "fast only"), 0);
    OPT_TOK(out, cap, &pos, "below30", s->below30 ? "on" : "off", opt_b30(s), 0);
    OPT_TOK(out, cap, &pos, "shallow_queue", s->shallow_queue ? "on" : "off", opt_shq(s), 0);
    snprintf(v, sizeof(v), "%d", s->sharpness);
    OPT_TOK(out, cap, &pos, "sharpness", v, opt_sharp(s), 1);
    OPT_TOK(out, cap, &pos, "enhance", opt_aa_name(s->upscale), opt_aa(s), 1);
    snprintf(v, sizeof(v), "%d", s->nmid);
    OPT_TOK(out, cap, &pos, "gen_buffers", v, opt_stat(OPT_OFF, s->buf2_boot == OPT_BUF2_ON ? "below30 at boot"
                                                     : s->buf2_boot == OPT_BUF2_OFF_B30 ? "below30=0 at boot"
                                                     : s->buf2_boot == OPT_BUF2_OFF_OTHER ? "shave14 off" : ""), 0);
    OPT_TOK(out, cap, &pos, "game_vectors", s->game_vectors ? "on" : "off", opt_gv(s), 0);
    snprintf(v, sizeof(v), "%d", s->hud_protect);
    OPT_TOK(out, cap, &pos, "hud_protect", v, opt_hud(s), 0);
    snprintf(v, sizeof(v), "%d", s->fps_lock);
    OPT_TOK(out, cap, &pos, "fps_lock", s->fps_lock ? v : "auto", opt_lock(s), -1);
    if (s->present_copy || s->pcopy_on)
        OPT_TOK(out, cap, &pos, "present_copy", s->present_copy == 1 ? "on" : s->present_copy == 2 ? "off" : "auto",
                opt_stat(OPT_OFF, s->pcopy_on ? "running" : "not running"), 0);
    if (s->post_mid) { snprintf(v, sizeof(v), "%d", s->post_mid); OPT_TOK(out, cap, &pos, "post_mid", v, opt_stat(OPT_OFF, ""), 0); }
    if (s->mid_lru) OPT_TOK(out, cap, &pos, "mid_order", "lru", opt_stat(OPT_OFF, ""), 0);   // below30=1 or Fast (main.c mid_lru_on)
#undef OPT_TOK
    if (pos && out[0] == ' ') { for (size_t i = 0; i < pos; i++) out[i] = out[i + 1]; pos--; }
    return pos;
}
// Signature of what the line says: settings and every state with its reason. A change of signature is a new line.
static inline uint32_t opt_signature(const OptState *s)
{
    char b[400];
    size_t n = opt_format(b, sizeof(b), s);
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < n; i++) { h ^= (uint8_t)b[i]; h *= 16777619u; }
    return h;
}
// Names of the options whose state (not value) differs between two snapshots, for the runtime line ("below30 ready->active").
static inline size_t opt_changes(char *out, size_t cap, const OptState *a, const OptState *b)
{
    if (!cap) return 0;
    out[0] = 0;
    size_t pos = 0;
    const struct { const char *name; OptStat (*f)(const OptState *); } k[] = {
        { "framegen", opt_fg }, { "preset", opt_mode }, { "below30", opt_b30 }, { "shallow_queue", opt_shq }, { "sharpness", opt_sharp },
        { "enhance", opt_aa }, { "game_vectors", opt_gv }, { "hud_protect", opt_hud }, { "fps_lock", opt_lock } };
    for (size_t i = 0; i < sizeof(k) / sizeof(k[0]); i++) {
        OptStat x = k[i].f(a), y = k[i].f(b);
        if (x.st == y.st) continue;
        int m = snprintf(out + pos, cap - pos, "%s%s %s->%s", pos ? ", " : "", k[i].name, opt_word(x, 0), opt_word(y, 0));
        if (m <= 0 || (size_t)m >= cap - pos) break;
        pos += (size_t)m;
    }
    return pos;
}
