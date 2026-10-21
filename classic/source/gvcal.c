// gvcal.c - see gvcal.h. No libm: sqrt / fabs are compiler builtins (sqrtss / andps on x86_64).
#include "gvcal.h"
#include <stdio.h>
#include <string.h>

#define TEX_MIN   384u      // block texture (sum of |shift-by-2 differences| over 2 x 64 sqrt-luma bytes)
#define SAD_DIV   3u        // reliable match: sad <= tex / 3
#define FMAX      160.0f    // |flow| limit for calibration blocks (px)
#define COH       1.0f      // neighbour coherence (px, per component)
#define MIN_REL   120u      // reliable blocks for a frame to count
#define STD_INF   1.0f      // flow std (px) for an axis to be informative in a frame
#define MODE_SHARE 0.20f    // a single exact raw value on >= 20 % of the textured blocks = sentinel candidate

// own fabs / sqrt: the plugin is built without -O, where the builtins become libm calls
static float fabs_(float x) { return x < 0.0f ? -x : x; }
static float sqrt_(float x)
{
    if (!(x > 0.0f)) return 0.0f;
    uint32_t u; float y;
    memcpy(&u, &x, 4);
    u = 0x1FBD1DF5u + (u >> 1);                 // initial estimate, then Newton (relative error < 1e-7 after 4)
    memcpy(&y, &u, 4);
    for (int i = 0; i < 4; i++) y = 0.5f * (y + x / y);
    return y;
}
static int finite_(float x) { uint32_t u; memcpy(&u, &x, 4); return (u & 0x7F800000u) != 0x7F800000u; }
static uint32_t bits_(float x) { uint32_t u; memcpy(&u, &x, 4); return u; }

void gvcal_reset(gvcal_t *c) { memset(c, 0, sizeof(*c)); }

void gvcal_set_res(gvcal_t *c, uint32_t w, uint32_t h)
{
    if (w < 320u || h < 180u) return;
    c->cur_w = w;
    c->cur_h = h;
    if (!c->ref_w) {
        c->ref_w = w;
        c->ref_h = h;
    } else if (w != c->ref_w || h != c->ref_h) {
        c->drs_varied = 1u;
    }
}

// k-th smallest of a[0..n) (quickselect, in place)
static float kth(float *a, int n, int k)
{
    int lo = 0, hi = n - 1;
    while (lo < hi) {
        float p = a[(lo + hi) >> 1];
        int i = lo, j = hi;
        while (i <= j) {
            while (a[i] < p) i++;
            while (a[j] > p) j--;
            if (i <= j) { float t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; }
        }
        if (k <= j) hi = j; else if (k >= i) lo = i; else return a[k];
    }
    return a[k];
}

typedef struct { double n, sr, sf, srr, sff, srf; } sums_t;
static void sums_add(sums_t *s, double r, double f) { s->n++; s->sr += r; s->sf += f; s->srr += r * r; s->sff += f * f; s->srf += r * f; }
// slope, intercept, R^2 of f ~ a r + b; returns 0 if degenerate (raw constant)
static int fit(const sums_t *s, float *a, float *b, float *r2, float *varf)
{
    if (s->n < 2) return 0;
    double mr = s->sr / s->n, mf = s->sf / s->n;
    double vr = s->srr / s->n - mr * mr, vf = s->sff / s->n - mf * mf, cv = s->srf / s->n - mr * mf;
    *varf = (float)(vf > 0 ? vf : 0);
    if (vr <= 1e-12 * (1.0 + mr * mr)) { *a = 0; *b = (float)mf; *r2 = 0; return 0; }
    *a = (float)(cv / vr);
    *b = (float)(mf - cv / vr * mr);
    *r2 = vf > 1e-9 ? (float)(cv * cv / (vr * vf)) : 0.0f;
    return 1;
}

static uint16_t g_idx[GV_NB];
static float g_res[GV_NB];

