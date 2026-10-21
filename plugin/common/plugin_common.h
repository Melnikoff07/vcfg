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

size_t Detour_GetInstructionSize(Detour *This, uint64_t Address, size_t MinSize);
void Detour_WriteJump64(Detour *This, void *Address, uint64_t Destination);
void Detour_WriteJump32(Detour *This, void *Address, uint64_t Destination);

static uint8_t g_vcfg_tramp_pool[32][16] __attribute__((aligned(16)));
static int g_vcfg_tramp_used = 0;

static inline void *vcfg_hook_install(Detour *d, uint64_t fn_ptr, void *hk, const char *name)
{
    if (!fn_ptr || !hk) return NULL;
    Detour_Construct(d, DetourMode_x32);
    size_t ins_sz = Detour_GetInstructionSize(d, fn_ptr, sizeof(d->JumpInstructions32));
    if (ins_sz < sizeof(d->JumpInstructions32)) return NULL;

    void *tramp = NULL;
    if (g_vcfg_tramp_used < 32) {
        tramp = (void *)g_vcfg_tramp_pool[g_vcfg_tramp_used++];
        sceKernelMprotect(tramp, 16, VM_PROT_ALL);
    } else {
        if (sceKernelMmap(0, sizeof(d->JumpInstructions64), VM_PROT_ALL, 0x1000 | 0x2, -1, 0, &tramp) < 0 || !tramp)
            return NULL;
    }

    int64_t rel_tramp = (int64_t)(uintptr_t)tramp - (int64_t)(fn_ptr + sizeof(d->JumpInstructions32));
    int64_t rel_hk    = (int64_t)(uintptr_t)hk    - (int64_t)(fn_ptr + sizeof(d->JumpInstructions32));
    uint64_t jmp32_dst = 0;
    if (rel_tramp >= -0x7FFFFFFFLL && rel_tramp <= 0x7FFFFFFFLL) {
        Detour_WriteJump64(d, tramp, (uint64_t)(uintptr_t)hk);
        jmp32_dst = (uint64_t)(uintptr_t)tramp;
    } else if (rel_hk >= -0x7FFFFFFFLL && rel_hk <= 0x7FFFFFFFLL) {
        jmp32_dst = (uint64_t)(uintptr_t)hk;
    } else {
        Detour_Construct(d, DetourMode_x64);
        return Detour_DetourFunction(d, fn_ptr, hk);
    }

    d->TrampolinePtr = tramp;
    d->FunctionPtr = (void *)(uintptr_t)fn_ptr;
    d->HookPtr = hk;
    sceKernelMprotect((void *)(uintptr_t)fn_ptr, ins_sz, VM_PROT_ALL);
    d->StubSize = ins_sz + sizeof(d->JumpInstructions64);
    if (sceKernelMmap(0, d->StubSize, VM_PROT_ALL, 0x1000 | 0x2, -1, 0, &d->StubPtr) < 0 || !d->StubPtr)
        return NULL;

    const uint8_t *p = (const uint8_t *)(uintptr_t)fn_ptr;
    if (p[0] == 0xE9u && ins_sz == 5) {
        int32_t orig_rel = *(const int32_t *)(p + 1);
        uint64_t orig_dst = (uint64_t)((intptr_t)fn_ptr + 5 + (intptr_t)orig_rel);
        memset(d->StubPtr, 0x90, ins_sz);
        Detour_WriteJump64(d, d->StubPtr, orig_dst);
    } else {
        memcpy(d->StubPtr, (void *)(uintptr_t)fn_ptr, ins_sz);
        Detour_WriteJump64(d, (void *)((uintptr_t)d->StubPtr + ins_sz), fn_ptr + ins_sz);
    }

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
