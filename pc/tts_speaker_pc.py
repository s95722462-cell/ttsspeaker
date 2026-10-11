# -*- coding: utf-8 -*-
"""TTS 스피커 — PC 받는 프로그램 (트레이에서 계속 듣기)

웹 스피커(index.html)와 똑같은 약속으로 움직여요.
- 중계: ntfy.sh 주제 ttsspk-<22자>, JSON {t: msg|spoke|ack|ask|hi, id, from, text, ts, v?}
- 연결: QR/링크 #to=<채널>&n=<이름>, 6자리 코드 = ttsspk-pair-<코드> 에서 ECDH P-256 + AES-GCM 교환 (10분)
- 다시 연결: since=마지막 서버 시각, 1→30초 간격, 오래 조용하면 다시 연결
- 10분 넘게 지난 메시지는 '놓침'으로 기록만
"""
import sys, os, json, time, threading, queue, random, re, math, struct, wave, base64, hashlib, logging, logging.handlers
import ctypes, ctypes.wintypes, urllib.request, urllib.error, urllib.parse, secrets, tempfile, argparse
from datetime import datetime

APP_NAME = "TTS 스피커"
VERSION = "1.0"
PUBLIC_URL = "https://s95722462-cell.github.io/ttsspeaker/"
CH_RE = re.compile(r"^ttsspk-[a-z0-9]{16,40}$")
MAXLEN = 200
FROZEN = getattr(sys, "frozen", False)
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

# ---------------- 시험용 덮어쓰기 (평소에는 안 씀) ----------------
ap = argparse.ArgumentParser(add_help=False)
ap.add_argument("--tray", action="store_true")          # 자동 실행: 설정 창 없이 트레이로
ap.add_argument("--relay"); ap.add_argument("--home"); ap.add_argument("--link-base")
ARGS, _ = ap.parse_known_args()
def _relay():
    r = ARGS.relay or os.environ.get("TTSSPK_RELAY") or ""
    if re.fullmatch(r"http://(localhost|127\.0\.0\.1)(:\d+)?/", r): return r     # 시험: 이 PC 안의 가짜 중계만 허용
    return "https://ntfy.sh/"
RELAY = _relay()
LINK_BASE = ARGS.link_base or os.environ.get("TTSSPK_LINK_BASE") or PUBLIC_URL
HOME = ARGS.home or os.environ.get("TTSSPK_HOME") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TTSSpeaker")
FAKE_AUDIO = os.environ.get("TTSSPK_FAKE_AUDIO") == "1"      # 시험: 소리 대신 기록
CTL_PORT = int(os.environ.get("TTSSPK_CTL_PORT") or 0)       # 시험: 조종용 작은 서버
STARTUP_DIR = os.environ.get("TTSSPK_STARTUP_DIR") or os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
LNK_NAME = "TTS 스피커.lnk"
os.makedirs(HOME, exist_ok=True)

log = logging.getLogger("tts")
log.setLevel(logging.INFO)
_h = logging.handlers.RotatingFileHandler(os.path.join(HOME, "log.txt"), maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)

now = lambda: int(time.time() * 1000)
now_sec = lambda: int(time.time())
def rid(n):
    A = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(A[b % 32] for b in secrets.token_bytes(n))
def hm(ts_ms):
    d = datetime.fromtimestamp(ts_ms / 1000)
    return d.strftime("%H:%M") if d.date() == datetime.now().date() else f"{d.month}/{d.day} {d:%H:%M}"
def enc_uri(s): return urllib.parse.quote(s, safe="-_.!~*'()")

# ---------------- 설정 · 기록 ----------------
DEFAULTS = {"ch": "", "name": "", "setup": False, "voice": "", "rate": 0, "pitch": 0, "volume": 100, "rep": 1,
            "readName": False, "vFollow": True, "chime": True, "qOn": False, "qFrom": "23:00", "qTo": "07:00",
            "autostart": True, "keepAwake": True, "out": "", "online": False, "edgeVoice": "ko-KR-SunHiNeural",
            "lastTs": 0, "senders": [], "pauseUntil": 0}
class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.cfg = dict(DEFAULTS); self.cfg.update(self._load("config.json", {}))
        st = self._load("state.json", {})
        self.hist = st.get("hist", [])[:40]; self.seen = st.get("seen", [])[-300:]
        if not CH_RE.match(self.cfg["ch"] or ""): self.cfg["ch"] = "ttsspk-" + rid(22); self.save()
    def _load(self, n, d):
        try:
            with open(os.path.join(HOME, n), encoding="utf-8") as f: v = json.load(f)
            return v if isinstance(v, dict) else d
        except Exception: return d
    def _write(self, n, obj):
        p = os.path.join(HOME, n); tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    def save(self):
        with self.lock:
            try: self._write("config.json", self.cfg)
            except Exception as e: log.warning("config save: %s", e)
    def save_state(self):
        with self.lock:
            try: self._write("state.json", {"hist": self.hist[:40], "seen": self.seen[-300:]})
            except Exception as e: log.warning("state save: %s", e)
    def __getitem__(self, k): return self.cfg[k]
    def set(self, k, v):
        with self.lock: self.cfg[k] = v; self.save()

# ---------------- 중계 (ntfy.sh) ----------------
UA = f"TTSSpeakerPC/{VERSION}"
def post(topic, obj, tries=5):
    """실패하면 1·2·4·8초 뒤 다시 (429·5xx·인터넷 끊김)"""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    for k in range(tries):
        try:
            req = urllib.request.Request(RELAY + topic, data=body, method="POST", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                if 200 <= r.status < 300: return True
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500: log.warning("post %s → %s", topic[:12], e.code); return False
            log.info("post %s → %s, 다시", topic[:12], e.code)
        except Exception as e:
            log.info("post err %s", e)
        time.sleep(2 ** k + random.random() * 0.3)
    return False
def post_bg(topic, obj, done=None):
    def run():
        ok = post(topic, obj)
        if done: done(ok)
    threading.Thread(target=run, daemon=True).start()

class Sub:
    """SSE 듣기. 끊기면 since=마지막 서버 시각부터 다시 (1·2·4…30초). keepalive 가 90초 없으면 다시."""
    def __init__(self, topic, since, on_msg, on_state=None):
        self.topic, self.last, self.on_msg, self.on_state = topic, since, on_msg, on_state
        self.closed = False; self.state = ""; self.wake = threading.Event(); self.resp = None; self.seen = []
        threading.Thread(target=self.run, daemon=True).start()
    def _set(self, s):
        if s != self.state:
            self.state = s
            if self.on_state:
                try: self.on_state(s)
                except Exception: log.exception("on_state")
    def _kill_conn(self):
        r = self.resp
        if not r: return
        try:
            import socket
            r.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except Exception: pass
        try: r.close()
        except Exception: pass
    def retry(self):
        if self.closed: return
        self._kill_conn(); self.wake.set()
    def close(self):
        self.closed = True; self._kill_conn(); self.wake.set()
    def _on_data(self, data):
        try: d = json.loads(data)
        except Exception: return
        if not isinstance(d, dict): return
        if d.get("event") not in (None, "message"): return
        nid = d.get("id")
        if nid in self.seen: return
        self.seen.append(nid); self.seen = self.seen[-500:]
        t = d.get("time")
        if isinstance(t, int) and t > self.last: self.last = t
        try: m = json.loads(d.get("message") or "")
        except Exception: return
        if isinstance(m, dict):
            try: self.on_msg(m, d)
            except Exception: log.exception("on_msg")
    def run(self):
        tries = 0
        while not self.closed:
            self._set("retry" if tries else "connecting")
            wait429 = False
            try:
                req = urllib.request.Request(f"{RELAY}{self.topic}/sse?since={self.last}", headers={"User-Agent": UA, "Accept": "text/event-stream"})
                self.resp = urllib.request.urlopen(req, timeout=90)
                if self.closed: break
                tries = 0; self._set("ok")
                buf = []
                for raw in self.resp:
                    if self.closed: break
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"): buf.append(line[5:].lstrip(" "))
                    elif line == "":
                        if buf: self._on_data("\n".join(buf)); buf = []
            except urllib.error.HTTPError as e:
                wait429 = e.code == 429; log.info("sse %s → HTTP %s", self.topic[:14], e.code)
            except Exception as e:
                if not self.closed: log.info("sse %s 끊김: %r", self.topic[:14], e, exc_info=isinstance(e, TypeError))
            finally:
                try: self.resp and self.resp.close()
                except Exception: pass
                self.resp = None
            if self.closed: break
            tries += 1; self._set("retry")
            delay = 60 if wait429 else min(30, 2 ** min(tries - 1, 5)) + random.random() * 0.5
            self.wake.clear(); self.wake.wait(delay)

# ---------------- 6자리 코드 연결 (암호화 교환) ----------------
def make_welcome(hello_pub_b64, info):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives import serialization
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(hello_pub_b64))
    priv = ec.generate_private_key(ec.SECP256R1())
    key = priv.exchange(ec.ECDH(), peer)                 # WebCrypto deriveKey(AES-GCM 256) = 공유 비밀 x 좌표 그대로
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(info, ensure_ascii=False).encode("utf-8"), None)
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"pub": base64.b64encode(pub).decode(), "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}

