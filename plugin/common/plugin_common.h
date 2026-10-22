#include "git_ver.h"
#include <Common.h>

#define GOLDHEN_PATH "/data/GoldHEN"

#define u8  uint8_t
#define u16 uint16_t
#define u32 uint32_t
#define u64 uint64_t
#define s8  int8_t
#define s16 int16_t
#define s32 int32_t
#define s64 int64_t
#define f32 float
#define f64 double
#define TEX_ICON_SYSTEM "cxml://psnotification/tex_icon_system"
#define MAX_PATH_ 260

#define attr_module_hidden __attribute__((weak)) __attribute__((visibility("hidden")))
#define attr_public __attribute__((visibility("default")))

#if (__FINAL__) == 1
#define BUILD_TYPE "(Release)"
#define debug_printf(a, args...)
#else
#define BUILD_TYPE "(Debug)"
#define debug_printf(a, args...) klog("[%s] (%s:%d) " a,  __func__,__FILE__, __LINE__, ##args)
#endif

#define final_printf(a, args...) klog("(%s:%d) " a, __FILE__, __LINE__, ##args)
#define boot_ver() {\
    final_printf("Commit: %s Branch: %s Build: %i %s\n", GIT_COMMIT, GIT_VER, GIT_NUM, BUILD_TYPE);\
    final_printf("Built: %s\n", BUILD_DATE);\
    final_printf("GoldHEN SDK Ver: 0x%08x\n", GOLDHEN_SDK_VERSION);\
    final_printf("GoldHEN System SDK Ver: 0x%08x\n", sys_sdk_version());\
}

#define print_proc_info() {\
    final_printf("process info\n");\
    final_printf("pid: %d\n", procInfo.pid);\
    final_printf("name: %s\n", procInfo.name);\
    final_printf("path: %s\n", procInfo.path);\
    final_printf("titleid: %s\n", procInfo.titleid);\
    final_printf("contentid: %s\n", procInfo.contentid);\
    final_printf("version: %s\n", procInfo.version);\
    final_printf("base_address: 0x%lx\n", procInfo.base_address);\
}

void NotifyStatic(const char* IconUri, const char *text);
void Notify(const char* IconUri, const char *FMT, ...);
// Takes hardcoded input string 2 to strlen against during compile time.
// startsWith(input_1, "input 2");
#define startsWith(str1, str2) (strncmp(str1, str2, __builtin_strlen(str2)) == 0)

#include <HDE64.h>

size_t Detour_GetInstructionSize(Detour *This, uint64_t Address, size_t MinSize);
void Detour_WriteJump64(Detour *This, void *Address, uint64_t Destination);
void Detour_WriteJump32(Detour *This, void *Address, uint64_t Destination);

// Static trampoline pool placed in .text (PH[0], max_prot=RWX, lowest VA offset in PRX) plus a near-page
// allocator for when the PRX is loaded >2 GB above libSceVideoOut.sprx (e.g. 0x8803cf000 vs 0x800061410).
__attribute__((aligned(16), noinline, used)) static void vcfg_tramp_pool_stub(void) { __asm__ volatile(".rept 512\n\t.byte 0x90\n\t.endr\n\t"); }
static int g_vcfg_tramp_used = 0;
static uint8_t *g_vcfg_near_page = NULL;
static int g_vcfg_near_used = 0;

static inline int vcfg_in_rel32(uint64_t src_end, uint64_t dst)
{
    int64_t rel = (int64_t)dst - (int64_t)src_end;
    return (rel >= -0x7FFF0000LL && rel <= 0x7FFF0000LL);
}

