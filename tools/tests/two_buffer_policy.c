// Run as LLVM bitcode on the host; no console APIs or GPU code are executed.
#include "../../classic/source/two_buffer_policy.h"
int main(void)
{
    for (uint32_t k = 2; k <= 3; k++) {
        uint32_t cursor = 0, seen = 0, random = 17;
        for (uint32_t frame = 0; frame < 100000; frame++) {
            random = random * 1664525u + 1013904223u;
            uint32_t mids = 1 + (random >> 31);
            uint32_t a = tb_mid_index(2, k, cursor, 0);
            uint32_t b = tb_mid_index(2, k, cursor, 1);
            uint32_t copy = 2 + k + frame % k;
            if (a < 2 || a >= 2 + k || b < 2 || b >= 2 + k || a == b) return 1;
            if (copy == a || copy == b || copy >= 2 + 2 * k) return 2;
            seen |= 1u << (a - 2);
            if (mids == 2) seen |= 1u << (b - 2);
            cursor = (cursor + mids) % k;
        }
        if (seen != (1u << k) - 1) return 3;
    }
    const int64_t args[] = {0, 1, -1, INT64_MIN, INT64_MAX, 0x123456789abcdefLL};
    for (unsigned i = 0; i < sizeof(args) / sizeof(args[0]); i++) {
        int64_t tag = tb_tag_real_arg(args[i]);
        if (tag == args[i] || tb_tag_real_arg(tag) != args[i]) return 4;
    }
    return 0;
}