# ---------------- 목소리 이름·성별 (웹과 같은 표) ----------------
VALIAS = {"인준": "injoon", "선히": "sunhi", "선희": "sunhi", "해미": "heami", "현수": "hyunsu", "유나": "yuna", "지민": "jimin", "서현": "seohyeon", "순복": "soonbok", "유진": "yujin", "봉진": "bongjin", "국민": "gookmin"}
VKO = {"injoon": "인준", "sunhi": "선희", "heami": "해미", "hyunsu": "현수", "yuna": "유나", "jimin": "지민", "seohyeon": "서현", "soonbok": "순복", "yujin": "유진", "bongjin": "봉진", "gookmin": "국민"}
VGEN = {k: "m" for k in "injoon hyunsu bongjin gookmin brian andrew christopher guy eric roger steffan davis tony jason ryan william remy florian giuseppe".split()}
VGEN.update({k: "f" for k in "sunhi heami yuna jimin seohyeon soonbok yujin ava emma jenny aria michelle seraphina vivienne sara nancy jane".split()})
def vcanon(k): k = str(k or "").strip(); return VALIAS.get(k, k.lower())
def vkey(desc):
    n = re.sub(r"Microsoft |Google |Online|\(Natural\)|Multilingual|Desktop| - .*$|\(.*?\)", " ", desc).strip().split()
    c = vcanon(n[0] if n else "")
    if c in VGEN: return c
    for t in re.split(r"[^A-Za-z가-힣]+", desc):
        if vcanon(t) in VGEN: return vcanon(t)
    return c
def vlabel(k): return VKO.get(k) or (k[:1].upper() + k[1:] if k else "")
EDGE_VOICES = [{"id": "ko-KR-SunHiNeural", "key": "sunhi", "g": "f", "label": "선희 (여)"},
               {"id": "ko-KR-InJoonNeural", "key": "injoon", "g": "m", "label": "인준 (남)"},
               {"id": "ko-KR-HyunsuMultilingualNeural", "key": "hyunsu", "g": "m", "label": "현수 (남)"}]
def josa(w):
    c = ord(w[-1]) if w else 0
    if c < 0xAC00 or c > 0xD7A3: return None
    return "이" if (c - 0xAC00) % 28 else "가"

# ---------------- 딩동 (웹과 같은 종소리를 WAV 로) ----------------
def make_chime(path, sr=22050):
    dur = 2.4; n = int(sr * dur); buf = [0.0] * n
    def bell(f, t0, d=1.5, a=0.34):
        for m, k in ((1, 1), (2, 0.16), (3.01, 0.07), (4.2, 0.03)):
            dd = d / math.sqrt(m); s0 = int(t0 * sr)
            for i in range(int((dd + 0.05) * sr)):
                j = s0 + i
                if j >= n: break
                t = i / sr
                env = min(1.0, t / 0.006) * math.exp(-t * 9.2 / dd)
                buf[j] += a * k * env * math.sin(2 * math.pi * f * m * t)
    bell(880, 0.03); bell(698.46, 0.53, 1.8)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(x * 0.9 * 32767)))) for x in buf))

