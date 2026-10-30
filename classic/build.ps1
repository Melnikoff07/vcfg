param([ValidateRange(0,4)][int]$Test = 0, [switch]$Bench)
$ErrorActionPreference = 'Stop'
if ($Bench -and $Test -ne 0) { throw 'Bench and two-buffer experiments are separate builds' }
$root = Split-Path $PSScriptRoot -Parent
$cfg = Get-Content (Join-Path $root 'config.json') -Raw | ConvertFrom-Json
$tc = $cfg.openorbis_toolchain
$sdk = $cfg.goldhen_sdk
$ll = $cfg.llvm_bin
$ver = (Get-Content (Join-Path $root 'VERSION') -First 1) -replace '[^0-9.]', ''
$folder = if ($Bench) { 'classic-bench' } elseif ($Test -eq 0) { 'classic' } else { "classic-test$Test" }
$out = Join-Path $root "build/$folder"
New-Item -ItemType Directory -Path $out -Force | Out-Null
$env:OO_PS4_TOOLCHAIN = $tc
$env:DOTNET_ROLL_FORWARD = 'Major'
function Run([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe exited with $LASTEXITCODE" }
}
Run $cfg.python @('-I', "$PSScriptRoot/gen_shaders.py", $cfg.llc_gfx700, "$PSScriptRoot/shaders", "$PSScriptRoot/source/shaders.h")
if ($Bench) {
    Run $cfg.python @('-I', "$PSScriptRoot/gen_shaders.py", $cfg.llc_gfx700, "$PSScriptRoot/bench/shaders", "$out/bench_shaders.h")
}
[IO.File]::WriteAllText("$PSScriptRoot/source/git_ver.h", "#define GIT_COMMIT `"vcfg`"`n#define GIT_VER `"vcfg`"`n#define GIT_NUM 0`n#define BUILD_DATE `"vcfg`"`n")
if ($Bench) { Run $cfg.python @('-I', "$PSScriptRoot/bench/build_id.py", $out) }
# Deliberately no optimization: optimized classic builds regressed CUSA00207.
$flags = @('--target=x86_64-pc-freebsd12-elf', '-fPIC', '-funwind-tables', "-ffile-prefix-map=$root=.", '-isysroot', $tc,
    '-isystem', "$tc/include", "-I$sdk/include", "-I$PSScriptRoot/source", "-I$root/overlay",
    '-Wno-c99-designator', '-D_BSD_SOURCE=1', '-D__BSD_VISIBLE=1', '-D__PS4__=1', '-DOO=1',
    '-D__OPENORBIS__=1', '-D__OOPS4__=1', '-D__FINAL__=1', '-D__USE_PRINTF__', "-DVCFG_VER=$ver", "-DVCFG_2BUF_TEST=$Test",
    "-DVCFG_BENCH=$([int][bool]$Bench)", "-I$out")
$objects = @()
$sources = @(Get-ChildItem "$PSScriptRoot/source/*.c") + @(Get-Item "$root/overlay/vov.c")
foreach ($src in $sources) {
    $obj = "$out/$($src.BaseName).o"
    Run "$ll/clang.exe" ($flags + @('-c', $src.FullName, '-o', $obj))
    $objects += $obj
}
Run "$ll/ld.lld.exe" (@("$sdk/build/crtprx.o") + $objects + @('-o', "$out/vcfg_classic.elf", '-m', 'elf_x86_64',
    '-pie', '-e', '_init', '--script', "$tc/link.x", '--eh-frame-hdr', "-L$tc/lib", "-L$sdk",
    '-lSceLibcInternal', '-lSceGnmDriver', '-lSceVideoOut', '-lScePad', '-lGoldHEN_Hook', '-lkernel',
    '-lSceSysmodule', '-lSceSystemService', '-lSceUserService'))
Push-Location $out
try {
    Run "$tc/bin/windows/create-fself.exe" @('-in=vcfg_classic.elf', '-out=vcfg_classic.oelf', '-lib=vcfg_classic.prx', '--paid', '0x3800000000000011')
} finally {
    Pop-Location
}
Get-Item "$out/vcfg_classic.prx" | Select-Object FullName, Length
Write-Host "SHA256 $((Get-FileHash "$out/vcfg_classic.prx" -Algorithm SHA256).Hash)"
if (-not $Bench -and $Test -eq 0) {
    Run $cfg.python @('-I', "$root/tools/tests/test_lowmem_valve.py")
    Run $cfg.python @('-I', "$root/tools/tests/test_real_traces.py")
    Run $cfg.python @('-I', "$root/tools/tests/test_vout_gpu_flip.py")
}
