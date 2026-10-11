# CLAUDE.md — TTS 스피커 (가족 집 안내 방송)

클라우드 Claude Code 세션용 안내서. 이 PC의 메모·시험 파일은 클라우드에 없으니, 여기 적힌 것과 저장소 코드만 믿고 작업할 것.

## 누구를 위한 앱인가
- 가족 휴대폰에서 "밥 먹어라~" 보내면 집에 켜 둔 기기(PC Edge 최적, 안드로이드 Chrome, 또는 PC 트레이 프로그램)가 딩동 + 소리 내어 읽고 크게 보여 줌, [알겠어요 👍] 답장.
- 라이브: https://s95722462-cell.github.io/ttsspeaker/ (main 에 push = GitHub Pages 배포)
- 사용자는 한국인 비개발자. **답은 한국어, 쉬운 말로.** 장황한 화면 글을 싫어함.
- **새 기능·큰 변경은 기획안을 먼저 보여 주고 승인받은 뒤 만들 것.** 작은 수정·버그는 바로 해도 됨.
- `ttsspeaker.txt` = 사용자가 쓴 PC 스피커 주의 메모. **지우지 말 것.**

## 웹앱 구조
- `index.html` 하나 (CSS + IIFE 스크립트), `manifest.webmanifest`, `sw.js`, `icon-192/512.png`. 이 저장소가 원본.
- **웹을 고쳐 push 할 때마다 `sw.js` 의 `CACHE` 번호를 올릴 것** (지금 `ttsspk-v2` → 다음 `ttsspk-v3`). `pc\` 만 고쳤으면 안 올려도 됨.
- 라이브러리: cdnjs qrcodejs 1.0.0. 새 것도 cdnjs 정확한 버전만.
- 저장: localStorage 접두사 `tts_` (`ls` 도우미, 스피커 설정 `R` / `saveR`).

## 주요 동작
- 중계 `RELAY` = 늘 `https://ntfy.sh/`. 시험 때만 `window.TTS_RELAY = "http://127.0.0.1:포트/"` (localhost·file 에서만 허용).
- 스피커마다 주제 `ttsspk-<22자>` (`ensureCh`, 형식 `CH_RE`) = **비밀번호**. 메시지 JSON `{t: msg|spoke|ack|ask|hi, id, from, text, ts, v}`, 최대 200자(`MAXLEN`), id 로 중복 제거, `since=` 마지막 서버 시각으로 다시 연결(1→30초 backoff, keepalive 감시).
- 연결 QR/링크: `#to=<채널>&n=<이름>` (해시라서 서버 로그에 안 남음, `hashchange` 처리). 6자리 코드 연결: 주제 `ttsspk-pair-<코드>` 에서 ECDH P-256 + AES-GCM 으로 채널 교환(10분).
- 목소리: 기본 `DEFAULT_VOICE` = Brian Multilingual (Edge). 보내는 쪽 '내 목소리' 👨/👩/이름 → `v:{g,name}`, 스피커는 '보낸 사람 목소리 따르기'(`vKey` 로 이름 맞춤, 같은 성별 없으면 pitch 로 구분). '이름 읽기' 기본 끔.
- 아이폰 스피커: 누른 순간에만 speak 허용 → `primeSpeech`; 6초 안에 말이 안 시작하면 ▶ 듣기 버튼. 안드로이드는 Chrome 만 웹 음성 목소리가 있음.

## 개인정보·안전
- ntfy.sh 는 공개 무료 서버 → **채널 id·코드를 커밋·로그·문서·이슈에 절대 남기지 말 것.** 시험 로그에도 가리기.
- 화면에 "민감한 내용은 보내지 마세요" 안내 유지. 메시지를 다른 서버로 보내는 기능은 기획·승인 먼저.
- 비밀·개인정보를 저장소에 넣지 말 것.

## PC 프로그램 (`pc\`)
- `tts_speaker_pc.py` (tkinter + pystray 트레이 + comtypes SAPI + cryptography + qrcode + edge-tts), 웹 스피커와 같은 중계 규칙. 설정·기록 `%LOCALAPPDATA%\TTSSpeaker\`.
- 빌드 `pc\build.ps1`: Python 3.13 venv → PyInstaller 6.22.3 **onedir**(`--noupx`, `version_info.txt`, 아이콘), 부트로더를 VS2022 MSVC 로 다시 빌드해 venv 에 복사, 그다음 Inno Setup 6 `pc\TTSSpeaker.iss` 로 설치 파일. 결과물은 사용자 PC의 E: 드라이브 작업 폴더에 만듦.
- **Windows 전용 — 클라우드(리눅스)에서는 빌드·실행 시험 불가.** 클라우드에서는 코드 수정·문법 확인(`python -m py_compile`)까지만 하고, 빌드·실행 확인은 사용자 PC 세션에 넘길 것.
- **exe·zip·build/dist 는 커밋 금지** (`.gitignore` 에 있음).
- 백신 오진 이력: 예전 onefile exe 가 Defender `Trojan:Win32/Bearfoos.A!ml`(실행 중 행동 ML 탐지)로 잡힘. 대책 = onedir, UPX 안 씀, 버전 정보, 다시 빌드한 부트로더, 관리자 권한 안 씀, 시작프로그램 바로가기는 첫 '✓ 완료' 때만 생성. 이 원칙을 되돌리지 말 것 (onefile·UPX·숨은 다운로드·자기 복제 같은 의심 행동 금지).
- 버전을 올리면 `build.ps1` 의 `$Ver`, `version_info.txt`, `TTSSpeaker.iss` 를 함께 고칠 것.
- 시험용 환경 변수: `TTSSPK_RELAY`(127.0.0.1 만), `TTSSPK_HOME`, `TTSSPK_FAKE_AUDIO=1`, `TTSSPK_CTL_PORT`, `TTSSPK_STARTUP_DIR`, `TTSSPK_LINK_BASE`.

## 규칙
- 화면 글은 짧게. 휴대폰 먼저(아이폰 느낌): 누르는 칸 ≥44px, 입력칸 ≥16px, `touch-action: manipulation`, 320~430px 가로 넘침 없음.
- 색은 `:root` 토큰 + `prefers-color-scheme` 다크/라이트.
- 내려받기·공유 파일 이름은 영문·숫자만.

## 시험 (클라우드에서)
- 사용자 PC의 시험 도구(cdp.py, mock_relay.py, e2e.py, live.py, pc_e2e.py)는 저장소에 없음. 필요하면 `tests/` 를 만들어 작게 넣을 것.
- 헤드리스 Chromium + Playwright/CDP, 소리 음소거, `speechSynthesis.speak` 를 init script 로 가로채 읽은 문장을 모으고 실제로 틀지 말 것.
- 로컬 가짜 ntfy 중계(POST 저장, `/sse?since=` 내보내기, 끊김 흉내) + `window.TTS_RELAY`. 브라우저 두 개(스피커·보내는 사람)로 msg → spoke → ack 흐름, QR 링크 연결, 6자리 코드 연결, 재연결·중복 제거 확인. 실제 ntfy.sh 는 가끔만.
- 확인할 것: JS 오류 0, 320/375/390px 넘침, 다크/라이트.

## Git
- 커밋 메시지는 한국어로 무엇이 바뀌었는지 (예: "PC 프로그램 1.1: 백신 오진 줄이기 · 설치 프로그램").
- 시작 전 `git pull --rebase`, 강제 push 금지. main 에 push = 배포.
- push 뒤 라이브 확인: `https://s95722462-cell.github.io/ttsspeaker/sw.js` 의 CACHE 가 새 번호인지, JS 오류 없는지.