# ---------------- 말하기 (SAPI · 인터넷 목소리) ----------------
class Speaker:
    """말하기 전용 스레드. 일은 jobs 큐로 받음."""
    def __init__(self, app):
        self.app = app; self.jobs = queue.Queue(); self.stop = threading.Event()
        self.voices = []          # 한국어 SAPI 목소리 [{id, desc, key, g, label}]
        self.outputs = []         # [{id, desc}]
        self.ready = threading.Event(); self.captured = []
        self.chime_path = os.path.join(HOME, "chime.wav")
        threading.Thread(target=self._run, daemon=True).start()
    # --- 스레드 ---
    def _run(self):
        self.v = None; self.tokens = {}; self.outtok = {}
        try:
            import comtypes, comtypes.client as cc
            comtypes.CoInitialize()
            self.cc = cc
            self.v = cc.CreateObject("SAPI.SpVoice")
            seen = set()
            def add(tok, src):
                desc = tok.GetDescription(); lang = ""
                try: lang = tok.GetAttribute("Language") or ""
                except Exception: pass
                if not ("412" in lang.lower().split(";") or "korean" in desc.lower() or "한국어" in desc): return
                g = ""
                try: g = {"female": "f", "male": "m"}.get((tok.GetAttribute("Gender") or "").lower(), "")
                except Exception: pass
                k = vkey(desc); g = VGEN.get(k, g)
                label = f"{vlabel(k)} ({'여' if g == 'f' else '남' if g == 'm' else '?'})" + (" · 데스크톱" if "Desktop" in desc else "")
                if tok.Id in seen: return
                seen.add(tok.Id); self.tokens[tok.Id] = tok
                self.voices.append({"id": tok.Id, "desc": desc, "key": k, "g": g, "label": label, "src": src})
            for t in self.v.GetVoices(): add(t, "sapi")
            try:
                cat = cc.CreateObject("SAPI.SpObjectTokenCategory")
                cat.SetId(r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices", False)
                for t in cat.EnumerateTokens(): add(t, "onecore")
            except Exception as e: log.info("OneCore 목록 없음: %s", e)
            self.voices.sort(key=lambda x: (x["src"] != "onecore", x["label"]))   # OneCore(새) 먼저
            for t in self.v.GetAudioOutputs():
                self.outtok[t.Id] = t; self.outputs.append({"id": t.Id, "desc": t.GetDescription()})
            log.info("목소리 %s · 출력 %s", [x["desc"] for x in self.voices], [x["desc"] for x in self.outputs])
        except Exception as e:
            log.exception("SAPI 준비 실패: %s", e)
        try:
            if not os.path.exists(self.chime_path): make_chime(self.chime_path)
        except Exception as e: log.warning("chime: %s", e)
        self.ready.set()
        while True:
            fn, ev, box = self.jobs.get()
            try: box.append(fn())
            except Exception as e: log.exception("speak job"); box.append("err")
            ev.set()
    def call(self, fn, timeout=None):
        ev, box = threading.Event(), []
        self.jobs.put((fn, ev, box)); ev.wait(timeout)
        return box[0] if box else None
    # --- 고르기 ---
    def base_voice(self):
        c = self.app.st["voice"]
        return next((x for x in self.voices if x["id"] == c), None) or (self.voices[0] if self.voices else None)
    def choose(self, pref):
        """보낸 사람이 바라는 목소리 → {'edge': id|None, 'sapi': voice|None, 'shift': n}"""
        st = self.app.st; follow = st["vFollow"] and isinstance(pref, dict)
        g = pref.get("g") if follow and pref.get("g") in ("m", "f") else ""
        name = vcanon(pref.get("name", ""))[:30] if follow and isinstance(pref.get("name"), str) else ""
        base = self.base_voice(); sv, shift = base, 0
        if name and any(x["key"] == name for x in self.voices): sv = next(x for x in self.voices if x["key"] == name)
        elif g and base and base["g"] != g:
            same = next((x for x in self.voices if x["g"] == g), None)
            if same: sv = same
            else: shift = -8 if g == "m" else 5               # 그 성별 목소리가 없으면 높낮이로 구분
        edge = None
        if st["online"]:
            eb = next((x for x in EDGE_VOICES if x["id"] == st["edgeVoice"]), EDGE_VOICES[0]); edge = eb["id"]
            if name and any(x["key"] == name for x in EDGE_VOICES): edge = next(x["id"] for x in EDGE_VOICES if x["key"] == name)
            elif name and any(x["key"] == name for x in self.voices): edge = None    # 이 PC 목소리 이름을 콕 집었으면 그걸로
            elif g and eb["g"] != g: edge = next(x["id"] for x in EDGE_VOICES if x["g"] == g)
        return {"edge": edge, "sapi": sv, "shift": shift}
    def advertise(self):
        out, seen = [], set()
        if self.app.st["online"]:
            for x in EDGE_VOICES: seen.add(x["key"]); out.append({"n": x["key"], "k": vlabel(x["key"]), "g": x["g"]})
        for x in self.voices:
            if x["key"] and x["key"] not in seen: seen.add(x["key"]); out.append({"n": x["key"], "k": vlabel(x["key"]), "g": x["g"]})
        return out[:12]
    # --- 소리 (말하기 스레드에서만 부름) ---
    def _apply_out(self):
        o = self.app.st["out"] if self.app.st["out"] in self.outtok else ""
        if o == getattr(self, "_out_now", ""): return
        try:
            if o: self.v.AudioOutput = self.outtok[o]
            else: self.v = self.cc.CreateObject("SAPI.SpVoice")      # 기본 장치로 돌아가기
            self._out_now = o
        except Exception as e: log.warning("출력 장치: %s", e)
    def _wait(self, timeout_s):
        t0 = time.time()
        while not self.v.WaitUntilDone(100):
            if self.stop.is_set() or time.time() - t0 > timeout_s:
                try: self.v.Speak("", 1 | 2)      # 비우고 멈춤
                except Exception: pass
                return
    def chime(self):
        if FAKE_AUDIO: self.captured.append({"kind": "chime", "t": now()}); time.sleep(0.05); return True
        try:
            self._apply_out(); self.v.Volume = int(self.app.st["volume"])
            fs = self.cc.CreateObject("SAPI.SpFileStream"); fs.Open(self.chime_path, 0, False)
            self.v.SpeakStream(fs, 1); self._wait(6); fs.Close(); return True
        except Exception as e:
            log.warning("chime SAPI 실패 → winsound: %s", e)
            try:
                import winsound; winsound.PlaySound(self.chime_path, winsound.SND_FILENAME); return True
            except Exception: return False
    def say(self, text, ch):
        """→ 'ok' | 'none'(목소리 없음) | 'err'"""
        if FAKE_AUDIO:
            self.captured.append({"kind": "say", "text": text, "edge": ch["edge"], "voice": ch["sapi"]["key"] if ch["sapi"] else "", "shift": ch["shift"] + int(self.app.st["pitch"]),
                                  "rate": int(self.app.st["rate"]), "t": now()})
            t0 = time.time()
            while time.time() - t0 < 0.05 + len(text) * 0.004 and not self.stop.is_set(): time.sleep(0.01)
            return "ok"
        if ch["edge"]:
            try:
                if self._edge(text, ch["edge"]): return "ok"
            except Exception as e: log.warning("인터넷 목소리 실패 → 이 PC 목소리: %s", e)
            if self.stop.is_set(): return "ok"
        return self._sapi(text, ch)
    def _sapi(self, text, ch, to_file=None):
        if not self.v: return "none"
        sv = ch["sapi"]
        if not sv: return "none"
        try:
            if not to_file: self._apply_out()
            self.v.Voice = self.tokens[sv["id"]]
            self.v.Rate = max(-10, min(10, int(self.app.st["rate"]))); self.v.Volume = int(self.app.st["volume"])
            p = max(-10, min(10, ch["shift"] + int(self.app.st["pitch"])))
            xml = f'<pitch absmiddle="{p}">' + text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</pitch>"
            fs = None
            if to_file:
                fs = self.cc.CreateObject("SAPI.SpFileStream"); fmt = self.cc.CreateObject("SAPI.SpAudioFormat"); fmt.Type = 22
                fs.Format = fmt; fs.Open(to_file, 3, False); self.v.AudioOutputStream = fs
            self.v.Speak(xml, 1 | 8); self._wait(20 + len(text) * 0.5)
            if fs: fs.Close(); self.v = self.cc.CreateObject("SAPI.SpVoice"); self._out_now = ""
            return "ok"
        except Exception as e:
            log.exception("SAPI 말하기 실패: %s", e); return "err"
    def edge_file(self, text, voice, path):
        import asyncio, edge_tts
        rate = f"{int(self.app.st['rate']) * 10:+d}%"; pitch = f"{int(self.app.st['pitch']) * 4:+d}Hz"
        async def go():
            await asyncio.wait_for(edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(path), 15)
        asyncio.run(go())
        return os.path.getsize(path)
    def _edge(self, text, voice):
        path = os.path.join(tempfile.gettempdir(), f"ttsspk_{os.getpid()}.mp3")
        self.edge_file(text, voice, path)
        if self.stop.is_set(): return True
        return mci_play(path, int(self.app.st["volume"]) * 10, self.stop)
    def synth_wav(self, text, path, pref=None):
        ch = self.choose(pref or {})
        return self._sapi(text, ch, to_file=path)

def mci_play(path, vol, stop):
    mci = ctypes.windll.winmm.mciSendStringW
    buf = ctypes.create_unicode_buffer(64)
    alias = "ttsspk" + str(random.randint(1000, 9999))
    if mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, None): return False
    try:
        mci(f"setaudio {alias} volume to {max(0, min(1000, vol))}", None, 0, None)
        if mci(f"play {alias}", None, 0, None): return False
        t0 = time.time(); time.sleep(0.2)
        while time.time() - t0 < 60:
            if stop.is_set(): mci(f"stop {alias}", None, 0, None); break
            mci(f"status {alias} mode", buf, 64, None)
            if buf.value in ("stopped", ""): break
            time.sleep(0.1)
        return True
    finally:
        mci(f"close {alias}", None, 0, None)

# ---------------- Windows 도우미 ----------------
k32 = ctypes.windll.kernel32
def keep_awake(on):
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    try: k32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception: pass
def lnk_path(): return os.path.join(STARTUP_DIR, LNK_NAME)
def set_autostart(on):
    p = lnk_path()
    try:
        if not on:
            if os.path.exists(p): os.remove(p)
            return True
        import comtypes, comtypes.client as cc
        comtypes.CoInitialize()
        sh = cc.CreateObject("WScript.Shell", dynamic=True)
        s = sh.CreateShortcut(p)
        if FROZEN: s.TargetPath = sys.executable; s.Arguments = "--tray"; s.IconLocation = sys.executable + ",0"
        else:
            pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            s.TargetPath = pyw if os.path.exists(pyw) else sys.executable; s.Arguments = f'"{os.path.abspath(__file__)}" --tray'
        s.WorkingDirectory = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
        s.Description = "TTS 스피커 — 가족 메시지를 읽어 줘요"
        s.Save()
        return os.path.exists(p)
    except Exception as e:
        log.warning("자동 실행 바로가기: %s", e); return False

