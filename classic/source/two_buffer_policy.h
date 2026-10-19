#pragma once
#include <stdint.h>

// The present-copy allocation is partitioned into k mids followed by k copies.
// g_nmid describes the first allocation, not this partition; do not use it here.
static inline uint32_t tb_mid_index(uint32_t ngame, uint32_t k, uint32_t cursor, uint32_t offset)
{
    return ngame + (cursor + offset) % k;
}
static inline int64_t tb_tag_real_arg(int64_t arg)
{
    return (int64_t)((uint64_t)arg ^ (1ull << 63));
}