// motion class bit of a frame: standing / 8 directions x (slow, fast) / parallax
static uint32_t motion_class(float mx, float my, float sd)
{
    float m = sqrt_(mx * mx + my * my);
    uint32_t c;
    if (m < 2.0f) c = 1u;
    else {
        int dir = (fabs_(mx) > fabs_(my)) ? (mx > 0 ? 0 : 2) : (my > 0 ? 1 : 3);
        if (fabs_(mx) > 0.4f * m && fabs_(my) > 0.4f * m) dir = 4 + (mx > 0) + 2 * (my > 0);
        c = 1u << (1 + dir + (m > 16.0f ? 8 : 0));
    }
    if (sd > 3.0f) c |= 1u << 17;
    return c;
}

int gvcal_add(gvcal_t *c, const gv_rec_t *rec, gvcal_frame_t *fo)
{
    gvcal_frame_t lf;
    if (!fo) fo = &lf;
    memset(fo, 0, sizeof(*fo));
    c->frames++;
    // ---- heavy hitter of exact raw values over textured blocks (sentinel candidate) ----
    uint32_t hv[8][2], hc[8], nh = 0, ntex = 0, nimg_tex = 0;
    for (int i = 0; i < GV_NB; i++) {
        const gv_rec_t *r = &rec[i];
        if (r->tex >= TEX_MIN) nimg_tex++;
        if (!(r->flags & GVREC_GVALID) || r->tex < TEX_MIN) continue;
        ntex++;
        uint32_t x = bits_(r->rx), y = bits_(r->ry);
        uint32_t k = 0;
        while (k < nh && (hv[k][0] != x || hv[k][1] != y)) k++;
        if (k < nh) hc[k]++;
        else if (nh < 8) { hv[nh][0] = x; hv[nh][1] = y; hc[nh] = 1; nh++; }
        else for (k = 0; k < 8; k++) if (hc[k]) hc[k]--;      // Misra-Gries decrement
    }
    uint32_t mk = 0;
    for (uint32_t k = 1; k < nh; k++) if (hc[k] > hc[mk]) mk = k;
    uint32_t mode_cnt = 0;
    if (nh) for (int i = 0; i < GV_NB; i++) {                  // exact count of the winner
        const gv_rec_t *r = &rec[i];
        if ((r->flags & GVREC_GVALID) && r->tex >= TEX_MIN && bits_(r->rx) == hv[mk][0] && bits_(r->ry) == hv[mk][1]) mode_cnt++;
    }
    fo->n_textured = ntex;
    fo->mode_share = ntex ? (float)mode_cnt / (float)ntex : 0.0f;
    int have_mode = ntex >= MIN_REL && fo->mode_share >= MODE_SHARE;
    uint32_t mx_ = have_mode ? hv[mk][0] : 0, my_ = have_mode ? hv[mk][1] : 0;
    // ---- reliable blocks ----
    int n = 0;
    double sfx = 0, sfy = 0, sff = 0;
    for (int by = 0; by < GV_BH; by++)
        for (int bx = 0; bx < GV_BW; bx++) {
            int i = by * GV_BW + bx;
            const gv_rec_t *r = &rec[i];
            if (!(r->flags & GVREC_GVALID) || (r->flags & GVREC_GK)) continue;
            if (r->tex < TEX_MIN || r->sad * SAD_DIV > r->tex) continue;
            if (!finite_(r->rx) || !finite_(r->ry) || !finite_(r->fx) || !finite_(r->fy)) continue;
            if (fabs_(r->rx) > 1e5f || fabs_(r->ry) > 1e5f) continue;
            if (fabs_(r->fx) > FMAX || fabs_(r->fy) > FMAX) continue;
            int nn = 0, ok = 1;
            const int ox[4] = { -1, 1, 0, 0 }, oy[4] = { 0, 0, -1, 1 };
            for (int k = 0; k < 4; k++) {
                int x = bx + ox[k], y = by + oy[k];
                if (x < 0 || y < 0 || x >= GV_BW || y >= GV_BH) continue;
                const gv_rec_t *q = &rec[y * GV_BW + x];
                nn++;
                if (fabs_(q->fx - r->fx) > COH || fabs_(q->fy - r->fy) > COH) ok = 0;
            }
            if (!ok || nn < 3) continue;
            if (have_mode && bits_(r->rx) == mx_ && bits_(r->ry) == my_) {
                // the repeated value: kept out of the fit; its flow decides "sentinel or legit value" later
                if (c->mode_frames == 0 || (c->mode_x == mx_ && c->mode_y == my_)) {
                    c->mode_n++; c->mode_sfx += r->fx; c->mode_sfy += r->fy; c->mode_sff += r->fx * r->fx + r->fy * r->fy;
                }
                continue;
            }
            g_idx[n++] = (uint16_t)i;
            sfx += r->fx; sfy += r->fy; sff += r->fx * r->fx + r->fy * r->fy;
        }
    if (have_mode) {
        if (c->mode_frames == 0) { c->mode_x = mx_; c->mode_y = my_; }
        if (c->mode_x == mx_ && c->mode_y == my_) { c->mode_frames++; c->mode_share += fo->mode_share; }
    }
    fo->n_rel = (uint32_t)n;
    if (nimg_tex >= MIN_REL && ntex < MIN_REL / 2) {
        c->const_frames++;
    } else if (have_mode && fo->mode_share >= 0.9f && c->mode_n >= MIN_REL) {
        // (nearly) the whole buffer holds one value: count the frame if the image moved on those blocks. Either not
        // a velocity buffer, or an objects-only buffer with no moving object in view: a SOFT rejection (retried)
        double mn = c->mode_n, ex = c->mode_sfx / mn, ey = c->mode_sfy / mn;
        if (ex * ex + ey * ey >= 4.0) c->const_frames++;
    }
    if (n < (int)MIN_REL) return 0;
    fo->mean_fx = (float)(sfx / n); fo->mean_fy = (float)(sfy / n);
    {
        double v = sff / n - (double)fo->mean_fx * fo->mean_fx - (double)fo->mean_fy * fo->mean_fy;
        fo->std_f = sqrt_((float)(v > 0 ? v : 0));
    }
    int inf_any = 0;
    for (int ax = 0; ax < 2; ax++) {
        // Robust location and scale of raw and flow before the initial least-squares fit, so isolated corrupt
        // raw texels on a UV/NDC/UNORM buffer cannot blow up s.srr and drive the initial slope a -> 0.
        for (int j = 0; j < n; j++) { const gv_rec_t *r = &rec[g_idx[j]]; g_res[j] = ax ? r->fy : r->fx; }
        float medf = kth(g_res, n, n / 2);
        for (int j = 0; j < n; j++) { const gv_rec_t *r = &rec[g_idx[j]]; g_res[j] = ax ? r->ry : r->rx; }
        float medr = kth(g_res, n, n / 2);
        for (int j = 0; j < n; j++) { const gv_rec_t *r = &rec[g_idx[j]]; g_res[j] = fabs_((ax ? r->ry : r->rx) - medr); }
        float mad_r = kth(g_res, n, n / 2);
        float p95_r = kth(g_res, n, (n * 19) / 20);
        float rscale = 1.4826f * mad_r;
        if (p95_r > rscale) rscale = p95_r;
        float rmax = 10.0f * rscale + 1e-5f * fabs_(medr) + 1e-6f;
        float rthr = 5.0f * 1.4826f * mad_r + 1e-6f * fabs_(medr) + 1e-9f;

        // A uniform pan has (almost) no flow variance INSIDE the frame: the frame then contributes one point
        // (mean raw, mean flow) and the slope comes from the differences BETWEEN frames. So the fit is done on
        // the sums over all frames; per frame we only trim outliers and keep the per-frame means.
        sums_t s = { 0 };
        for (int j = 0; j < n; j++) {
            const gv_rec_t *r = &rec[g_idx[j]];
            float rv = ax ? r->ry : r->rx, fv = ax ? r->fy : r->fx;
            if (fabs_(rv - medr) > rmax) continue;
            sums_add(&s, rv, fv);
        }
        float a, b, r2, vf;
        int ok = fit(&s, &a, &b, &r2, &vf);
        float mf = s.n > 0 ? (float)(s.sf / s.n) : medf;
        int moving = vf >= STD_INF * STD_INF || fabs_(mf) >= 2.0f;
        if (!moving) continue;
        if (!ok && vf >= STD_INF * STD_INF) c->flat++;          // raw constant while the flow varies inside the frame
        // trimming model: the global fit when there is one, else this frame's fit (if it has variance and correlation), else none
        sums_t g = { c->n[ax], c->sr[ax], c->sf[ax], c->srr[ax], c->sff[ax], c->srf[ax] };
        float ga, gb, gr2, gvf;
        int use_global = c->nf[ax] >= 2 && fit(&g, &ga, &gb, &gr2, &gvf) && gr2 > 0.5f;
        int use_frame = !use_global && ok && vf >= STD_INF * STD_INF && r2 >= 0.25f;
        for (int j = 0; j < n; j++) {
            const gv_rec_t *r = &rec[g_idx[j]];
            float rv = ax ? r->ry : r->rx, fv = ax ? r->fy : r->fx;
            float e = use_global ? fv - (ga * rv + gb) : use_frame ? fv - (a * rv + b) : fv - medf;
            g_res[j] = fabs_(e);
        }
        float mad = kth(g_res, n, n / 2);
        float thr = 3.0f * 1.4826f * mad;
        if (thr < 1.5f) thr = 1.5f;
        float drs_k = 1.0f;
        if (c->ref_w && c->cur_w && c->ref_h && c->cur_h)
            drs_k = ax ? ((float)c->ref_h / (float)c->cur_h) : ((float)c->ref_w / (float)c->cur_w);
        sums_t si = { 0 }, sp = { 0 }, sdrs = { 0 };
        for (int j = 0; j < n; j++) {
            const gv_rec_t *r = &rec[g_idx[j]];
            float rv = ax ? r->ry : r->rx, fv = ax ? r->fy : r->fx, pv = ax ? r->fpy : r->fpx;
            if (fabs_(rv - medr) > rmax) continue;
            float e = use_global ? fv - (ga * rv + gb) : use_frame ? fv - (a * rv + b) : fv - medf;
            if (fabs_(e) > thr) continue;
            if (!use_global && !use_frame && fabs_(rv - medr) > rthr) continue;
            sums_add(&si, rv, fv);
            sums_add(&sp, rv, pv);
            sums_add(&sdrs, rv * drs_k, fv);
        }
        if (si.n < MIN_REL / 2) continue;
        float fa_ = 0, fb_ = 0, fr2 = 0, fvf = 0, ap, bp, r2p, vfp;
        int fok = fit(&si, &fa_, &fb_, &fr2, &fvf);
        fit(&sp, &ap, &bp, &r2p, &vfp);
        fo->r2[ax] = fr2; fo->a[ax] = fa_; fo->b[ax] = fb_; fo->r2_prev[ax] = r2p; fo->n_inl[ax] = (uint32_t)si.n;
        // stale test: only meaningful with variance inside the frame
        if (fok && fvf >= 4.0f && r2p > fr2 + 0.05f) fo->stale = 1;
        c->n[ax] += si.n; c->sr[ax] += si.sr; c->sf[ax] += si.sf; c->srr[ax] += si.srr; c->sff[ax] += si.sff; c->srf[ax] += si.srf;
        c->drs_n[ax] += sdrs.n; c->drs_sr[ax] += sdrs.sr; c->drs_sf[ax] += sdrs.sf;
        c->drs_srr[ax] += sdrs.srr; c->drs_sff[ax] += sdrs.sff; c->drs_srf[ax] += sdrs.srf;
        uint32_t k = c->nf[ax] % GVCAL_MAXF;
        c->fmr[ax][k] = (float)(si.sr / si.n); c->fmf[ax][k] = (float)(si.sf / si.n);
        // per-frame slope only where the frame has variance AND a good fit (CUSA03041: a frame with R2 0.55 had
        // slope 0.35, the good ones 1.0, and the spread rule rejected the true buffer)
        c->fa[ax][k] = (fok && fvf >= 4.0f && fr2 >= 0.85f) ? fa_ : 0.0f;
        c->drs_fmr[ax][k] = (float)(sdrs.sr / sdrs.n);
        c->drs_fa[ax][k] = (fok && fvf >= 4.0f && fr2 >= 0.85f && drs_k > 0.0f) ? (fa_ / drs_k) : 0.0f;
        c->nf[ax]++;
        inf_any = 1;
    }
    if (!inf_any) return 0;
    fo->informative = 1;
    c->informative++;
    if (fo->stale) c->stale_votes++;
    c->classes |= motion_class(fo->mean_fx, fo->mean_fy, fo->std_f);
    return 1;
}

