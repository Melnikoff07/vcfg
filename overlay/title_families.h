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
    { "CUSA07820", "CUSA04529" },
    { "CUSA07875", "CUSA04529" },
    { "CUSA08347", "CUSA04529" },
    { "CUSA08352", "CUSA04529" },
    { "CUSA09564", "CUSA04529" },
    { "CUSA10249", "CUSA04529" },
    { "CUSA13915", "CUSA04529" },
    { "CUSA14006", "CUSA04529" },

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
    { "CUSA08034", "CUSA08809" },
    { "CUSA07090", "CUSA08809" },
    { "CUSA08810", "CUSA08809" },
    { "CUSA08811", "CUSA08809" },
    { "CUSA12085", "CUSA08809" },

    // CUSA08344 family
    { "CUSA08344", "CUSA08344" },
    { "CUSA08308", "CUSA08344" },

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
    { "CUSA07410", "CUSA09171" },
    { "CUSA07411", "CUSA09171" },
    { "CUSA07412", "CUSA09171" },
    { "CUSA07435", "CUSA09171" },
    { "CUSA08966", "CUSA09171" },
    { "CUSA09175", "CUSA09171" },
    { "CUSA09176", "CUSA09171" },
    { "CUSA09193", "CUSA09171" },
    { "CUSA09515", "CUSA09171" },

    // CUSA13801 family
    { "CUSA13801", "CUSA13801" },
    { "CUSA11993", "CUSA13801" },
    { "CUSA12047", "CUSA13801" },
    { "CUSA12605", "CUSA13801" },
    { "CUSA13795", "CUSA13801" },
    { "CUSA13800", "CUSA13801" },
    { "CUSA14876", "CUSA13801" },

    // CUSA11456 family
    { "CUSA11456", "CUSA11456" },
    { "CUSA11455", "CUSA11456" },
    { "CUSA13323", "CUSA11456" },
    { "CUSA13917", "CUSA11456" },

    // CUSA14209 family
    { "CUSA14209", "CUSA14209" },
    { "CUSA14123", "CUSA14209" },
    { "CUSA14168", "CUSA14209" },
    { "CUSA14205", "CUSA14209" },
    { "CUSA14208", "CUSA14209" },
    { "CUSA14210", "CUSA14209" },

    // CUSA16734 family
    { "CUSA16734", "CUSA16734" },
    { "CUSA16596", "CUSA16734" },
    { "CUSA16725", "CUSA16734" },
    { "CUSA18278", "CUSA16734" },

    // CUSA10454 family
    { "CUSA10454", "CUSA10454" },
    { "CUSA09457", "CUSA10454" },
    { "CUSA10211", "CUSA10454" },
    { "CUSA10212", "CUSA10454" },
    { "CUSA10213", "CUSA10454" },

    // CUSA11100 family
    { "CUSA11100", "CUSA11100" },
    { "CUSA11099", "CUSA11100" },

    // CUSA12054 family
    { "CUSA12054", "CUSA12054" },
    { "CUSA11560", "CUSA12054" },

    // CUSA15090 family
    { "CUSA15090", "CUSA15090" },
    { "CUSA14897", "CUSA15090" },

    // CUSA18723 family
    { "CUSA18723", "CUSA18723" },
    { "CUSA18742", "CUSA18723" },
    { "CUSA28863", "CUSA18723" },

    // CUSA09311 family
    { "CUSA09311", "CUSA09311" },
    { "CUSA09303", "CUSA09311" },
    { "CUSA09505", "CUSA09311" },

    // CUSA18534 family
    { "CUSA18534", "CUSA18534" },
    { "CUSA18522", "CUSA18534" },
    { "CUSA18526", "CUSA18534" },
    { "CUSA33151", "CUSA18534" },

    // CUSA36842 family
    { "CUSA36842", "CUSA36842" },
    { "CUSA36843", "CUSA36842" },

    // CUSA01015 family
    { "CUSA01015", "CUSA01015" },

    // CUSA33388 family
    { "CUSA33388", "CUSA33388" },
    { "CUSA33387", "CUSA33388" },
    { "CUSA33389", "CUSA33388" },
    { "CUSA31497", "CUSA33388" },

    // CUSA34384 family
    { "CUSA34384", "CUSA34384" },
    { "CUSA34388", "CUSA34384" },
    { "CUSA34389", "CUSA34384" },
    { "CUSA34390", "CUSA34384" },

    // CUSA15778 family
    { "CUSA15778", "CUSA15778" },

    // CUSA00083 family
    { "CUSA00083", "CUSA00083" },

    // CUSA02320 family
    { "CUSA02320", "CUSA02320" },

    // CUSA03388 family
    { "CUSA03388", "CUSA03388" },

    // CUSA02299 family
    { "CUSA02299", "CUSA02299" },

    // CUSA10123 family
    { "CUSA10123", "CUSA10123" },

    // CUSA36375 family
    { "CUSA36375", "CUSA36375" },
    { "CUSA36374", "CUSA36375" },

    // Additional verified families
    { "CUSA08364", "CUSA08364" },
    { "CUSA06548", "CUSA06548" },
    { "CUSA10237", "CUSA10237" },
    { "CUSA11260", "CUSA11260" },
    { "CUSA11262", "CUSA11260" },
    { "CUSA11263", "CUSA11260" },
    { "CUSA12607", "CUSA11260" },
    { "CUSA32946", "CUSA32946" },
    { "CUSA28561", "CUSA28561" },
    { "CUSA01154", "CUSA01154" },
    { "CUSA04841", "CUSA04841" },
    { "CUSA17722", "CUSA17722" },
    { "CUSA32836", "CUSA32836" },
    { "CUSA01113", "CUSA01113" },
    { "CUSA00009", "CUSA00009" },
    { "CUSA00002", "CUSA00009" },
    { "CUSA00008", "CUSA00009" },
    { "CUSA00100", "CUSA00009" },
    { "CUSA00093", "CUSA00093" },
    { "CUSA00049", "CUSA00093" },
    { "CUSA00265", "CUSA00265" },
    { "CUSA00744", "CUSA00265" },
    { "CUSA00362", "CUSA00362" },
    { "CUSA00363", "CUSA00362" },
    { "CUSA01766", "CUSA01766" },
    { "CUSA01765", "CUSA01766" },
    { "CUSA02703", "CUSA02703" },
    { "CUSA01788", "CUSA02703" },
    { "CUSA02902", "CUSA02902" },
    { "CUSA02533", "CUSA02902" },
    { "CUSA18824", "CUSA18824" },
    { "CUSA24360", "CUSA24360" },
    { "CUSA29008", "CUSA29008" },
    { "CUSA33277", "CUSA33277" },
    { "CUSA40994", "CUSA40994" },
    { "CUSA42311", "CUSA42311" },
    { "CUSA02962", "CUSA02962" },
    { "CUSA01004", "CUSA01004" },
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
        "CUSA05571", "CUSA08809", "CUSA00557", "CUSA09171", "CUSA13801", "CUSA11456",
        "CUSA14209", "CUSA12054", "CUSA16734", "CUSA36842", "CUSA01015", "CUSA33388",
        "CUSA34384", "CUSA15778", "CUSA00083", "CUSA02320", "CUSA03388", "CUSA02299",
        "CUSA09311", "CUSA18534", "CUSA10123", "CUSA08364", "CUSA08344", "CUSA10454",
        "CUSA11100", "CUSA15090", "CUSA18723", "CUSA36375", "CUSA06548", "CUSA10237",
        "CUSA11260", "CUSA32946", "CUSA28561", "CUSA01154", "CUSA04841", "CUSA17722",
        "CUSA32836", "CUSA01113", "CUSA00009", "CUSA00093", "CUSA00265", "CUSA00362",
        "CUSA01766", "CUSA02703", "CUSA02902", "CUSA18824", "CUSA24360", "CUSA29008",
        "CUSA33277", "CUSA40994", "CUSA42311", "CUSA02962", "CUSA01004"
    };
    for (unsigned i = 0; i < sizeof(k_ver) / sizeof(k_ver[0]); i++) {
        if (!strcmp(f, k_ver[i])) return 1;
    }
    return 0;
}
