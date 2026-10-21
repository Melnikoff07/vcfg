// Regional CUSA title family mapping (maps regional/edition Title IDs to their canonical family ID).
#pragma once
#include <string.h>

typedef struct {
    const char *id;
    const char *family;
} VcfgTitleAlias;

static const VcfgTitleAlias k_vcfg_title_aliases[] = {
    // CUSA07439 family
    { "CUSA07439", "CUSA07439" },
    { "CUSA07399", "CUSA07439" },
    { "CUSA07413", "CUSA07439" },

    // CUSA08519 family
    { "CUSA08519", "CUSA08519" },
    { "CUSA03041", "CUSA08519" },
    { "CUSA08532", "CUSA08519" },
    { "CUSA08568", "CUSA08519" },

    // CUSA00411 family
    { "CUSA00411", "CUSA00411" },
    { "CUSA00419", "CUSA00411" },
    { "CUSA00880", "CUSA00411" },
    { "CUSA01439", "CUSA00411" },

    // CUSA00207 family
    { "CUSA00207", "CUSA00207" },
    { "CUSA00208", "CUSA00207" },
    { "CUSA00900", "CUSA00207" },
    { "CUSA01322", "CUSA00207" },
    { "CUSA01363", "CUSA00207" },
    { "CUSA03014", "CUSA00207" },
    { "CUSA03023", "CUSA00207" },
    { "CUSA03173", "CUSA00207" },
    { "CUSA03323", "CUSA00207" },

    // CUSA04529 family
    { "CUSA04529", "CUSA04529" },
    { "CUSA00341", "CUSA04529" },
    { "CUSA00912", "CUSA04529" },
    { "CUSA00917", "CUSA04529" },
    { "CUSA00918", "CUSA04529" },
    { "CUSA00919", "CUSA04529" },
    { "CUSA07737", "CUSA04529" },
    { "CUSA07875", "CUSA04529" },
    { "CUSA08347", "CUSA04529" },
    { "CUSA08352", "CUSA04529" },
    { "CUSA09564", "CUSA04529" },

    // CUSA17419 family
    { "CUSA17419", "CUSA17419" },
    { "CUSA17416", "CUSA17419" },
    { "CUSA18007", "CUSA17419" },

    // CUSA05571 family
    { "CUSA05571", "CUSA05571" },
    { "CUSA05572", "CUSA05571" },
    { "CUSA05573", "CUSA05571" },
    { "CUSA05574", "CUSA05571" },
    { "CUSA05725", "CUSA05571" },
    { "CUSA00527", "CUSA05571" },
    { "CUSA01438", "CUSA05571" },
    { "CUSA01440", "CUSA05571" },

    // CUSA08809 family
    { "CUSA08809", "CUSA08809" },
    { "CUSA08344", "CUSA08809" },
    { "CUSA08810", "CUSA08809" },
    { "CUSA08811", "CUSA08809" },
    { "CUSA12085", "CUSA08809" },

    // CUSA00557 family
    { "CUSA00557", "CUSA00557" },
    { "CUSA00552", "CUSA00557" },
    { "CUSA00554", "CUSA00557" },
    { "CUSA00556", "CUSA00557" },
    { "CUSA00558", "CUSA00557" },
    { "CUSA00559", "CUSA00557" },

    // CUSA09171 family
    { "CUSA09171", "CUSA09171" },
    { "CUSA07408", "CUSA09171" },
    { "CUSA09175", "CUSA09171" },
    { "CUSA09176", "CUSA09171" },
    { "CUSA09515", "CUSA09171" },

    // CUSA13801 family
    { "CUSA13801", "CUSA13801" },
    { "CUSA11993", "CUSA13801" },
    { "CUSA12605", "CUSA13801" },
    { "CUSA13795", "CUSA13801" },
    { "CUSA13800", "CUSA13801" },

    // CUSA11456 family
    { "CUSA11456", "CUSA11456" },
    { "CUSA11455", "CUSA11456" },
    { "CUSA13917", "CUSA11456" },
};

static inline const char *vcfg_title_family(const char *id)
{
    if (!id || !id[0]) return "";
    for (unsigned i = 0; i < sizeof(k_vcfg_title_aliases) / sizeof(k_vcfg_title_aliases[0]); i++) {
        if (!strcmp(id, k_vcfg_title_aliases[i].id))
            return k_vcfg_title_aliases[i].family;
    }
    return id;
}

static inline int vcfg_in_family(const char *id, const char *canon)
{
    return strcmp(vcfg_title_family(id), canon) == 0;
}

static inline int vcfg_is_verified_family(const char *id)
{
    const char *f = vcfg_title_family(id);
    static const char *const k_ver[] = {
        "CUSA07439", "CUSA08519", "CUSA00411", "CUSA00207", "CUSA04529", "CUSA17419",
        "CUSA05571", "CUSA08809", "CUSA00557", "CUSA09171", "CUSA13801", "CUSA11456"
    };
    for (unsigned i = 0; i < sizeof(k_ver) / sizeof(k_ver[0]); i++) {
        if (!strcmp(f, k_ver[i])) return 1;
    }
    return 0;
}