static int popc(uint32_t x) { int n = 0; while (x) { n += (int)(x & 1u); x >>= 1; } return n; }

static void eval_fit(const gvcal_t *c, int use_drs, float a[2], float b[2], float r2[2], float spread[2], float jit[2])
{
    for (int ax = 0; ax < 2; ax++) {
        sums_t s = use_drs
            ? (sums_t){ c->drs_n[ax], c->drs_sr[ax], c->drs_sf[ax], c->drs_srr[ax], c->drs_sff[ax], c->drs_srf[ax] }
            : (sums_t){ c->n[ax], c->sr[ax], c->sf[ax], c->srr[ax], c->sff[ax], c->srf[ax] };
        float vf = 0.0f;
        a[ax] = 0.0f; b[ax] = 0.0f; r2[ax] = 0.0f; spread[ax] = 0.0f; jit[ax] = 0.0f;
        fit(&s, &a[ax], &b[ax], &r2[ax], &vf);
        uint32_t m = c->nf[ax] < GVCAL_MAXF ? c->nf[ax] : GVCAL_MAXF;
        if (m >= 3 && a[ax] != 0.0f) {
            // spread of the per-frame slopes (relative mean absolute deviation) and jitter = spread of the
            // per-frame intercepts under the global slope (centered around b[ax] to avoid float cancellation on UNORM)
            double sa = 0, sj = 0, sj2 = 0;
            uint32_t ms = 0;
            for (uint32_t k = 0; k < m; k++) {
                float fak = use_drs ? c->drs_fa[ax][k] : c->fa[ax][k];
                float fmrk = use_drs ? c->drs_fmr[ax][k] : c->fmr[ax][k];
                if (fak != 0.0f) { sa += fabs_(fak / a[ax] - 1.0f); ms++; }
                double bj = ((double)c->fmf[ax][k] - (double)a[ax] * (double)fmrk) - (double)b[ax];
                sj += bj; sj2 += bj * bj;
            }
            spread[ax] = ms >= 3 ? (float)(sa / ms) : 0.0f;
            double mj = sj / m, vj = sj2 / m - mj * mj;
            jit[ax] = sqrt_((float)(vj > 0 ? vj : 0));
        }
    }
}