def single_instance():
    tag = hashlib.md5(os.path.abspath(HOME).lower().encode()).hexdigest()[:10]
    k32.CreateMutexW.restype = ctypes.wintypes.HANDLE; k32.CreateEventW.restype = ctypes.wintypes.HANDLE; k32.OpenEventW.restype = ctypes.wintypes.HANDLE
    h = k32.CreateMutexW(None, False, f"Local\\TTSSpeakerPC-{tag}")
    if k32.GetLastError() == 183:                           # 이미 켜져 있음 → 그 창을 띄우라고 알림
        e = k32.OpenEventW(0x0002, False, f"Local\\TTSSpeakerPC-show-{tag}")
        if e: k32.SetEvent(e)
        return None, None
    ev = k32.CreateEventW(None, False, False, f"Local\\TTSSpeakerPC-show-{tag}")
    return h, ev

# ---------------- 앱 ----------------
import tkinter as tk
from tkinter import ttk

FONT = "Malgun Gothic"
C = {"bg": "#f2f2f7", "card": "#ffffff", "ink": "#1c1c1e", "dim": "#6c6c70", "accent": "#0066d6", "ok": "#1f8a3b", "warn": "#7a4300", "warnbg": "#fff4e0", "line": "#e3e3e8", "danger": "#c8102e"}

