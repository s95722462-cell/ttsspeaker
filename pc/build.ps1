# TTS 스피커 PC 프로그램 만들기 (exe 하나)
# 쓰는 법: PowerShell 에서  .\pc\build.ps1
# 가상환경·만든 파일은 E: 에 둠 (C: 자리 아끼기). exe 는 git 에 올리지 않음.
$ErrorActionPreference = "Stop"
$Root = "E:\claude_보관\ttsspk_pc"
$Venv = "$Root\venv"
$Src  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Src
$env:PIP_CACHE_DIR = "$Root\pipcache"

if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    py -3.13 -m venv $Venv
    & "$Venv\Scripts\python.exe" -m pip install --upgrade pip
}
& "$Venv\Scripts\python.exe" -m pip install -q -r "$Src\requirements.txt" pyinstaller

# SAPI(말하기) 형식 정보를 미리 만들어 exe 에 넣음
& "$Venv\Scripts\python.exe" -c "import comtypes.client as c; c.GetModule('sapi.dll'); print('SpeechLib ok')"

& "$Venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed `
    --name TTSSpeaker `
    --icon "$Src\icon.ico" `
    --add-data "$Src\icon.ico;." `
    --add-data "$Repo\icon-192.png;." `
    --hidden-import pystray._win32 `
    --hidden-import comtypes.gen.SpeechLib `
    --collect-submodules comtypes.gen `
    --collect-data certifi `
    --exclude-module numpy `
    --distpath "$Root\dist" --workpath "$Root\build" --specpath "$Root" `
    "$Src\tts_speaker_pc.py"

Copy-Item "$Root\dist\TTSSpeaker.exe" "$Root\dist\TTS스피커.exe" -Force
Get-Item "$Root\dist\*.exe" | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