static inline void *vcfg_alloc_near_tramp(uint64_t fn_ptr)
{
    uint64_t src_end = fn_ptr + 5;
    if (g_vcfg_near_page && g_vcfg_near_used < 256) {
        uint8_t *slot = g_vcfg_near_page + (g_vcfg_near_used * 64);
        if (vcfg_in_rel32(src_end, (uint64_t)(uintptr_t)slot)) {
            g_vcfg_near_used++;
            return slot;
        }
    }
    const uint64_t hints[8] = {
        (fn_ptr > 0x08000000ULL) ? ((fn_ptr - 0x08000000ULL) & ~0x3FFFULL) : 0x00000007F0000000ULL,
        0x00000007F0000000ULL,
        0x00000007E0000000ULL,
        0x00000007C0000000ULL,
        0x0000000808000000ULL,
        0x0000000820000000ULL,
        0x0000000840000000ULL,
        0x0000000860000000ULL
    };
    for (int i = 0; i < 8; i++) {
        void *page = NULL;
        if (sceKernelMmap((void *)(uintptr_t)hints[i], 0x4000, VM_PROT_ALL, 0x1000 | 0x2, -1, 0, &page) == 0 && page) {
            if (vcfg_in_rel32(src_end, (uint64_t)(uintptr_t)page)) {
                g_vcfg_near_page = (uint8_t *)page;
                g_vcfg_near_used = 1;
                return page;
            }
            sceKernelMunmap(page, 0x4000);
        }
    }
    uint64_t probe = (fn_ptr > 0x40000000ULL) ? ((fn_ptr - 0x40000000ULL) & ~0x3FFFULL) : 0x0000000790000000ULL;
    for (int step = 0; step < 64 && vcfg_in_rel32(src_end, probe); step++) {
        OrbisKernelVirtualQueryInfo vq;
        memset(&vq, 0, sizeof(vq));
        if (sceKernelVirtualQuery((const void *)(uintptr_t)probe, 0, &vq, sizeof(vq)) != 0) {
            void *page = NULL;
            if (sceKernelMmap((void *)(uintptr_t)probe, 0x4000, VM_PROT_ALL, 0x1000 | 0x2 | 0x10, -1, 0, &page) == 0 &&
                page && vcfg_in_rel32(src_end, (uint64_t)(uintptr_t)page)) {
                g_vcfg_near_page = (uint8_t *)page;
                g_vcfg_near_used = 1;
                return page;
            }
            probe += 0x02000000ULL;
        } else {
            uint64_t end_va = ((uint64_t)(uintptr_t)vq.unk02 + 0x3FFFULL) & ~0x3FFFULL;
            probe = (end_va > probe) ? end_va : (probe + 0x01000000ULL);
        }
    }
    return NULL;
}

static inline void *vcfg_alloc_stub_mem(uint64_t fn_ptr, size_t sz)
{
    if (sz <= 64) {
        void *near_slot = vcfg_alloc_near_tramp(fn_ptr);
        if (near_slot) return near_slot;
    }
    void *ptr = NULL;
    if (sceKernelMmap(0, sz, VM_PROT_ALL, 0x1000 | 0x2, -1, 0, &ptr) == 0 && ptr)
        return ptr;
    return NULL;
}

