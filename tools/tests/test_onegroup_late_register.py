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

    def test_copy_checked_and_drained(self):
        cp = body("static int og2_copy(void *dst, const void *src, size_t n)")
        self.assertIn("if (!dcb_mem_ok((uintptr_t)src, n) || !og2_cpu_writable((uintptr_t)dst, n)) return 0;", cp)
        self.assertIn('__asm__ volatile("sfence" ::: "memory");', cp)
        self.assertIn("(prot & 0x02 /*PROT_CPU_WRITE*/) == 0", body("static int og2_cpu_writable(uintptr_t a, size_t n)"))


if __name__ == "__main__":
    unittest.main()
