# Builds the VCFG launcher package (settings app + plugin installer): build\launcher\VCFG_v<Version>.pkg
# Needs both plugins built first: ..\build.ps1 (new engine) and ../classic/build.sh (classic engine, used for
# CUSA08519, CUSA00411 and CUSA00207). System modules come from the OpenOrbis samples.
param([string]$Version = '2.00')
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$cfg = Get-Content (Join-Path $repo 'config.json') -Raw | ConvertFrom-Json
$tc = $cfg.openorbis_toolchain; $llvm = $cfg.llvm_bin
$env:OO_PS4_TOOLCHAIN = $tc; $env:DOTNET_ROLL_FORWARD = 'Major'
$pkgTool = Join-Path $tc 'bin\windows\PkgTool.Core.exe'
$out = Join-Path $repo 'build\launcher'; $stage = Join-Path $out 'pkgroot'
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\sce_sys\about", "$stage\sce_module", "$stage\plugins", "$stage\assets\fonts" | Out-Null
function Run($exe, [string[]]$a) { & $exe @a; if ($LASTEXITCODE -ne 0) { throw "Failed: $exe ($LASTEXITCODE)" } }

# 1. App executable (ui.c / app.c are the same files the PC preview renders).
$flags = @('--target=x86_64-pc-freebsd12-elf', '-fPIC', '-funwind-tables', '-O2', '-Wall', '-Wno-unused-function', '-Werror=format',
    '-D_BSD_SOURCE=1', '-D__BSD_VISIBLE=1', '-D__PS4__=1', '-DOO=1', '-isysroot', $tc, '-isystem', "$tc\include", "-I$($cfg.goldhen_sdk)\include")
$objs = @()
foreach ($f in 'main', 'ui', 'app') {
    Run "$llvm\clang.exe" ($flags + @('-c', "$PSScriptRoot\src\$f.c", '-o', "$out\$f.o")); $objs += "$out\$f.o"
}
Run "$llvm\ld.lld.exe" (@('-m', 'elf_x86_64', '-pie', '--script', "$tc\link.x", '--eh-frame-hdr', "$tc\lib\crt1.o") + $objs +
    @('-L', "$tc\lib", '-L', $cfg.goldhen_sdk, '-lGoldHEN_Hook', '-lc', '-lkernel', '-lSceVideoOut', '-lScePad', '-lSceUserService', '-lSceSysmodule', '-lSceSystemService',
      '-o', "$out\launcher.elf"))
Run "$tc\bin\windows\create-fself.exe" @("-in=$out\launcher.elf", "-out=$out\launcher.oelf", "--eboot=$stage\eboot.bin", '--paid', '0x3800000000000011')

# 2. Payload: plugins and fonts.
$prx = Join-Path $repo 'build\vcfg\vcfg.prx'
if (-not (Test-Path $prx)) { throw "Build the plugin first: $prx not found" }
$classic = Join-Path $repo 'build\classic\vcfg_classic.prx'
if (-not (Test-Path $classic)) { throw "Build the classic engine first (classic/build.sh): $classic not found" }
# Each release that changes a plugin uses a new file name: a running game keeps the old file mapped.
Copy-Item $prx "$stage\plugins\vcfg4.prx"
Copy-Item $classic "$stage\plugins\vcfg_classic5.prx"
Copy-Item "$PSScriptRoot\assets\fonts\*" "$stage\assets\fonts\"

# 3. System files from the OpenOrbis samples; our own icon.
$sample = Join-Path $tc 'samples\hello_world'
if (-not (Test-Path "$sample\sce_module\libc.prx")) { $sample = Join-Path $tc 'samples\audio-wav' }
Copy-Item "$sample\sce_module\libc.prx", "$sample\sce_module\libSceFios2.prx" "$stage\sce_module\"
Copy-Item "$sample\sce_sys\about\right.sprx" "$stage\sce_sys\about\"
Copy-Item "$PSScriptRoot\assets\icon0.png" "$stage\sce_sys\icon0.png"

# 4. param.sfo (same title id as the 1.x installer, so this installs as an update).
$contentId = 'IV0000-VCFG00001_00-VCFGINSTALLER000'
$sfo = "$stage\sce_sys\param.sfo"
Run $pkgTool @('sfo_new', $sfo)
$entries = @(
    @('APP_TYPE', 'Integer', '4', '1'), @('APP_VER', 'Utf8', '8', $Version), @('ATTRIBUTE', 'Integer', '4', '0'),
    @('CATEGORY', 'Utf8', '4', 'gd'), @('CONTENT_ID', 'Utf8', '48', $contentId), @('DOWNLOAD_DATA_SIZE', 'Integer', '4', '0'),
    @('SYSTEM_VER', 'Integer', '4', '0'), @('TITLE', 'Utf8', '128', 'VCFG'),
    @('TITLE_ID', 'Utf8', '12', 'VCFG00001'), @('VERSION', 'Utf8', '8', $Version))
foreach ($e in $entries) { Run $pkgTool @('sfo_setentry', $sfo, $e[0], '--type', $e[1], '--maxsize', $e[2], '--value', $e[3]) }

# 5. gp4 project and package.
$files = @('eboot.bin', 'sce_sys/param.sfo', 'sce_sys/icon0.png', 'sce_sys/about/right.sprx', 'sce_module/libc.prx', 'sce_module/libSceFios2.prx',
    'plugins/vcfg4.prx', 'plugins/vcfg_classic5.prx')
$files += Get-ChildItem "$stage\assets\fonts" | ForEach-Object { "assets/fonts/$($_.Name)" }
$fileXml = ($files | ForEach-Object { "  <file targ_path=""$_"" orig_path=""$_"" />" }) -join "`n"
$gp4 = @"
<?xml version="1.0" encoding="utf-8"?>
<psproject fmt="gp4" version="1000">
 <volume><volume_type>pkg_ps4_app</volume_type><volume_id>PS4VOLUME</volume_id><volume_ts>2026-10-19 00:00:00</volume_ts>
 <package content_id="$contentId" passcode="00000000000000000000000000000000" storage_type="digital50" app_type="full" />
 <chunk_info chunk_count="1" scenario_count="1"><chunks><chunk id="0" layer_no="0" label="Chunk #0" /></chunks><scenarios default_id="0"><scenario id="0" type="sp" initial_chunk_count="1" label="Scenario #0">0</scenario></scenarios></chunk_info></volume>
 <files img_no="0">
$fileXml
 </files>
 <rootdir><dir targ_name="sce_sys"><dir targ_name="about" /></dir><dir targ_name="sce_module" /><dir targ_name="plugins" /><dir targ_name="assets"><dir targ_name="fonts" /></dir></rootdir>
</psproject>
"@
[IO.File]::WriteAllText("$stage\pkg.gp4", $gp4, [Text.UTF8Encoding]::new($false))
Push-Location $stage
try { Run $pkgTool @('pkg_build', 'pkg.gp4', $out) } finally { Pop-Location }
$pkg = Join-Path $out "$contentId.pkg"
Run $pkgTool @('pkg_validate', $pkg)
$final = Join-Path $out "VCFG_v$Version.pkg"
Copy-Item $pkg $final -Force
Get-Item $final | Select-Object FullName, Length
Get-FileHash -Algorithm SHA256 $final | Format-List