class App:
    def __init__(self, show_ev):
        self.st = Store(); self.uiq = queue.Queue(); self.show_ev = show_ev
        self.state = "connecting"; self.sub = None; self.pair_sub = None; self.pair_code = ""; self.pair_exp = 0
        self.mq = queue.Queue(); self.cur = None; self.busy = False; self.hi_at = 0; self.tray = None
        self.spk = Speaker(self)
        self.root = tk.Tk(); self.root.withdraw()
        self.root.title(APP_NAME); self.root.configure(bg=C["bg"])
        try: self.root.iconbitmap(default=os.path.join(HERE, "icon.ico"))
        except Exception: pass
        self.root.protocol("WM_DELETE_WINDOW", self.hide_settings)
        self._style()
        self.win_built = False; self.popup = None; self.hist_win = None; self.qr_img = None
        self.root.after(150, self._poll)
        keep_awake(self.st["keepAwake"])
        if self.st["autostart"] and not os.path.exists(lnk_path()) and not self.st["setup"]: set_autostart(True)
        self.connect()
        threading.Thread(target=self._msg_loop, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        if show_ev: threading.Thread(target=self._show_waiter, daemon=True).start()
        self._tray_start()
        if CTL_PORT: threading.Thread(target=self._ctl_server, daemon=True).start()
        if not ARGS.tray or not self.st["setup"]: self.root.after(300, self.show_settings)
        log.info("시작 v%s relay=%s ch=%s… home=%s", VERSION, RELAY, self.st["ch"][:10], HOME)

    # --- 스레드 → 화면 ---
    def ui(self, fn): self.uiq.put(fn)
    def _poll(self):
        try:
            while True: self.uiq.get_nowait()()
        except queue.Empty: pass
        except Exception: log.exception("ui")
        self.root.after(150, self._poll)
    def _show_waiter(self):
        while True:
            if k32.WaitForSingleObject(ctypes.wintypes.HANDLE(self.show_ev), 0xFFFFFFFF) == 0: self.ui(self.show_settings)
    def _watch(self):                    # 절전에서 깨어남 등 시간이 튀면 다시 연결
        last = time.time()
        while True:
            time.sleep(5); t = time.time()
            if t - last > 60: log.info("시간이 %d초 튐 → 다시 연결", t - last); self.sub and self.sub.retry()
            last = t
            if self.pair_sub and now() > self.pair_exp: self.ui(self.pair_tick)

    # --- 연결 ---
    def connect(self):
        if self.sub: self.sub.close()
        lt = self.st["lastTs"]
        since = lt if lt and lt > now_sec() - 43000 else now_sec() - 120
        self.sub = Sub(self.st["ch"], since, self.on_recv, lambda s: self.ui(lambda: self.set_state(s)))
    def set_state(self, s):
        self.state = s; self._tray_update()
        if self.win_built:
            M = {"connecting": ("#8e8e93", "연결 중…"), "ok": ("#34c759", "연결됨 · 메시지를 기다리는 중"), "retry": ("#ff9f0a", "끊김 — 다시 연결하는 중…")}
            col, t = M.get(s, ("#8e8e93", s))
            if self.paused(): t += f"  (🔕 {datetime.fromtimestamp(self.st['pauseUntil']):%H:%M}까지 쉬는 중)"
            self.dot.itemconfig("d", fill=col); self.stat.config(text=t)
    def paused(self): return self.st["pauseUntil"] > time.time()
    def is_quiet(self):
        if self.paused(): return True
        if not self.st["qOn"]: return False
        def p(s):
            try: h, m = str(s).split(":"); return int(h) * 60 + int(m)
            except Exception: return 0
        d = datetime.now(); m = d.hour * 60 + d.minute; a, b = p(self.st["qFrom"]), p(self.st["qTo"])
        return False if a == b else (a <= m < b if a < b else m >= a or m < b)

    def on_recv(self, m, d):                 # Sub 스레드
        t = d.get("time")
        if isinstance(t, int) and t > self.st["lastTs"]: self.st.set("lastTs", t)
        if m.get("t") == "ask" and isinstance(t, int) and t > now_sec() - 120: self.say_hi(); return
        if m.get("t") != "msg" or not isinstance(m.get("text"), str) or not isinstance(m.get("id"), str) or not m["text"].strip(): return
        mid = m["id"][:40]
        with self.st.lock:
            if mid in self.st.seen: return
            self.st.seen.append(mid)
        msg = {"id": mid, "from": str(m.get("from") or "가족")[:20], "text": m["text"].strip()[:MAXLEN], "ts": t * 1000 if isinstance(t, int) else now(), "st": "new"}
        v = m.get("v")
        if isinstance(v, dict): msg["v"] = {"g": v.get("g") if v.get("g") in ("m", "f") else "", "name": v["name"][:30] if isinstance(v.get("name"), str) else ""}
        self._add_sender(msg["from"])
        if now() - msg["ts"] > 10 * 60000: msg["missed"] = True
        with self.st.lock: self.st.hist.insert(0, msg); self.st.hist = self.st.hist[:40]
        self.st.save_state(); self.ui(self.refresh_hist)
        log.info("받음 %s %s%s", msg["id"], msg["from"], " (놓침)" if msg.get("missed") else "")
        if msg.get("missed"): return
        self.mq.put(msg)
    def _add_sender(self, who):
        s = self.st["senders"]
        if who not in s: self.st.set("senders", ([who] + s)[:12]); self.ui(self.refresh_senders)
    def say_hi(self):
        if time.time() - self.hi_at < 15: return
        self.hi_at = time.time(); self.spk.ready.wait(10)
        post_bg(self.st["ch"], {"t": "hi", "name": self.st["name"] or "PC", "voices": self.spk.advertise(), "ts": now(), "pc": 1})

    def _msg_loop(self):                     # 한 번에 하나씩: 딩동 → 말하기 × 반복 → 'spoke'
        self.spk.ready.wait(15)
        while True:
            msg = self.mq.get(); self.busy = True
            try:
                self.ui(lambda m=msg: self.show_popup(m, fresh=True))
                if self.is_quiet():
                    msg["quiet"] = True; msg["st"] = "spoke"; post_bg(self.st["ch"], {"t": "spoke", "id": msg["id"], "quiet": 1, "ts": now()})
                else:
                    r = self.announce(msg)
                    if r == "ok": msg["st"] = "spoke"; post_bg(self.st["ch"], {"t": "spoke", "id": msg["id"], "ts": now()})
                    else: msg["st"] = "wait"
                self.st.save_state(); self.ui(self.refresh_hist); self.ui(lambda m=msg: self.cur is m and self.show_popup(m))
                if not self.mq.empty(): time.sleep(0.9)
            except Exception: log.exception("msg")
            finally: self.busy = False
    def spoken_text(self, msg):
        if not self.st["readName"] or not msg.get("from"): return msg["text"]
        j = josa(msg["from"])
        return (f"{msg['from']}{j} 보냈어요. " if j else f"{msg['from']}, ") + msg["text"]
    def announce(self, msg):                 # 말하기 스레드에 맡기고 기다림
        def job():
            self.spk.stop.clear()
            ch = self.spk.choose(msg.get("v") or {})
            if self.st["chime"]: self.spk.chime()
            r = "ok"
            for i in range(int(self.st["rep"])):
                if self.spk.stop.is_set(): break
                r = self.spk.say(self.spoken_text(msg) if i == 0 else msg["text"], ch)
                if r != "ok": break
                if i < int(self.st["rep"]) - 1 and not self.spk.stop.is_set(): time.sleep(1.2)
            return r
        return self.spk.call(job)
    def test_say(self):
        def run():
            if self.busy: return
            self.announce({"from": "", "text": f"{self.st['name'] or 'PC'} 스피커가 준비됐어요."})
        threading.Thread(target=run, daemon=True).start()
    def ack(self, msg):
        if not msg or msg.get("acked") or not msg.get("id"): return
        self.spk.stop.set(); msg["acked"] = True; self.st.save_state(); self.refresh_hist()
        def done(ok):
            if not ok: msg["acked"] = False; self.st.save_state()
            self.ui(lambda: (self.refresh_hist(), self.cur is msg and self.show_popup(msg), (not ok) and self.notify("👍 보내지 못했어요 — 인터넷 연결을 확인해 주세요")))
        post_bg(self.st["ch"], {"t": "ack", "id": msg["id"], "from": self.st["name"] or "PC", "ts": now()}, done)
        if self.cur is msg: self.show_popup(msg)
        self.root.after(1500, lambda: self.cur is msg and self.hide_popup())
    def again(self, msg):
        def run():
            if self.busy: return
            self.busy = True
            try:
                r = self.announce(msg)
                if r == "ok" and msg.get("st") == "wait": msg["st"] = "spoke"; post_bg(self.st["ch"], {"t": "spoke", "id": msg["id"], "ts": now()})
            finally: self.busy = False
        self.spk.stop.set(); threading.Thread(target=run, daemon=True).start()
    def pause(self, minutes):
        self.st.set("pauseUntil", time.time() + minutes * 60 if minutes else 0)
        if minutes: self.spk.stop.set()
        self.set_state(self.state)
    def notify(self, text):
        try: self.tray and self.tray.notify(text, APP_NAME)
        except Exception: pass

    # --- 가족 연결 (코드) ---
    def link_url(self): return LINK_BASE + "#to=" + self.st["ch"] + "&n=" + enc_uri(self.st["name"] or "PC")
    def pair_start(self):
        self.pair_stop()
        self.pair_code = str(100000 + secrets.randbelow(900000)); self.pair_exp = now() + 10 * 60000
        topic = "ttsspk-pair-" + self.pair_code; exp = self.pair_exp
        def on(m, d):
            if m.get("t") != "hello" or not isinstance(m.get("pub"), str) or not isinstance(m.get("nonce"), str) or now() > exp: return
            try:
                self.spk.ready.wait(5)
                w = make_welcome(m["pub"], {"ch": self.st["ch"], "name": self.st["name"] or "PC", "voices": self.spk.advertise()})
                w.update({"t": "welcome", "nonce": m["nonce"][:40]})
                if post(topic, w):
                    who = str(m.get("from") or "가족")[:20]; self._add_sender(who)
                    log.info("코드 연결: %s", who); self.ui(lambda: self.notify(f"📱 {who} 연결됐어요"))
            except Exception: log.exception("welcome")
        self.pair_sub = Sub(topic, now_sec() - 5, on)
        self.pair_tick()
    def pair_stop(self):
        if self.pair_sub: self.pair_sub.close(); self.pair_sub = None
    def pair_tick(self):
        if not self.win_built: return
        if self.pair_sub and now() > self.pair_exp:
            if self.root.state() != "withdrawn": self.pair_start(); return     # 창이 열려 있으면 새 코드로
            self.pair_stop()
        if self.pair_sub:
            s = max(0, (self.pair_exp - now()) // 1000)
            self.code_l.config(text=self.pair_code[:3] + " " + self.pair_code[3:]); self.left_l.config(text=f"코드 남은 시간 {s // 60}:{s % 60:02d}")
        else:
            self.code_l.config(text="— — —"); self.left_l.config(text="")

    # --- 트레이 ---
    def _icon_img(self):
        from PIL import Image, ImageDraw
        base = None
        for p in (os.path.join(HERE, "icon-192.png"), os.path.join(HERE, "..", "icon-192.png")):
            try: base = Image.open(p).convert("RGBA").resize((64, 64), Image.LANCZOS); break
            except Exception: pass
        if base is None: base = Image.new("RGBA", (64, 64), "#0066d6")
        d = ImageDraw.Draw(base)
        col = "#8e8e93" if self.paused() else {"ok": "#34c759", "retry": "#ff9f0a"}.get(self.state, "#ff3b30")
        d.ellipse((38, 38, 63, 63), fill=col, outline="white", width=3)
        return base
    def _tray_title(self):
        s = {"ok": "연결됨", "retry": "끊김 — 다시 연결 중", "connecting": "연결 중"}.get(self.state, self.state)
        if self.paused(): s += f" · {datetime.fromtimestamp(self.st['pauseUntil']):%H:%M}까지 쉬는 중"
        return f"TTS 스피커 · {self.st['name'] or 'PC'} · {s}"
    def _tray_start(self):
        try:
            import pystray
            I = pystray.MenuItem
            menu = pystray.Menu(
                I("설정 열기", lambda: self.ui(self.show_settings), default=True),
                I("연결 QR 보기", lambda: self.ui(lambda: self.show_settings(tab=0))),
                I(lambda it: "쉬기 끝내기" if self.paused() else "1시간 쉬기", lambda: self.ui(lambda: self.pause(0 if self.paused() else 60))),
                I("시험 듣기", lambda: self.ui(self.test_say)),
                I("최근 메시지", lambda: self.ui(self.show_hist)),
                pystray.Menu.SEPARATOR,
                I("종료", lambda: self.ui(self.quit)))
            self.tray = pystray.Icon("TTSSpeaker", self._icon_img(), self._tray_title(), menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception: log.exception("tray")
    def _tray_update(self):
        if not self.tray: return
        try: self.tray.icon = self._icon_img(); self.tray.title = self._tray_title(); self.tray.update_menu()
        except Exception: pass
    def quit(self):
        log.info("종료")
        try: self.sub and self.sub.close(); self.pair_stop()
        except Exception: pass
        try: self.tray and self.tray.stop()
        except Exception: pass
        keep_awake(False)
        self.root.after(200, self.root.destroy)

    # --- 화면 ---
    def _style(self):
        s = ttk.Style(self.root)
        try: s.theme_use("vista")
        except Exception: pass
        for w in ("TLabel", "TCheckbutton", "TRadiobutton", "TFrame", "TLabelframe", "TLabelframe.Label"): s.configure(w, background=C["card"], font=(FONT, 10))
        s.configure("Bg.TFrame", background=C["bg"]); s.configure("TNotebook", background=C["bg"]); s.configure("TNotebook.Tab", font=(FONT, 10), padding=(14, 6))
        s.configure("Dim.TLabel", foreground=C["dim"], font=(FONT, 9)); s.configure("H.TLabel", font=(FONT, 11, "bold"))
        s.configure("Code.TLabel", font=(FONT, 26, "bold")); s.configure("Warn.TLabel", foreground=C["warn"], background=C["warnbg"], font=(FONT, 9))
        s.configure("TButton", font=(FONT, 10), padding=(10, 5)); s.configure("Big.TButton", font=(FONT, 11, "bold"), padding=(14, 8))
        s.configure("Bg.TLabel", background=C["bg"], font=(FONT, 10)); s.configure("BgDim.TLabel", background=C["bg"], foreground=C["dim"], font=(FONT, 9))
    def _build(self):
        r = self.root; r.geometry("600x700"); r.minsize(540, 620); r.resizable(True, True)
        top = ttk.Frame(r, style="Bg.TFrame", padding=(14, 10, 14, 0)); top.pack(fill="x")
        self.dot = tk.Canvas(top, width=14, height=14, bg=C["bg"], highlightthickness=0); self.dot.create_oval(2, 2, 12, 12, fill="#8e8e93", outline="", tags="d"); self.dot.pack(side="left")
        self.stat = ttk.Label(top, text="연결 중…", style="Bg.TLabel"); self.stat.pack(side="left", padx=6)
        nb = ttk.Notebook(r); nb.pack(fill="both", expand=True, padx=12, pady=8); self.nb = nb
        # 탭 1: 연결
        t1 = ttk.Frame(nb, padding=14); nb.add(t1, text="📱 가족 연결")
        ttk.Label(t1, text="이 스피커 이름", style="H.TLabel").grid(row=0, column=0, sticky="w")
        nr = ttk.Frame(t1); nr.grid(row=1, column=0, sticky="we", pady=(4, 10))
        self.name_v = tk.StringVar(value=self.st["name"])
        e = ttk.Entry(nr, textvariable=self.name_v, width=16, font=(FONT, 12)); e.pack(side="left")
        for n in ("아들 방", "거실", "안방", "공부방"): ttk.Button(nr, text=n, command=lambda n=n: self.name_v.set(n)).pack(side="left", padx=2)
        self.name_v.trace_add("write", lambda *a: self._name_changed())
        pr = ttk.Frame(t1); pr.grid(row=2, column=0, sticky="we")
        self.qr_l = tk.Label(pr, bg="white", bd=0); self.qr_l.grid(row=0, column=0, rowspan=6, padx=(0, 16))
        ttk.Label(pr, text="휴대폰 카메라로 QR을 찍으세요.", font=(FONT, 11)).grid(row=0, column=1, sticky="w")
        ttk.Label(pr, text="한 번만 찍으면 계속 연결돼요.", font=(FONT, 10, "bold")).grid(row=1, column=1, sticky="w")
        ttk.Label(pr, text="QR이 안 되면 휴대폰에서 메시지 보내기 →\n코드로 연결에 아래 숫자를 넣어요", style="Dim.TLabel").grid(row=2, column=1, sticky="w", pady=(8, 0))
        self.code_l = ttk.Label(pr, text="— — —", style="Code.TLabel"); self.code_l.grid(row=3, column=1, sticky="w")
        ttk.Label(pr, text="처음 연결할 때만 쓰는 코드예요 (10분 안에 입력)\n한 번 연결되면 계속 쓸 수 있어요", style="Dim.TLabel").grid(row=4, column=1, sticky="w")
        self.left_l = ttk.Label(pr, text="", style="Dim.TLabel"); self.left_l.grid(row=5, column=1, sticky="w")
        br = ttk.Frame(t1); br.grid(row=3, column=0, sticky="w", pady=10)
        ttk.Button(br, text="🔗 링크 복사", command=self._copy_link).pack(side="left"); ttk.Button(br, text="새 코드", command=self.pair_start).pack(side="left", padx=6)
        self.senders_l = ttk.Label(t1, text="", style="Dim.TLabel", wraplength=480); self.senders_l.grid(row=4, column=0, sticky="w")
        ttk.Label(t1, text="메시지는 무료 공개 중계 서버(ntfy.sh)를 거쳐요. 민감한 내용은 보내지 마세요.", style="Dim.TLabel", wraplength=480).grid(row=5, column=0, sticky="w", pady=(12, 0))
        # 탭 2: 소리
        t2 = ttk.Frame(nb, padding=14); nb.add(t2, text="🔊 소리")
        t2.columnconfigure(1, weight=1); row = 0
        self.nov_l = ttk.Label(t2, text="⚠️ 한국어 목소리가 없어요. 설정 → 시간 및 언어 → 언어 및 지역 → 한국어 ⋯ → 언어 옵션 → 음성(음성 인식·읽어 주기) 설치 뒤 다시 켜 주세요.", style="Warn.TLabel", wraplength=490, padding=8)
        self.nov_row = row; row += 1
        ttk.Label(t2, text="목소리").grid(row=row, column=0, sticky="w", pady=4)
        self.voice_cb = ttk.Combobox(t2, state="readonly", width=30); self.voice_cb.grid(row=row, column=1, sticky="we"); self.voice_cb.bind("<<ComboboxSelected>>", lambda e: self._voice_sel())
        ttk.Button(t2, text="🔊 시험 듣기", command=self.test_say).grid(row=row, column=2, padx=(6, 0)); row += 1
        def scale(label, key, lo, hi):
            nonlocal row
            ttk.Label(t2, text=label).grid(row=row, column=0, sticky="w", pady=2)
            v = tk.IntVar(value=int(self.st[key])); lv = ttk.Label(t2, text=str(v.get()), width=4)
            sc = ttk.Scale(t2, from_=lo, to=hi, orient="horizontal", command=lambda x, v=v, lv=lv: (v.set(round(float(x))), lv.config(text=str(v.get())), self.st.set(key, v.get())))
            sc.set(v.get()); sc.grid(row=row, column=1, sticky="we"); lv.grid(row=row, column=2, sticky="w", padx=6); row += 1
        scale("소리 크기", "volume", 0, 100); scale("빠르기", "rate", -5, 5); scale("높낮이", "pitch", -5, 5)
        ttk.Label(t2, text="반복").grid(row=row, column=0, sticky="w", pady=4)
        rf = ttk.Frame(t2); rf.grid(row=row, column=1, sticky="w"); self.rep_v = tk.IntVar(value=int(self.st["rep"]))
        for i in (1, 2, 3): ttk.Radiobutton(rf, text=f"{i}번", value=i, variable=self.rep_v, command=lambda: self.st.set("rep", self.rep_v.get())).pack(side="left", padx=(0, 10))
        row += 1
        def chk(text, key, rr, extra=None):
            v = tk.BooleanVar(value=bool(self.st[key]))
            ttk.Checkbutton(t2, text=text, variable=v, command=lambda: (self.st.set(key, v.get()), extra and extra())).grid(row=rr, column=0, columnspan=3, sticky="w", pady=2)
            return v
        chk("보낸 사람 목소리 따르기 (아빠는 남자, 엄마는 여자 목소리 등)", "vFollow", row); row += 1
        chk("보낸 사람 이름도 읽기 (알림 창엔 늘 보여요)", "readName", row); row += 1
        chk("딩동 소리", "chime", row); row += 1
        qf = ttk.Frame(t2); qf.grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
        self.q_v = tk.BooleanVar(value=self.st["qOn"]); ttk.Checkbutton(qf, text="조용한 시간 (소리 없이 알림 창만)", variable=self.q_v, command=lambda: self.st.set("qOn", self.q_v.get())).pack(side="left")
        self.qf_v = tk.StringVar(value=self.st["qFrom"]); self.qt_v = tk.StringVar(value=self.st["qTo"])
        ttk.Entry(qf, textvariable=self.qf_v, width=6).pack(side="left", padx=(8, 2)); ttk.Label(qf, text="~").pack(side="left"); ttk.Entry(qf, textvariable=self.qt_v, width=6).pack(side="left", padx=2)
        for var, key in ((self.qf_v, "qFrom"), (self.qt_v, "qTo")): var.trace_add("write", lambda *a, var=var, key=key: re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", var.get().strip()) and self.st.set(key, var.get().strip()))
        row += 1
        ttk.Label(t2, text="소리 나올 곳").grid(row=row, column=0, sticky="w", pady=(8, 2))
        self.out_cb = ttk.Combobox(t2, state="readonly", width=30); self.out_cb.grid(row=row, column=1, columnspan=2, sticky="we", pady=(8, 2)); self.out_cb.bind("<<ComboboxSelected>>", lambda e: self._out_sel()); row += 1
        ttk.Label(t2, text="‘항상 이 스피커로’ 고르면 Windows 기본 장치가 바뀌어도 거기로 나와요. (인터넷 목소리는 기본 장치로)", style="Dim.TLabel", wraplength=490).grid(row=row, column=0, columnspan=3, sticky="w"); row += 1
        of = ttk.Frame(t2); of.grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.on_v = tk.BooleanVar(value=self.st["online"]); ttk.Checkbutton(of, text="인터넷 고품질 목소리", variable=self.on_v, command=lambda: self.st.set("online", self.on_v.get())).pack(side="left")
        self.edge_cb = ttk.Combobox(of, state="readonly", width=12, values=[x["label"] for x in EDGE_VOICES]); self.edge_cb.pack(side="left", padx=8)
        self.edge_cb.current(next((i for i, x in enumerate(EDGE_VOICES) if x["id"] == self.st["edgeVoice"]), 0)); self.edge_cb.bind("<<ComboboxSelected>>", lambda e: self.st.set("edgeVoice", EDGE_VOICES[self.edge_cb.current()]["id"]))
        row += 1
        ttk.Label(t2, text="Microsoft 온라인 음성 서비스로 읽은 말을 보내요. 안 되면 이 PC 목소리로 읽어요.", style="Dim.TLabel", wraplength=490).grid(row=row, column=0, columnspan=3, sticky="w"); row += 1
        # 탭 3: PC
        t3 = ttk.Frame(nb, padding=14); nb.add(t3, text="💻 PC")
        self.auto_v = tk.BooleanVar(value=os.path.exists(lnk_path()) if self.st["setup"] else self.st["autostart"])
        ttk.Checkbutton(t3, text="PC 시작 시 자동 실행", variable=self.auto_v, command=self._auto_sel).pack(anchor="w")
        ttk.Label(t3, text="켜 두면 PC를 켤 때 트레이(오른쪽 아래 ^)에서 저절로 듣기 시작해요.", style="Dim.TLabel", wraplength=490).pack(anchor="w", pady=(0, 10))
        self.awake_v = tk.BooleanVar(value=self.st["keepAwake"])
        ttk.Checkbutton(t3, text="절전 막기", variable=self.awake_v, command=lambda: (self.st.set("keepAwake", self.awake_v.get()), keep_awake(self.awake_v.get()))).pack(anchor="w")
        ttk.Label(t3, text="이 프로그램이 켜져 있는 동안 PC가 잠자기(절전)로 들어가지 않아요. 화면은 꺼져도 괜찮아요. PC가 잠들면 메시지를 못 받아요.", style="Dim.TLabel", wraplength=490).pack(anchor="w", pady=(0, 10))
        ttk.Button(t3, text="최근 메시지 보기", command=self.show_hist).pack(anchor="w", pady=(4, 4))
        ttk.Button(t3, text="연결 새로 만들기 (가족이 QR 다시 찍어야 해요)", command=self._reset).pack(anchor="w", pady=4)
        ttk.Label(t3, text=f"기록·설정: {HOME}", style="Dim.TLabel", wraplength=490).pack(anchor="w", pady=(14, 0))
        ttk.Label(t3, text=f"TTS 스피커 PC v{VERSION} · 웹: {PUBLIC_URL}", style="Dim.TLabel").pack(anchor="w")
        # 아래 단추
        bot = ttk.Frame(r, style="Bg.TFrame", padding=(14, 0, 14, 12)); bot.pack(fill="x")
        ttk.Label(bot, text="창을 닫아도 트레이에서 계속 들어요.", style="BgDim.TLabel").pack(side="left")
        ttk.Button(bot, text="✓ 완료 — 트레이로", style="Big.TButton", command=self.hide_settings).pack(side="right")
        self.win_built = True
        self.spk.ready.wait(10); self._fill_voices(); self.refresh_senders(); self.set_state(self.state)
    def _fill_voices(self):
        vs = self.spk.voices
        if vs:
            self.nov_l.grid_remove()
            self.voice_cb.config(values=[x["label"] for x in vs]); b = self.spk.base_voice(); self.voice_cb.current(vs.index(b) if b in vs else 0)
        else:
            self.nov_l.grid(row=self.nov_row, column=0, columnspan=3, sticky="we", pady=(0, 8)); self.voice_cb.config(values=["한국어 목소리 없음"]); self.voice_cb.current(0)
        outs = [{"id": "", "desc": "Windows 기본 장치 (따라감)"}] + self.spk.outputs
        self.out_cb.config(values=[("항상 이 스피커로: " + x["desc"]) if x["id"] else x["desc"] for x in outs]); self._outs = outs
        self.out_cb.current(next((i for i, x in enumerate(outs) if x["id"] == self.st["out"]), 0))
    def _voice_sel(self):
        if self.spk.voices: self.st.set("voice", self.spk.voices[self.voice_cb.current()]["id"])
    def _out_sel(self): self.st.set("out", self._outs[self.out_cb.current()]["id"])
    def _auto_sel(self):
        on = self.auto_v.get(); self.st.set("autostart", on); ok = set_autostart(on)
        if on and not ok: self.auto_v.set(False); self.notify("자동 실행을 만들지 못했어요")
    def _name_changed(self):
        n = self.name_v.get().strip()[:12]
        if n != self.st["name"]: self.st.set("name", n); self._draw_qr(); self._tray_update()
    def _draw_qr(self):
        try:
            import qrcode
            from PIL import ImageTk
            q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=2); q.add_data(self.link_url()); q.make(fit=True)
            img = q.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
            sz = 200 // img.size[0] * img.size[0] or img.size[0]
            from PIL import Image
            img = img.resize((sz, sz), Image.NEAREST); self.qr_img = ImageTk.PhotoImage(img); self.qr_l.config(image=self.qr_img)
        except Exception: log.exception("qr")
    def _copy_link(self):
        self.root.clipboard_clear(); self.root.clipboard_append(self.link_url()); self.notify("🔗 링크를 복사했어요 — 카톡으로 보내 주세요")
    def _reset(self):
        from tkinter import messagebox
        if not messagebox.askyesno(APP_NAME, "연결을 새로 만들까요?\n지금 연결된 가족 휴대폰은 QR을 다시 찍어야 해요.", parent=self.root): return
        self.st.set("ch", "ttsspk-" + rid(22)); self.st.set("senders", []); self.st.set("lastTs", 0); self.connect(); self._draw_qr(); self.refresh_senders(); self.pair_start()
    def refresh_senders(self):
        if self.win_built:
            s = self.st["senders"]; self.senders_l.config(text=("연결된 가족: " + ", ".join(s)) if s else "아직 연결된 가족이 없어요.")
    def show_settings(self, tab=None):
        if not self.win_built: self._build()
        self._draw_qr()
        r = self.root; r.deiconify(); r.lift(); r.attributes("-topmost", True); r.after(400, lambda: r.attributes("-topmost", False))
        if tab is not None: self.nb.select(tab)
        if not self.pair_sub or now() > self.pair_exp: self.pair_start()
        if not getattr(self, "_pt_on", False):
            self._pt_on = True
            def loop():
                if self.root.state() == "withdrawn": self._pt_on = False; return
                self.pair_tick(); self.root.after(1000, loop)
            loop()
    def hide_settings(self):
        first = not self.st["setup"]
        if first:
            self.st.set("setup", True)
            if not self.st["name"]: self.st.set("name", "PC")
        self.root.withdraw(); self.pair_stop()
        if first: self.root.after(800, lambda: self.notify("트레이(오른쪽 아래 ^)에서 계속 듣고 있어요. 아이콘을 누르면 설정이 열려요."))
    # --- 알림 창 (오른쪽 아래, 맨 위) ---
    def show_popup(self, msg, fresh=False):
        self.cur = msg
        if not self.popup:
            p = tk.Toplevel(self.root); p.withdraw(); p.title("TTS 스피커 — 메시지"); p.configure(bg=C["card"]); p.attributes("-topmost", True); p.resizable(False, False)
            p.protocol("WM_DELETE_WINDOW", self.hide_popup)
            self.p_meta = tk.Label(p, bg=C["card"], fg=C["dim"], font=(FONT, 11), anchor="w"); self.p_meta.pack(fill="x", padx=18, pady=(14, 0))
            self.p_text = tk.Label(p, bg=C["card"], fg=C["ink"], font=(FONT, 20, "bold"), wraplength=380, justify="left", anchor="w"); self.p_text.pack(fill="x", padx=18, pady=(4, 12))
            bf = tk.Frame(p, bg=C["card"]); bf.pack(fill="x", padx=14, pady=(0, 14))
            self.p_ack = tk.Button(bf, text="알겠어요 👍", font=(FONT, 13, "bold"), bg=C["ok"], fg="white", activebackground="#17702f", activeforeground="white", relief="flat", padx=16, pady=6, command=lambda: self.ack(self.cur))
            self.p_ack.pack(side="left")
            tk.Button(bf, text="🔁 다시 듣기", font=(FONT, 10), relief="flat", bg="#e9e9ef", padx=10, pady=6, command=lambda: self.cur and self.again(self.cur)).pack(side="left", padx=8)
            tk.Button(bf, text="닫기", font=(FONT, 10), relief="flat", bg="#e9e9ef", padx=10, pady=6, command=self.hide_popup).pack(side="left")
            self.popup = p
        tags = (" · 🔕 조용한 시간" if msg.get("quiet") else "") + (" · 놓친 메시지" if msg.get("missed") else "") + (" · ⚠ 소리 안 남" if msg.get("st") == "wait" else "")
        self.p_meta.config(text=f"{msg['from']} · {hm(msg['ts'])}{tags}")
        self.p_text.config(text=msg["text"])
        self.p_ack.config(text="✓ 보냈어요" if msg.get("acked") else "알겠어요 👍", state="disabled" if msg.get("acked") else "normal")
        p = self.popup; p.update_idletasks()
        w, h = max(430, p.winfo_reqwidth()), p.winfo_reqheight()
        wa = ctypes.wintypes.RECT(); ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
        p.geometry(f"{w}x{h}+{wa.right - w - 16}+{wa.bottom - h - 16}")
        if p.state() == "withdrawn": p.deiconify()
        p.lift()
        if fresh:
            if getattr(self, "_pop_t", None): self.root.after_cancel(self._pop_t)
            self._pop_t = self.root.after(5 * 60000, self.hide_popup)
    def hide_popup(self):
        self.spk.stop.set()
        if self.popup: self.popup.withdraw()
        self.cur = None
    def show_hist(self):
        if not self.hist_win:
            w = tk.Toplevel(self.root); w.title("TTS 스피커 — 최근 메시지"); w.geometry("520x380"); w.configure(bg=C["bg"])
            w.protocol("WM_DELETE_WINDOW", w.withdraw)
            tv = ttk.Treeview(w, columns=("tm", "from", "text", "st"), show="headings", height=14)
            for c, t, wd in (("tm", "시각", 70), ("from", "보낸 사람", 80), ("text", "내용", 250), ("st", "상태", 90)): tv.heading(c, text=t); tv.column(c, width=wd, anchor="w")
            tv.pack(fill="both", expand=True, padx=10, pady=10); tv.bind("<Double-1>", lambda e: self._hist_open())
            ttk.Label(w, text="두 번 누르면 알림 창으로 다시 보여요 (거기서 👍)", style="BgDim.TLabel").pack(anchor="w", padx=12, pady=(0, 10))
            self.hist_win, self.hist_tv = w, tv
        self.refresh_hist(); self.hist_win.deiconify(); self.hist_win.lift()
    def _hist_open(self):
        sel = self.hist_tv.selection()
        if sel: i = int(sel[0]); i < len(self.st.hist) and self.show_popup(self.st.hist[i])
    def refresh_hist(self):
        if not self.hist_win: return
        tv = self.hist_tv; tv.delete(*tv.get_children())
        for i, m in enumerate(self.st.hist[:40]):
            s = "👍 확인" if m.get("acked") else "놓침" if m.get("missed") else "🔕 화면만" if m.get("quiet") else "⚠ 소리 안 남" if m.get("st") == "wait" else "🔊 말함" if m.get("st") == "spoke" else "…"
            tv.insert("", "end", iid=str(i), values=(hm(m["ts"]), m["from"], m["text"], s))

    # --- 시험용 조종 서버 (TTSSPK_CTL_PORT 가 있을 때만, 127.0.0.1) ---
    def _ctl_server(self):
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
        app = self
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _j(self, obj):
                b = json.dumps(obj, ensure_ascii=False).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/state":
                    return self._j({"state": app.state, "ch": app.st["ch"], "name": app.st["name"], "code": app.pair_code if app.pair_sub else "", "link": app.link_url(), "cfg": app.st.cfg,
                                    "hist": app.st.hist, "captured": app.spk.captured, "voices": app.spk.voices, "outputs": app.spk.outputs, "paused": app.paused(), "busy": app.busy,
                                    "popup": bool(app.popup and app.popup.state() != "withdrawn"), "cur": app.cur and app.cur.get("id"), "lnk": os.path.exists(lnk_path()), "tray": bool(app.tray and app.tray.visible),
                                    "settings": app.root.state() != "withdrawn", "advert": app.spk.advertise()})
                self.send_response(404); self.end_headers()
            def do_POST(self):
                q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}; a = q.get("a"); res = {}
                ev = threading.Event()
                def run():
                    try:
                        if a == "ack": app.ack(next((m for m in app.st.hist if m["id"] == q.get("id")), app.cur))
                        elif a == "pause": app.pause(int(q.get("min", 60)))
                        elif a == "test": app.test_say()
                        elif a == "set": app.st.set(q["k"], json.loads(q["v"]))
                        elif a == "autostart": app.st.set("autostart", q.get("on") == "1"); res["ok"] = set_autostart(q.get("on") == "1")
                        elif a == "show": app.show_settings(int(q["tab"]) if "tab" in q else None)
                        elif a == "hide": app.hide_settings()
                        elif a == "hist": app.show_hist()
                        elif a == "newcode": app.pair_start()
                        elif a == "shot":
                            from PIL import ImageGrab
                            w = {"settings": app.root, "popup": app.popup, "hist": app.hist_win}[q.get("w", "settings")]
                            if w is None: raise RuntimeError("no window")
                            w.update(); x, y = w.winfo_rootx(), w.winfo_rooty()
                            ImageGrab.grab((x - 8, y - 32, x + w.winfo_width() + 8, y + w.winfo_height() + 8), all_screens=True).save(q["path"])
                        elif a == "wav": res["r"] = app.spk.call(lambda: app.spk.synth_wav(q["text"], q["path"], json.loads(q.get("pref", "{}"))))
                        elif a == "edgefile": res["r"] = app.spk.edge_file(q["text"], q["voice"], q["path"])
                        elif a == "quit": app.quit()
                    except Exception as e: res["err"] = repr(e); log.exception("ctl")
                    ev.set()
                if a in ("wav", "edgefile"): threading.Thread(target=run, daemon=True).start()
                else: app.ui(run)
                ev.wait(30); self._j(res)
        ThreadingHTTPServer(("127.0.0.1", CTL_PORT), H).serve_forever()

def main():
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass
    h, ev = single_instance()
    if h is None:
        log.info("이미 켜져 있어서 그 창을 띄움"); return 0
    try: ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TTSSpeaker.PC")
    except Exception: pass
    app = App(ev)
    app.root.mainloop()
    return 0

if __name__ == "__main__":
    sys.exit(main())
