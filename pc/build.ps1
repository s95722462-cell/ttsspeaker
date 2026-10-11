# TTS 스피커 PC 프로그램 만들기 (폴더 형태 + 설치 프로그램)
# 쓰는 법: PowerShell 에서  .\pc\build.ps1
# 가상환경·만든 파일은 E: 에 둠 (C: 자리 아끼기). exe·zip 은 git 에 올리지 않음.
#
# 백신 오진(ML 탐지)을 줄이려고:
#  - onefile(실행 때 임시 폴더에 풀기) 대신 onedir(폴더)
#  - UPX 압축 안 함, 버전 정보·아이콘 넣음, 관리자 권한 안 씀(asInvoker, PyInstaller 기본)
#  - PyInstaller 부트로더를 이 PC에서 MSVC 로 다시 빌드해 venv 에 넣어 둠 (아래 Rebuild-Bootloader)
$ErrorActionPreference = "Stop"
$Root = "E:\claude_보관\ttsspk_pc"
$Venv = "$Root\venv"
$Src  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Src
$Ver  = "1.1"
$env:PIP_CACHE_DIR = "$Root\pipcache"

if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    py -3.13 -m venv $Venv
    & "$Venv\Scripts\python.exe" -m pip install --upgrade pip
}
& "$Venv\Scripts\python.exe" -m pip install -q -r "$Src\requirements.txt" pyinstaller==6.22.3

function Rebuild-Bootloader {
    # PyInstaller 소스를 받아 부트로더(run*.exe)를 MSVC 로 빌드 → venv 의 PyInstaller 에 복사
    $pyiSrc = "$Root\pyi_src"; New-Item -ItemType Directory -Force $pyiSrc | Out-Null
    & "$Venv\Scripts\python.exe" -m pip download --no-deps --no-binary :all: pyinstaller==6.22.3 -d $pyiSrc
    tar xzf "$pyiSrc\pyinstaller-6.22.3.tar.gz" -C $pyiSrc
    $vs = & "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -latest -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    Push-Location "$pyiSrc\pyinstaller-6.22.3\bootloader"
    cmd /c "`"$vs\VC\Auxiliary\Build\vcvars64.bat`" >nul && `"$Venv\Scripts\python.exe`" ./waf all --target-arch=64bit"
    Pop-Location
    Copy-Item "$pyiSrc\pyinstaller-6.22.3\PyInstaller\bootloader\Windows-64bit-intel\*.exe" "$Venv\Lib\site-packages\PyInstaller\bootloader\Windows-64bit-intel\" -Force
}
$bl = Get-Item "$Venv\Lib\site-packages\PyInstaller\bootloader\Windows-64bit-intel\runw.exe"
if ($bl.LastWriteTime.Year -lt 2021) { Rebuild-Bootloader }      # 배포판 부트로더(날짜 2020-02-02)면 다시 빌드

# SAPI(말하기) 형식 정보를 미리 만들어 넣음
& "$Venv\Scripts\python.exe" -c "import comtypes.client as c; c.GetModule('sapi.dll'); print('SpeechLib ok')"

Remove-Item "$Root\dist\TTS스피커" -Recurse -Force -ErrorAction SilentlyContinue
& "$Venv\Scripts\pyinstaller.exe" --noconfirm --clean --onedir --windowed --noupx `
    --name "TTS스피커" `
    --icon "$Src\icon.ico" `
    --version-file "$Src\version_info.txt" `
    --add-data "$Src\icon.ico;." `
    --add-data "$Repo\icon-192.png;." `
    --hidden-import pystray._win32 `
    --hidden-import comtypes.gen.SpeechLib `
    --collect-submodules comtypes.gen `
    --collect-data certifi `
    --exclude-module numpy `
    --distpath "$Root\dist" --workpath "$Root\build" --specpath "$Root" `
    "$Src\tts_speaker_pc.py"

# USB 용 zip
$zip = "$Root\TTS스피커_v$Ver.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$Root\dist\TTS스피커" -DestinationPath $zip

# 설치 프로그램 (Inno Setup 6)
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc /Q "/DSrcDir=$Root\dist\TTS스피커" "/DOutDir=$Root" "/DIconFile=$Src\icon.ico" "$Src\TTSSpeaker.iss"
    if ($LASTEXITCODE) { throw "ISCC 실패 $LASTEXITCODE" }
    Copy-Item "$Root\TTS스피커_설치_v$Ver.exe" "$Root\TTSSpeaker_Setup_v$Ver.exe" -Force
} else { Write-Warning "Inno Setup 6 이 없어서 설치 프로그램은 건너뜀" }

Get-Item "$Root\dist\TTS스피커\TTS스피커.exe", $zip, "$Root\TTS*_v$Ver.exe" -ErrorAction SilentlyContinue | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