static inline void vcfg_build_stub(Detour *d, uint64_t fn_ptr, size_t ins_sz)
{
    const uint8_t *p = (const uint8_t *)(uintptr_t)fn_ptr;
    uint8_t *dst = (uint8_t *)d->StubPtr;
    size_t off = 0;
    size_t out = 0;
    while (off < ins_sz) {
        hde64s hs;
        memset(&hs, 0, sizeof(hs));
        size_t len = hde64_disasm((const void *)(uintptr_t)(fn_ptr + off), &hs);
        if (len == 0 || (hs.flags & F_ERROR) || off + len > ins_sz) len = ins_sz - off;
        if (len == 5 && p[off] == 0xE9u) {
            int32_t orig_rel = *(const int32_t *)(p + off + 1);
            uint64_t orig_dst = (uint64_t)((intptr_t)(fn_ptr + off + 5) + (intptr_t)orig_rel);
            Detour_WriteJump64(d, dst + out, orig_dst);
            return;
        }
        if (len == 2 && p[off] == 0xEBu) {
            int8_t orig_rel8 = *(const int8_t *)(p + off + 1);
            uint64_t orig_dst = (uint64_t)((intptr_t)(fn_ptr + off + 2) + (intptr_t)orig_rel8);
            if (orig_dst < fn_ptr || orig_dst >= fn_ptr + ins_sz) {
                Detour_WriteJump64(d, dst + out, orig_dst);
                return;
            }
        }
        if (len == 5 && p[off] == 0xE8u) {
            int32_t orig_rel = *(const int32_t *)(p + off + 1);
            uint64_t orig_dst = (uint64_t)((intptr_t)(fn_ptr + off + 5) + (intptr_t)orig_rel);
            uint64_t cur_end = (uint64_t)(uintptr_t)(dst + out + 5);
            if (vcfg_in_rel32(cur_end, orig_dst)) {
                dst[out] = 0xE8u;
                *(int32_t *)(dst + out + 1) = (int32_t)((int64_t)orig_dst - (int64_t)cur_end);
                out += 5;
            } else {
                static const uint8_t call_abs[8] = { 0xFF, 0x15, 0x02, 0x00, 0x00, 0x00, 0xEB, 0x08 };
                memcpy(dst + out, call_abs, 8);
                *(uint64_t *)(dst + out + 8) = orig_dst;
                out += 16;
            }
            off += len;
            continue;
        }
        memcpy(dst + out, p + off, len);
        if ((hs.flags & F_MODRM) && (hs.modrm & 0xC7) == 0x05 && (hs.flags & F_DISP32)) {
            size_t imm_sz = (hs.flags & F_IMM64) ? 8 : (hs.flags & F_IMM32) ? 4 :
                            (hs.flags & F_IMM16) ? 2 : (hs.flags & F_IMM8) ? 1 : 0;
            if (len >= 4 + imm_sz) {
                uint64_t target_va = (uint64_t)((int64_t)(fn_ptr + off + len) + (int64_t)(int32_t)hs.disp.disp32);
                uint64_t stub_next_rip = (uint64_t)(uintptr_t)(dst + out + len);
                int64_t new_disp = (int64_t)target_va - (int64_t)stub_next_rip;
                if (new_disp >= -0x80000000LL && new_disp <= 0x7FFFFFFFLL) {
                    *(int32_t *)(dst + out + len - 4 - imm_sz) = (int32_t)new_disp;
                }
            }
        }
        out += len;
        off += len;
    }
    Detour_WriteJump64(d, dst + out, fn_ptr + ins_sz);
}

static inline int vcfg_can_patch_pi14(uint64_t fn_ptr, const char *name, size_t *out_ins_sz)
{
    if (name && strcmp(name, "sceVideoOutSetFlipRate") == 0) return 0;
    const uint8_t *p = (const uint8_t *)(uintptr_t)fn_ptr;
    if (p[0] == 0xE9u || p[0] == 0xEBu) return 0;
    size_t off = 0;
    while (off < 14) {
        hde64s hs;
        memset(&hs, 0, sizeof(hs));
        size_t len = hde64_disasm((const void *)(uintptr_t)(fn_ptr + off), &hs);
        if (len == 0 || (hs.flags & F_ERROR)) return 0;
        if (hs.opcode == 0xC3 || hs.opcode == 0xC2 ||
            (off > 0 && (hs.opcode == 0xE9 || hs.opcode == 0xEB ||
                         (hs.opcode == 0xFF && (hs.modrm & 0x38) == 0x20)))) {
            return 0;
        }
        off += len;
    }
    *out_ins_sz = off;
    return 1;
}

static inline void vcfg_write_pi14_jump(void *addr, uint64_t dst, size_t ins_sz)
{
    uint8_t *b = (uint8_t *)addr;
    if (ins_sz > 14) memset(b + 14, 0x90, ins_sz - 14);
    b[0] = 0x48u;
    b[1] = 0xB8u;
    *(uint64_t *)(b + 2) = dst;
    b[10] = 0xFFu;
    b[11] = 0xE0u;
    b[12] = 0x90u;
    b[13] = 0x90u;
}

