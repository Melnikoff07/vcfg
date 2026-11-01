"""One display group at the late activation (CUSA02299): what the C harness does not compile.

The call sequence (temporary group or blank screen, drop the game's slot, one group, the game's buffer back, temporary
slot dropped, every fallback to the separate group) is exercised by the C harness of test_lowmem_valve.py, case 9c,
which compiles safe_try_activate and og2_tick. This test checks the rest: the path is on by default for the CUSA02299
family only and reads no option file, the game's attribute slot comes from its own registration at index 0, the
temporary group is dropped on the game's later flips, and the CPU copy into our buffer is checked and drained.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAIN = (ROOT / "classic" / "source" / "main.c").read_text(encoding="utf-8")


def body(head):
    """The body of the definition that starts with head (a prototype ending in ';' is skipped)."""
    m = re.search(re.escape(head) + r"[^;{]*?\n\{\n(.*?)\n\}\n", MAIN, re.S)
    assert m, head
    return m.group(1)


class OneGroup(unittest.TestCase):
    def test_default_on_for_the_family_only_without_option_file(self):
        g = body("static int safe_one_group(")
        self.assertRegex(g, r'^\s+if \(g_og2 < 0\) g_og2 = vcfg_in_family\(g_title_id, "CUSA02299"\) \? 1 : 0;\n'
                            r'\s+if \(!g_og2\) return 0;')
        self.assertIn("static int g_og2 = -1;", MAIN)
        self.assertEqual(MAIN.count(") g_og2 = "), 1)   # decided once, at the first activation
        self.assertNotIn("onegroup_", MAIN)            # no LOG_DIR/onegroup*_<TITLE> option

    def test_activation_takes_the_one_group_or_the_separate_group(self):
        act = body("static void safe_try_activate(uint32_t handle)")
        self.assertIn("int og = safe_one_group(handle, ext, nm, base, &r1);", act)
        self.assertRegex(act, r"if \(one_grp\) \{\n\s+base = g_ngame;\n\s+\} else if \(og == 2\) \{\n.*?\n\s+\} else \{\n"
                              r"\s+r1 = HOOK_CONTINUE\(sceVideoOutRegisterBuffers.*?\n\s+\(int32_t\)handle, base, ext, nm, &g_attr\);")
        self.assertIn("g_ext_attr_slot = one_grp ? -1 : r1;", act)
        self.assertLess(act.index("safe_one_group("), act.index('safe_refuse("REGISTER FAILED")'))

    def test_game_slot_from_its_registration(self):
        self.assertIn("if (rn >= 0 && startIndex == 0) g_game_attr_slot = rn;", MAIN)
        hook = MAIN.split("int32_t sceVideoOutRegisterBuffers_hook(", 1)[1]
        self.assertLess(hook.index("handle, startIndex, addresses, bufferNum, attr);"),
                        hook.index("if (rn >= 0 && startIndex == 0) g_game_attr_slot = rn;"))

    def test_temporary_group_dropped_on_later_flips(self):
        fn = MAIN.split("static int32_t flip_inner_ex(", 1)[1]
        self.assertIn("if (g_flips > INJECT_AFTER_FLIPS) og2_tick((int32_t)handle);", fn)
        self.assertEqual(MAIN.count("og2_tick((int32_t)handle);"), 1)
        # the activation also runs from the VideoOut flip path (from_vout): drop the temporary group there too
        vo = MAIN.split("int32_t sceVideoOutSubmitFlip_hook(", 1)[1].split("\n}\n", 1)[0]
        self.assertRegex(vo, r"tb_on_game_submit\(\);\n\s+og2_tick\(handle\);")
        self.assertIn("if (g_og2_pend_slot < 0) return;", body("static void og2_tick(int32_t handle)"))

    def test_game_registering_again_moves_ours_to_the_top(self):
        hook = MAIN.split("int32_t sceVideoOutRegisterBuffers_hook(", 1)[1]
        i_slot = hook.index("if (rn >= 0 && startIndex == 0) g_game_attr_slot = rn;")
        i_re = hook.index("if (rn >= 0 && startIndex == 0 && g_og2_active &&\n        og2_game_reregistered(handle, attr, ")
        i_fmt = hook.index("if (rn >= 0 && need_rereg_ext_fmt && attr && g_mbuf_state == 1 && g_ext_nm >= 1) {")
        self.assertLess(i_slot, i_re)
        self.assertLess(i_re, i_fmt)
        self.assertEqual(MAIN.count("og2_game_reregistered(handle"), 1)
        g = body("static int og2_game_reregistered(int32_t handle, const VoAttr *attr, int game_hi)")
        self.assertIn("int old = g_mbase, base = 16 - g_ext_nm;", g)
        self.assertIn("g_mbuf_state = -1;", g)

    def test_one_group_keeps_the_games_slot_and_copy_size(self):
        g = body("static int safe_one_group(")
        self.assertIn("const int32_t game_slot = g_game_attr_slot;", g)
        self.assertIn("if (r >= 0 && r != game_slot) {", g)
        self.assertIn("int32_t rr = og2_game_back(h, all, game_slot);", g)
        self.assertIn("size_t bytes = (size_t)g_pitch * (size_t)((g_bh + 63u) & ~63u) * 4u;", g)
        self.assertIn("if (one && bytes > one) bytes = one;", g)
        self.assertIn("for (int k = 0; k < 2 && rr < 0; k++) {", body("static int32_t og2_game_back("))

    def test_copy_checked_and_drained(self):
        cp = body("static int og2_copy(void *dst, const void *src, size_t n)")
        self.assertIn("if (!dcb_mem_ok((uintptr_t)src, n) || !og2_cpu_writable((uintptr_t)dst, n)) return 0;", cp)
        self.assertIn('__asm__ volatile("sfence" ::: "memory");', cp)
        self.assertIn("(prot & 0x02 /*PROT_CPU_WRITE*/) == 0", body("static int og2_cpu_writable(uintptr_t a, size_t n)"))


if __name__ == "__main__":
    unittest.main()
