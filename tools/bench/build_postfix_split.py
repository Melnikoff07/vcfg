"""Stage AA1 real+mid while retaining raw prior colors and immutable post sources."""
from build_gridmix_plugin import replace_once


def stage_split(text):
    text=replace_once(text,'static int g_postfix_ready;', '''static int g_postfix_ready;
static uint32_t g_postfix_raw_seq;
static int g_postfix_raw_valid;
_Static_assert(152u * 4u <= SLOT_FB2_OFF, "real post table overlaps second flip DCB");
_Static_assert(0x780u + 24u * 4u <= 0x800u, "copy tables overlap VS data");''')
    text=text.replace('0xA00000u','0x1200000u')
    text=replace_once(text,'g_cfg.sharpness == 100 && g_cfg.upscale == 0 &&','g_cfg.sharpness == 100 && g_cfg.upscale == 1 &&')
    anchor='    fill_table8(tb, P, C, M1, pitch, tidx, r, mid1_packed, base_flags, g_work, g_mon, stats);'
    text=replace_once(text,anchor,'''    uint64_t raw_history = (uint64_t)(uintptr_t)g_postfix_tmp + 0x800000u;
    int raw_prior = postfix_on && g_postfix_raw_valid && seq == g_postfix_raw_seq + 1u;
    fill_table8(tb, raw_prior ? raw_history : P, C, M1, pitch, tidx, r, mid1_packed, base_flags, g_work, g_mon, stats);
    static int raw_last = -1;
    if (postfix_on && raw_last != raw_prior) {
        raw_last = raw_prior;
        lg("POSTSPLIT raw-prior=%d seq=%u previous=%u saved=%llx\\n", raw_prior, seq, g_postfix_raw_seq, (unsigned long long)raw_history);
    }
    if (!postfix_on) g_postfix_raw_valid = 0;''')
    # Second-mid region is reserved for the real post's full descriptor table.
    text=replace_once(text,'uint32_t *ctb = (uint32_t *)(slotb + SLOT_TB2_OFF);',
                      'uint32_t *ctb = (uint32_t *)(slotb + 0x680u);')
    anchor='''    if (gv_viz_active()) {
        PASS_BARRIER();'''
    extra='''    if (postfix_on) {
        uint32_t *htb = (uint32_t *)(slotb + 0x700u);
        uint32_t *rtb = (uint32_t *)(slotb + SLOT_TB2_OFF);
        uint32_t *ftb = (uint32_t *)(slotb + 0x780u);
        uint64_t htbl = (uint64_t)(uintptr_t)htb, rtbl = (uint64_t)(uintptr_t)rtb, ftbl = (uint64_t)(uintptr_t)ftb;
        memset(htb, 0, 24u * sizeof(uint32_t));
        make_t(htb + 8, C, 1920, 1080, pitch, tidx);
        make_t(htb + 16, raw_history, 1920, 1080, pitch, tidx);
        memset(rtb, 0, 152u * sizeof(uint32_t));
        make_t(rtb + 16, C, 1920, 1080, pitch, tidx);
        make_t(rtb + 144, (uint64_t)(uintptr_t)g_postfix_tmp, 1920, 1080, pitch, tidx);
        rtb[136] = 0u; rtb[137] = 1u | (1u << 2); // AA1 only on real, no sharpening
        memset(ftb, 0, 24u * sizeof(uint32_t));
        make_t(ftb + 8, (uint64_t)(uintptr_t)g_postfix_tmp, 1920, 1080, pitch, tidx);
        make_t(ftb + 16, C, 1920, 1080, pitch, tidx);

        // Save raw C after warp consumption, before real AA. Each table is immutable.
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)htbl; *p++ = (uint32_t)(htbl >> 32);
        SET_PASS(g_sh8_off[16], PCOPY8_RSRC1, PCOPY8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)rtbl; *p++ = (uint32_t)(rtbl >> 32);
        SET_PASS(g_postfix_off, POSTOUT8_RSRC1, POSTOUT8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
        *p++ = PKT3(0x58, 6, 0); *p++ = 0x88400000; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0x0A;
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)ftbl; *p++ = (uint32_t)(ftbl >> 32);
        SET_PASS(g_sh8_off[16], PCOPY8_RSRC1, PCOPY8_RSRC2); DISPATCH(240, 135);
        PASS_BARRIER();
        *p++ = PKT3(0x76, 3, 1); *p++ = 0x240; *p++ = (uint32_t)tbl; *p++ = (uint32_t)(tbl >> 32);
        g_postfix_raw_seq = seq; g_postfix_raw_valid = 1;
    }
'''
    text=replace_once(text,anchor,extra+anchor)
    text=text.replace('/postfix_CUSA00557','/postfix_split_CUSA00557')
    text=text.replace('POSTFIX','POSTSPLIT').replace('raw-real=1','raw-history=1 real-AA=1')
    text=text.replace('raw real + out-of-place mid, no cut replacement','raw history + immutable AA1 real/mid, no cut replacement')
    return text