int gvcal_decide(const gvcal_t *c, gvcal_result_t *res)
{
    memset(res, 0, sizeof(*res));
    res->frames = c->informative;
    res->blocks = (uint32_t)(c->n[0] + c->n[1]);
    res->classes = c->classes;
    res->cal_w = c->ref_w;
    res->cal_h = c->ref_h;
    float a[2] = { 0, 0 }, b[2] = { 0, 0 }, r2[2] = { 0, 0 }, spread[2] = { 0, 0 }, jit[2] = { 0, 0 };
    eval_fit(c, 0, a, b, r2, spread, jit);
    if (c->drs_varied && c->ref_w && c->ref_h) {
        float da[2], db[2], dr2[2], dspread[2], djit[2];
        eval_fit(c, 1, da, db, dr2, dspread, djit);
        float sp0 = spread[0] > spread[1] ? spread[0] : spread[1];
        float dsp = dspread[0] > dspread[1] ? dspread[0] : dspread[1];
        if (fabs_(da[0]) >= 0.4f && fabs_(da[0]) <= 3.5f &&
            ((dr2[0] + dr2[1] > r2[0] + r2[1] + 0.02f) || (dsp + 0.02f < sp0 && dr2[0] >= 0.8f))) {
            for (int ax = 0; ax < 2; ax++) {
                a[ax] = da[ax]; b[ax] = db[ax]; r2[ax] = dr2[ax]; spread[ax] = dspread[ax]; jit[ax] = djit[ax];
            }
            res->drs_scale = 1;
        }
    } else if (c->ref_w && c->ref_h && fabs_(a[0]) >= 0.4f && fabs_(a[0]) <= 3.5f) {
        res->drs_scale = 1;
    }
    res->ax = a[0]; res->bx = b[0]; res->ay = a[1]; res->by = b[1];
    res->r2x = r2[0]; res->r2y = r2[1]; res->jit_x = jit[0]; res->jit_y = jit[1];
    res->slope_spread = spread[0] > spread[1] ? spread[0] : spread[1];
    // units (log only; the fitted values are used)
    {
        float k = fabs_(a[0]);
        float zrx = k > 1e-3f ? (-b[0] / a[0]) : 0.0f;
        int unorm_bias = fabs_(zrx - 0.5f) < 0.02f;
        const char *u = "custom";
        if (k > 0.97f && k < 1.03f) u = "pixels";
        else if (k > 1.94f && k < 2.06f) u = "half-res pixels";
        else if (k > 0.48f && k < 0.52f) u = "4K pixels";
        else if (k > 0.73f && k < 0.77f) u = "1440p pixels";
        else if (k > 1.16f && k < 1.24f) u = "900p pixels";
        else if (k > 1.45f && k < 1.55f) u = "720p pixels";
        else if (k >= 0.45f && k <= 3.1f) u = "DRS pixels";
        else if (k > 931.f && k < 989.f) u = "NDC (x W/2)";
        else if (k > 1862.f && k < 1978.f) u = unorm_bias ? "UNORM UV (x W, bias 0.5)" : "UV (x W)";
        else if (k > 3733.f && k < 3963.f) u = "UE4 encoded (x W/2/0.2495)";
        else if (unorm_bias) u = "UNORM biased";
        int flip = (a[0] < 0) != (a[1] < 0);
        snprintf(res->units, sizeof(res->units), "%s%s%s", u, a[0] < 0 ? ", cur-prev" : ", prev-cur", flip ? ", Y up" : "");
    }
    // sentinel: the repeated value is "not written" if the flow on those blocks is NOT what it decodes to
    if (c->mode_frames >= 3 && c->mode_n >= 100) {
        float ux, uy;
        memcpy(&ux, &c->mode_x, 4); memcpy(&uy, &c->mode_y, 4);
        double px = a[0] * ux + b[0], py = a[1] * uy + b[1];
        double mse = (c->mode_sff - 2.0 * (px * c->mode_sfx + py * c->mode_sfy)) / c->mode_n + px * px + py * py;
        if (mse >= 4.0) { res->sentinel = 1; res->sent_x = c->mode_x; res->sent_y = c->mode_y; }
    }
    int fx = (int)(c->nf[0] < 1000 ? c->nf[0] : 1000), fy = (int)(c->nf[1] < 1000 ? c->nf[1] : 1000);
    // ---- rejections (enough evidence against) ----
    if (c->const_frames >= 8 && c->informative * 2 < c->const_frames) {
        res->state = -1; res->soft = 1;
        snprintf(res->reason, sizeof(res->reason), "one value everywhere in %u moving frames (empty objects-only buffer?)", c->const_frames);
        return -1;
    }
    {   // raw constant over all frames while the flow changed: not a velocity buffer (cleared / unused / wrong copy)
        double vr = c->n[0] > 1 ? c->srr[0] / c->n[0] - (c->sr[0] / c->n[0]) * (c->sr[0] / c->n[0]) : 0;
        double vf = c->n[0] > 1 ? c->sff[0] / c->n[0] - (c->sf[0] / c->n[0]) * (c->sf[0] / c->n[0]) : 0;
        double mr = c->n[0] > 1 ? c->sr[0] / c->n[0] : 0;
        if (fx >= 8 && vf > 4.0 && vr <= 1e-12 * (1.0 + mr * mr)) { res->state = -1; snprintf(res->reason, sizeof(res->reason), "raw value constant over %d moving frames", fx); return -1; }
    }
    if (c->informative >= 8 && c->flat * 2 >= c->informative) { res->state = -1; snprintf(res->reason, sizeof(res->reason), "constant buffer while the image moves (%u of %u frames)", c->flat, c->informative); return -1; }
    if (fx >= 5 && r2[0] < 0.3f && (fy < 3 || r2[1] < 0.3f)) { res->state = -1; snprintf(res->reason, sizeof(res->reason), "no correlation with flow (R2 x %.2f y %.2f)", r2[0], r2[1]); return -1; }
    if (c->informative >= 8 && c->stale_votes * 2 > c->informative) { res->state = -1; snprintf(res->reason, sizeof(res->reason), "matches the PREVIOUS frame's flow better (stale copy, %u/%u)", c->stale_votes, c->informative); return -1; }
    if (fx >= 12 && spread[0] > 0.35f) { res->state = -1; snprintf(res->reason, sizeof(res->reason), "slope changes from frame to frame (spread %.2f): not a linear encoding", spread[0]); return -1; }
    // ---- acceptance ----
    if (fx < 8) { snprintf(res->reason, sizeof(res->reason), "pending: %d informative frames for x (need 8)", fx); return 0; }
    if (fy < 4) { snprintf(res->reason, sizeof(res->reason), "pending: need vertical motion (look up / down), %d frames", fy); return 0; }
    if (popc(c->classes) < 2) { snprintf(res->reason, sizeof(res->reason), "pending: need a different camera motion (turn / strafe / stand)"); return 0; }
    if (res->blocks < 3000) { snprintf(res->reason, sizeof(res->reason), "pending: %u blocks (need 3000)", res->blocks); return 0; }
    if (r2[0] < 0.8f || r2[1] < 0.8f) { snprintf(res->reason, sizeof(res->reason), "pending: R2 x %.2f y %.2f below 0.8", r2[0], r2[1]); return 0; }
    if (spread[0] > 0.2f || spread[1] > 0.2f) { snprintf(res->reason, sizeof(res->reason), "pending: slope spread %.2f / %.2f", spread[0], spread[1]); return 0; }
    res->state = 1;
    snprintf(res->reason, sizeof(res->reason), "accepted");
    return 1;
}