static inline void *vcfg_hook_install(Detour *d, uint64_t fn_ptr, void *hk, const char *name)
{
    if (!fn_ptr || !hk) return NULL;
    size_t pi14_sz = 0;
    if (vcfg_can_patch_pi14(fn_ptr, name, &pi14_sz)) {
        Detour_Construct(d, DetourMode_x64);
        d->FunctionPtr = (void *)(uintptr_t)fn_ptr;
        d->HookPtr = hk;
        sceKernelMprotect((void *)(uintptr_t)fn_ptr, pi14_sz, VM_PROT_ALL);
        d->StubSize = pi14_sz + sizeof(d->JumpInstructions64) + 16;
        d->StubPtr = vcfg_alloc_stub_mem(fn_ptr, d->StubSize);
        if (!d->StubPtr) return NULL;
        vcfg_build_stub(d, fn_ptr, pi14_sz);
        vcfg_write_pi14_jump((void *)(uintptr_t)fn_ptr, (uint64_t)(uintptr_t)hk, pi14_sz);
        klog("[vcfg_hook] %s: pi14 fn=%p ins=%zu stub=%p hk=%p\n",
             name, (void *)(uintptr_t)fn_ptr, pi14_sz, d->StubPtr, hk);
        return d->StubPtr;
    }

    Detour_Construct(d, DetourMode_x32);
    size_t ins_sz = Detour_GetInstructionSize(d, fn_ptr, sizeof(d->JumpInstructions32));
    if (ins_sz < sizeof(d->JumpInstructions32)) return NULL;

    uint64_t src_end = fn_ptr + sizeof(d->JumpInstructions32);
    void *tramp = NULL;
    uint64_t jmp32_dst = 0;

    if (vcfg_in_rel32(src_end, (uint64_t)(uintptr_t)hk)) {
        jmp32_dst = (uint64_t)(uintptr_t)hk;
    } else {
        if (g_vcfg_tramp_used < 32) {
            void *cand = (void *)((uint8_t *)(uintptr_t)&vcfg_tramp_pool_stub + (g_vcfg_tramp_used * 16));
            if (vcfg_in_rel32(src_end, (uint64_t)(uintptr_t)cand) &&
                sceKernelMprotect(cand, 16, VM_PROT_ALL) == 0) {
                g_vcfg_tramp_used++;
                tramp = cand;
            }
        }
        if (!tramp) {
            tramp = vcfg_alloc_near_tramp(fn_ptr);
        }
        if (tramp && vcfg_in_rel32(src_end, (uint64_t)(uintptr_t)tramp)) {
            Detour_WriteJump64(d, tramp, (uint64_t)(uintptr_t)hk);
            jmp32_dst = (uint64_t)(uintptr_t)tramp;
        }
    }

    if (!jmp32_dst) {
        Detour_Construct(d, DetourMode_x64);
        ins_sz = Detour_GetInstructionSize(d, fn_ptr, sizeof(d->JumpInstructions64));
        if (ins_sz < sizeof(d->JumpInstructions64)) return NULL;
        d->FunctionPtr = (void *)(uintptr_t)fn_ptr;
        d->HookPtr = hk;
        sceKernelMprotect((void *)(uintptr_t)fn_ptr, ins_sz, VM_PROT_ALL);
        d->StubSize = ins_sz + sizeof(d->JumpInstructions64) + 16;
        d->StubPtr = vcfg_alloc_stub_mem(fn_ptr, d->StubSize);
        if (!d->StubPtr) return NULL;
        vcfg_build_stub(d, fn_ptr, ins_sz);
        vcfg_write_pi14_jump((void *)(uintptr_t)fn_ptr, (uint64_t)(uintptr_t)hk, ins_sz);
        klog("[vcfg_hook] %s: x64-pi14 fn=%p ins=%zu stub=%p hk=%p\n",
             name, (void *)(uintptr_t)fn_ptr, ins_sz, d->StubPtr, hk);
        return d->StubPtr;
    }

    d->TrampolinePtr = tramp;
    d->FunctionPtr = (void *)(uintptr_t)fn_ptr;
    d->HookPtr = hk;
    sceKernelMprotect((void *)(uintptr_t)fn_ptr, ins_sz, VM_PROT_ALL);
    d->StubSize = ins_sz + sizeof(d->JumpInstructions64) + 16;
    d->StubPtr = vcfg_alloc_stub_mem(fn_ptr, d->StubSize);
    if (!d->StubPtr) return NULL;

    vcfg_build_stub(d, fn_ptr, ins_sz);

    memset((void *)(uintptr_t)fn_ptr, 0x90, ins_sz);
    Detour_WriteJump32(d, (void *)(uintptr_t)fn_ptr, jmp32_dst);
    klog("[vcfg_hook] %s: x32 fn=%p ins=%zu stub=%p tramp=%p\n",
         name, (void *)(uintptr_t)fn_ptr, ins_sz, d->StubPtr, (void *)(uintptr_t)jmp32_dst);
    return d->StubPtr;
}

#undef HOOK32
#define HOOK32(name) do { \
    vcfg_hook_install(&(Detour_##name), (uint64_t)(uintptr_t)(name), (void *)&(name##_hook), #name); \
} while (0)
