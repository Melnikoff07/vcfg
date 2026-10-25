# Builds the VCFG plugin and the discovery plugin.
#   powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1                 # release flags
#   powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -Target discovery
#   & .\build.ps1 -Defs @('A21_SLAB2=1','A21_POST=1')                            # custom stage (call with &, see note)
# Note: pass several -Defs only with `& .\build.ps1` from PowerShell; `powershell -File` joins arrays into one string.
# Toolchain paths come from config.json (copy config.example.json). Output: build\<name>\<name>.prx (+ .oelf, .elf).
param(
    [ValidateSet('plugin', 'discovery')][string]$Target = 'plugin',
    [string]$Name = '',
    [string[]]$Defs = @()
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$cfgPath = Join-Path $root 'config.json'
if (-not (Test-Path $cfgPath)) { throw "config.json not found: copy config.example.json and set your paths" }
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$tc = $cfg.openorbis_toolchain; $sdk = $cfg.goldhen_sdk; $clang = Join-Path $cfg.llvm_bin 'clang.exe'; $ld = Join-Path $cfg.llvm_bin 'ld.lld.exe'

# Release = every stage verified on hardware so far (see docs/COMPATIBILITY.md).
$release = @('A21_SLAB2=1', 'A21_POST=1', 'A21_MENU=1', 'A21_SCHED=1', 'A22_UNIVERSAL=1')
if ($Target -eq 'plugin') {
    $src = Join-Path $root 'plugin'
    if (-not $Name) { $Name = 'vcfg' }
    if ($Defs.Count -eq 0) { $Defs = $release }
} else {
    $src = Join-Path $root 'discovery'
    if (-not $Name) { $Name = 'vcfg_discovery' }
}
$out = Join-Path $root "build\$Name"
New-Item -ItemType Directory -Force $out | Out-Null
$env:OO_PS4_TOOLCHAIN = $tc
$inc = @('-isysroot', $tc, '-isystem', "$tc\include", "-I$sdk\include", "-I$src\common", "-I$src\source", "-I$root\overlay")
$def = @('-D_BSD_SOURCE=1', '-D__BSD_VISIBLE=1', '-D__PS4__=1', '-DOO=1', '-D__OPENORBIS__=1', '-D__OOPS4__=1', '-D__FINAL__=1', '-D__USE_PRINTF__')
foreach ($d in $Defs) { $def += "-D$d" }
$def += "-DVCFG_VER=$((Get-Content (Join-Path $root 'VERSION') -Raw).Trim())"   # shown in the trace / bug report
$flags = @('--target=x86_64-pc-freebsd12-elf', '-fPIC', '-funwind-tables', "-ffile-prefix-map=$root=.", '-Wno-c99-designator', '-Werror=format')
$objs = @()
$srcs = @(Get-ChildItem "$src\common\*.c") + @(Get-ChildItem "$src\source\*.c")
if ($Target -eq 'plugin') { $srcs += Get-Item (Join-Path $root 'overlay/vov.c') }   # in-game overlay renderer
foreach ($f in $srcs) {
    $o = Join-Path $out "$($f.BaseName).o"
    & $clang @flags @def @inc -c $f.FullName -o $o
    if ($LASTEXITCODE -ne 0) { throw "Compile failed: $($f.Name)" }
    $objs += $o
}
$libs = @('-lSceLibcInternal', '-lSceGnmDriver', '-lSceVideoOut', '-lScePad', '-lGoldHEN_Hook', '-lkernel', '-lSceSysmodule', '-lSceSystemService', '-lSceUserService')
$elf = Join-Path $out "$Name.elf"
& $ld '-m' 'elf_x86_64' '-pie' '-e' '_init' '--script' "$tc\link.x" '--eh-frame-hdr' '-L' "$tc\lib" '-L' $sdk "$sdk\build\crtprx.o" @objs @libs '-o' $elf
if ($LASTEXITCODE -ne 0) { throw 'Link failed' }
Push-Location $out
try {
    & "$tc\bin\windows\create-fself.exe" "-in=$Name.elf" "-out=$Name.oelf" "-lib=$Name.prx" '--paid' '0x3800000000000011'
    if ($LASTEXITCODE -ne 0) { throw 'create-fself failed' }
} finally {
    Pop-Location
}
if ($Target -eq 'plugin') {
    # Shader arrays must be byte-identical to the headers (never text-edit machine arrays).
    & $cfg.python -I (Join-Path $root 'tools\elf_arrays.py') $elf (Join-Path $src 'source')
    if ($LASTEXITCODE -ne 0) { throw 'Shader array check failed' }
    & $cfg.python -I (Join-Path $root 'tools\tests\test_lowmem_valve.py')
    if ($LASTEXITCODE -ne 0) { throw 'Low-memory regression test failed' }
    & $cfg.python -I (Join-Path $root 'tools\tests\test_real_traces.py')
    if ($LASTEXITCODE -ne 0) { throw 'Real hardware trace replay test failed' }
}
Write-Host "flags: $($Defs -join ' ')"
Get-FileHash -Algorithm SHA256 "$out\$Name.prx" | Format-List
