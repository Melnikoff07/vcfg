# Builds the VCFG installer package: build\installer\<CONTENT_ID>.pkg
# Needs the plugin built first (..\build.ps1) and config.json. The system modules (libc.prx, libSceFios2.prx,
# right.sprx) are taken from the OpenOrbis toolchain samples at build time and are not stored in this repository.
param([string]$Version = '1.00')
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$cfg = Get-Content (Join-Path $repo 'config.json') -Raw | ConvertFrom-Json
$tc = $cfg.openorbis_toolchain; $llvm = $cfg.llvm_bin
$env:OO_PS4_TOOLCHAIN = $tc; $env:DOTNET_ROLL_FORWARD = 'Major'
$pkgTool = Join-Path $tc 'bin\windows\PkgTool.Core.exe'
$out = Join-Path $repo 'build\installer'; $stage = Join-Path $out 'pkgroot'
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$stage\sce_sys\about", "$stage\sce_module", "$stage\plugins" | Out-Null
function Run($exe, [string[]]$a) { & $exe @a; if ($LASTEXITCODE -ne 0) { throw "Failed: $exe ($LASTEXITCODE)" } }

# 1. App executable.
$flags = @('--target=x86_64-pc-freebsd12-elf', '-fPIC', '-funwind-tables', '-O2', '-Wall', '-Werror=format',
    '-D_BSD_SOURCE=1', '-D__BSD_VISIBLE=1', '-D__PS4__=1', '-DOO=1', '-isysroot', $tc, '-isystem', "$tc\include")
Run "$llvm\clang.exe" ($flags + @('-c', "$PSScriptRoot\src\main.c", '-o', "$out\main.o"))
Run "$llvm\ld.lld.exe" @('-m', 'elf_x86_64', '-pie', '--script', "$tc\link.x", '--eh-frame-hdr', "$tc\lib\crt1.o", "$out\main.o",
    '-L', "$tc\lib", '-lc', '-lkernel', '-lSceSysmodule', '-lSceSystemService', '-o', "$out\installer.elf")
Run "$tc\bin\windows\create-fself.exe" @("-in=$out\installer.elf", "-out=$out\installer.oelf", "--eboot=$stage\eboot.bin", '--paid', '0x3800000000000011')

# 2. Payload: the plugin built by ..\build.ps1.
$prx = Join-Path $repo 'build\vcfg\vcfg.prx'
if (-not (Test-Path $prx)) { throw "Build the plugin first: $prx not found" }
Copy-Item $prx "$stage\plugins\vcfg.prx"

# 3. System files from the OpenOrbis samples; our own icon.
$sample = Join-Path $tc 'samples\hello_world'
if (-not (Test-Path "$sample\sce_module\libc.prx")) { $sample = Join-Path $tc 'samples\audio-wav' }
Copy-Item "$sample\sce_module\libc.prx", "$sample\sce_module\libSceFios2.prx" "$stage\sce_module\"
Copy-Item "$sample\sce_sys\about\right.sprx" "$stage\sce_sys\about\"
Copy-Item "$PSScriptRoot\assets\icon0.png" "$stage\sce_sys\icon0.png"

# 4. param.sfo
$contentId = 'IV0000-VCFG00001_00-VCFGINSTALLER000'
$sfo = "$stage\sce_sys\param.sfo"
Run $pkgTool @('sfo_new', $sfo)
$entries = @(
    @('APP_TYPE', 'Integer', '4', '1'), @('APP_VER', 'Utf8', '8', $Version), @('ATTRIBUTE', 'Integer', '4', '0'),
    @('CATEGORY', 'Utf8', '4', 'gd'), @('CONTENT_ID', 'Utf8', '48', $contentId), @('DOWNLOAD_DATA_SIZE', 'Integer', '4', '0'),
    @('SYSTEM_VER', 'Integer', '4', '0'), @('TITLE', 'Utf8', '128', 'VCFG Installer'),
    @('TITLE_ID', 'Utf8', '12', 'VCFG00001'), @('VERSION', 'Utf8', '8', $Version))
foreach ($e in $entries) { Run $pkgTool @('sfo_setentry', $sfo, $e[0], '--type', $e[1], '--maxsize', $e[2], '--value', $e[3]) }

# 5. gp4 project and package.
$gp4 = @"
<?xml version="1.0" encoding="utf-8"?>
<psproject fmt="gp4" version="1000">
 <volume><volume_type>pkg_ps4_app</volume_type><volume_id>PS4VOLUME</volume_id><volume_ts>2026-10-19 00:00:00</volume_ts>
 <package content_id="$contentId" passcode="00000000000000000000000000000000" storage_type="digital50" app_type="full" />
 <chunk_info chunk_count="1" scenario_count="1"><chunks><chunk id="0" layer_no="0" label="Chunk #0" /></chunks><scenarios default_id="0"><scenario id="0" type="sp" initial_chunk_count="1" label="Scenario #0">0</scenario></scenarios></chunk_info></volume>
 <files img_no="0">
  <file targ_path="eboot.bin" orig_path="eboot.bin" />
  <file targ_path="sce_sys/param.sfo" orig_path="sce_sys/param.sfo" />
  <file targ_path="sce_sys/icon0.png" orig_path="sce_sys/icon0.png" />
  <file targ_path="sce_sys/about/right.sprx" orig_path="sce_sys/about/right.sprx" />
  <file targ_path="sce_module/libc.prx" orig_path="sce_module/libc.prx" />
  <file targ_path="sce_module/libSceFios2.prx" orig_path="sce_module/libSceFios2.prx" />
  <file targ_path="plugins/vcfg.prx" orig_path="plugins/vcfg.prx" />
 </files>
 <rootdir><dir targ_name="sce_sys"><dir targ_name="about" /></dir><dir targ_name="sce_module" /><dir targ_name="plugins" /></rootdir>
</psproject>
"@
[IO.File]::WriteAllText("$stage\pkg.gp4", $gp4, [Text.UTF8Encoding]::new($false))
Push-Location $stage
try { Run $pkgTool @('pkg_build', 'pkg.gp4', $out) } finally { Pop-Location }
$pkg = Join-Path $out "$contentId.pkg"
Run $pkgTool @('pkg_validate', $pkg)
$final = Join-Path $out "VCFG_Installer_v$Version.pkg"
Copy-Item $pkg $final -Force
Get-Item $final | Select-Object FullName, Length
Get-FileHash -Algorithm SHA256 $final | Format-List