int gvcal_check(const gvcal_result_t *p, const gv_rec_t *rec, float *frac, uint32_t *nrel)
{
    uint32_t n = 0, ok = 0;
    double sf = 0, sff = 0;
    for (int i = 0; i < GV_NB; i++) {
        const gv_rec_t *r = &rec[i];
        if (!(r->flags & GVREC_GVALID) || (r->flags & GVREC_GK)) continue;
        if (r->tex < TEX_MIN || r->sad * SAD_DIV > r->tex) continue;
        if (!finite_(r->rx) || !finite_(r->ry) || fabs_(r->fx) > FMAX || fabs_(r->fy) > FMAX) continue;
        if (p->sentinel && bits_(r->rx) == p->sent_x && bits_(r->ry) == p->sent_y) continue;
        float px = p->ax * r->rx + p->bx, py = p->ay * r->ry + p->by;
        float m = fabs_(r->fx) > fabs_(r->fy) ? fabs_(r->fx) : fabs_(r->fy);
        float tol = m * 0.1f > 1.5f ? m * 0.1f : 1.5f;
        n++;
        if (fabs_(r->fx - px) <= tol && fabs_(r->fy - py) <= tol) ok++;
        sf += r->fx + r->fy; sff += r->fx * r->fx + r->fy * r->fy;
    }
    if (nrel) *nrel = n;
    *frac = n ? (float)ok / (float)n : 0.0f;
    if (n < MIN_REL) return -1;
    double mf = sf / (2.0 * n), v = sff / (2.0 * n) - mf * mf;
    if (v < STD_INF * STD_INF && *frac > 0.5f) return -1;   // standing still: agreement is trivial
    return *frac >= 0.6f ? 1 : 0;
}
