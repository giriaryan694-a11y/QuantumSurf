#!/usr/bin/env python3
"""
QuantumSurf — Remote Browser Isolation Privacy Toolkit
Made by Aryan Giri
Direct CDP · Real Chrome · No Playwright · No Snap · Single File
"""
import os,re,json,time,html,hmac,hashlib,secrets,base64,tempfile,sys
import threading,queue,copy,shutil,subprocess,multiprocessing,signal
import urllib.request,zipfile,platform,ctypes.util
from pathlib import Path
from functools import wraps
from datetime import timedelta
from flask import (Flask,request,Response,jsonify,session,redirect,url_for,render_template_string,abort)
import pyfiglet
from termcolor import colored
from colorama import init as colorama_init
try:
    import websocket; WS_AVAILABLE=True
except ImportError: WS_AVAILABLE=False; print("[!] pip install websocket-client")
colorama_init(autoreset=True)

BASE=Path(__file__).parent.resolve()
AUTH_FILE=BASE/"auth.txt"
SECRET_KEY=secrets.token_hex(32)
SESSION_LIFE=timedelta(hours=4)
MAX_BODY=32_768; MAX_ATTEMPTS=5; ATTEMPT_WINDOW=60; CDP_PORT=9222
DEBUG_CDP=os.environ.get("QS_DEBUG","0")=="1"
CHROME_DIR=BASE/".chrome"
_login_attempts={}; _attempts_lock=threading.Lock()
_auth_cache={}; _auth_cache_ts=0.0

def _in_container():
    if Path("/.dockerenv").exists(): return True
    if Path("/run/.containerenv").exists(): return True
    try:
        cg=Path("/proc/1/cgroup").read_text()
        if "docker" in cg or "kubepods" in cg or "containerd" in cg: return True
    except: pass
    try:
        if "container" in Path("/proc/1/environ").read_text(): return True
    except: pass
    if os.environ.get("CODESPACES") or os.environ.get("GITHUB_CODESPACE_TOKEN"): return True
    if "/workspaces/" in str(BASE): return True
    return False
IN_CONTAINER=_in_container()

def _detect_gpu():
    if IN_CONTAINER: return "none",False
    if shutil.which("nvidia-smi"):
        try:
            out=subprocess.check_output(["nvidia-smi","--query-gpu=name","--format=csv,noheader"],timeout=3,stderr=subprocess.DEVNULL).decode().strip()
            if out: return "nvidia",True
        except: pass
    if shutil.which("lspci"):
        try:
            out=subprocess.check_output(["lspci"],timeout=3,stderr=subprocess.DEVNULL).decode()
            for line in out.splitlines():
                l=line.lower()
                if "vga" in l or "3d" in l:
                    if "nvidia" in l: return "nvidia",True
                    if "amd" in l: return "amd",True
                    if "intel" in l: return "intel",True
                    return "unknown",True
        except: pass
    if Path("/dev/dri/renderD128").exists(): return "drm",True
    return "none",False
GPU_VENDOR,HAS_GPU=_detect_gpu()
CPU_CORES=multiprocessing.cpu_count()

def _validate_chrome(path):
    if not path or not os.path.isfile(path) or not os.access(path,os.X_OK): return False
    try:
        r=subprocess.run([path,"--version"],capture_output=True,timeout=5,text=True)
        o=(r.stdout+r.stderr).lower()
        if "snap" in o or "requires" in o: return False
        if r.returncode!=0: return False
        return any(k in o for k in ["chromium","chrome","google"])
    except: return False

def _find_chrome():
    for p in [CHROME_DIR/"chrome-linux64"/"chrome", Path("/opt/chrome/chrome-linux64/chrome")]:
        if _validate_chrome(str(p)): return str(p)
    cands=[]
    for n in ["google-chrome","google-chrome-stable","chromium","chromium-browser","chrome"]:
        p=shutil.which(n)
        if p: cands.append(p)
    cands+=["/usr/bin/google-chrome","/usr/bin/google-chrome-stable","/usr/bin/chromium","/usr/bin/chromium-browser","/opt/google/chrome/chrome"]
    for c in cands:
        if _validate_chrome(c): return c
    return None

def _download_chrome():
    print(colored("[*] Downloading Chrome for Testing...","yellow"))
    try:
        ver=urllib.request.urlopen("https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_STABLE",timeout=15).read().decode().strip()
        print(colored(f"[*] Version: {ver}","cyan"))
        sysname=platform.system().lower(); mach=platform.machine().lower()
        plat="linux64" if sysname=="linux" else ("mac-arm64" if "arm" in mach else "mac-x64") if sysname=="darwin" else "win64"
        CHROME_DIR.mkdir(parents=True,exist_ok=True)
        zp=CHROME_DIR/f"chrome-{plat}.zip"
        urllib.request.urlretrieve(f"https://storage.googleapis.com/chrome-for-testing-public/{ver}/{plat}/chrome-{plat}.zip",str(zp))
        with zipfile.ZipFile(str(zp)) as z: z.extractall(str(CHROME_DIR))
        zp.unlink()
        cb=CHROME_DIR/"chrome-linux64"/"chrome" if sysname=="linux" else CHROME_DIR/f"chrome-{plat}"/"chrome.exe"
        if cb.exists():
            cb.chmod(0o755)
            if _validate_chrome(str(cb)):
                print(colored(f"[✓] Chrome {ver} ready","green")); return str(cb)
    except Exception as e: print(colored(f"[!] Download failed: {e}","red"))
    return None

def _check_chrome_libs(chrome_path):
    missing=[]
    try:
        r=subprocess.run(["ldd",chrome_path],capture_output=True,timeout=10,text=True)
        for line in r.stdout.splitlines():
            if "not found" in line:
                lib=line.strip().split("=>")[0].strip()
                missing.append(lib)
    except: pass
    return missing

def _try_install_libs(missing):
    if not missing: return
    print(colored(f"[!] Missing libraries: {', '.join(missing[:10])}","red"))
    print(colored("[*] Attempting auto-install...","yellow"))
    pkg_map={
        "libnss3.so":"libnss3","libnspr4.so":"libnspr4",
        "libatk-1.0.so.0":"libatk1.0-0","libatk-bridge-2.0.so.0":"libatk-bridge2.0-0",
        "libcups.so.2":"libcups2","libdrm.so.2":"libdrm2",
        "libxkbcommon.so.0":"libxkbcommon0","libXcomposite.so.1":"libxcomposite1",
        "libXdamage.so.1":"libxdamage1","libXfixes.so.3":"libxfixes3",
        "libXrandr.so.2":"libxrandr2","libgbm.so.1":"libgbm1",
        "libasound.so.2":"libasound2","libpango-1.0.so.0":"libpango-1.0-0",
        "libcairo.so.2":"libcairo2","libxshmfence.so.1":"libxshmfence1",
        "libX11-xcb.so.1":"libx11-xcb1","libxcb-dri3.so.0":"libxcb-dri3-0",
        "libgtk-3.so.0":"libgtk-3-0","libgdk_pixbuf-2.0.so.0":"libgdk-pixbuf-2.0-0",
        "libvulkan.so.1":"libvulkan1","libEGL.so.1":"libegl1",
        "libGLESv2.so.2":"libgles2","libopus.so.0":"libopus0",
        "libwebp.so.7":"libwebp7","libwebpdemux.so.2":"libwebpdemux2",
        "libenchant-2.so.2":"libenchant-2-2","libsecret-1.so.0":"libsecret-1-0",
        "libhyphen.so.0":"libhyphen0","libxslt.so.1":"libxslt1.1",
        "libevent-2.1.so.7":"libevent-2.1-7",
    }
    pkgs=set()
    for lib in missing:
        for k,v in pkg_map.items():
            if k in lib or lib.startswith(k.split(".so")[0]):
                pkgs.add(v); break
        else:
            clean=lib.replace(".so","").replace("lib","")
            pkgs.add(f"lib{clean}0" if not clean.startswith("lib") else f"{clean}0")
    if pkgs:
        try:
            subprocess.run(["sudo","apt-get","update","-qq"],timeout=60,capture_output=True)
            subprocess.run(["sudo","apt-get","install","-y","-qq"]+list(pkgs),timeout=120,capture_output=True)
            print(colored("[✓] Libraries installed","green"))
        except Exception as e:
            print(colored(f"[!] Auto-install failed: {e}","red"))
            print(colored(f"    Manual: sudo apt-get install -y {' '.join(sorted(pkgs))}","yellow"))

CHROME_BIN=_find_chrome()
if not CHROME_BIN:
    r=_download_chrome()
    if r: CHROME_BIN=r

if CHROME_BIN:
    _missing=_check_chrome_libs(CHROME_BIN)
    if _missing:
        _try_install_libs(_missing)
        _missing2=_check_chrome_libs(CHROME_BIN)
        if _missing2:
            print(colored(f"[!] Still missing: {', '.join(_missing2)}","red"))

def _kill_existing_chrome():
    try:
        subprocess.run(["pkill","-9","-f",f"chrome.*remote-debugging-port={CDP_PORT}"],capture_output=True,timeout=5)
        time.sleep(0.5)
    except: pass

def _chrome_args():
    ud=tempfile.mkdtemp(prefix="qs_")
    a=[f"--remote-debugging-port={CDP_PORT}",f"--user-data-dir={ud}",
       "--headless=new","--no-sandbox",
       "--disable-setuid-sandbox",
       "--remote-allow-origins=*",
       "--disable-blink-features=AutomationControlled","--disable-infobars",
       "--disable-dev-shm-usage",
       "--disable-extensions","--disable-background-networking","--disable-sync",
       "--no-first-run","--mute-audio","--disable-default-apps",
       "--password-store=basic",
       "--disable-background-timer-throttling",
       "--disable-backgrounding-occluded-windows",
       "--disable-renderer-backgrounding",
       "--disable-ipc-flooding-protection",
       "--disable-features=TranslateUI,PaintHolding",
       "--disable-component-update","--disable-domain-reliability","--no-pings",
       "--disable-client-side-phishing-detection","--disable-hang-monitor",
       "--disable-popup-blocking","--disable-prompt-on-repost",
       "--metrics-recording-only",
       "--enable-features=NetworkService,NetworkServiceInProcess",
       f"--num-raster-threads={max(1,min(CPU_CORES//2,4))}",
       "--window-size=1280,720","--force-color-profile=srgb",
       "--disable-gpu",
       "--disable-software-rasterizer",
       "--use-gl=angle","--use-angle=swiftshader",
       "--no-zygote",
       "--disable-accelerated-2d-canvas",
       "--disable-accelerated-video-decode",
       "--disable-accelerated-video-encode",
       "--disable-background-crash-reporting",
    ]
    return a

LATEST_FRAME=None; FRAME_LOCK=threading.Lock(); FRAME_EVENT=threading.Event()

def _hash(pw): return hashlib.sha256(pw.encode()).hexdigest()
def _load_auth():
    global _auth_cache,_auth_cache_ts
    if time.monotonic()-_auth_cache_ts<30: return
    c={}
    if AUTH_FILE.exists():
        for line in AUTH_FILE.read_text().splitlines():
            if ":" in line: u,p=line.strip().split(":",1); c[u.strip()]=p.strip()
    _auth_cache,_auth_cache_ts=c,time.monotonic()
def check_auth(u,p):
    if not u or not p: return False
    u,p=u.strip()[:64],p.strip()[:128]
    if not AUTH_FILE.exists(): return hmac.compare_digest(u,"admin") and hmac.compare_digest(p,"admin")
    _load_auth(); s=_auth_cache.get(u)
    if not s: hmac.compare_digest(p,"x"); return False
    return hmac.compare_digest(s,_hash(p)) or hmac.compare_digest(s,p)
def _real_ip(): return request.remote_addr or "0.0.0.0"
def _rate_limited(ip):
    now=time.monotonic()
    with _attempts_lock:
        a=[t for t in _login_attempts.get(ip,[]) if now-t<ATTEMPT_WINDOW]; _login_attempts[ip]=a
        return len(a)>=MAX_ATTEMPTS
def _record(ip):
    with _attempts_lock: _login_attempts.setdefault(ip,[]).append(time.monotonic())
def _sanitize_url(raw):
    raw=re.sub(r'[\x00-\x1f\x7f]','',raw.strip())[:2048]
    if re.match(r'^(javascript|data|vbscript|file|blob):',raw,re.I): return "about:blank"
    if not re.match(r'^https?://',raw,re.I): raw="https://"+raw
    return raw
def _check_body():
    if request.content_length and request.content_length>MAX_BODY: abort(413)
def _csp(r):
    r.headers.update({"Content-Security-Policy":"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self';",
        "X-Frame-Options":"DENY","X-Content-Type-Options":"nosniff","X-XSS-Protection":"1; mode=block",
        "Referrer-Policy":"no-referrer","Permissions-Policy":"camera=(), microphone=(), geolocation=()"})
    return r

# ============================================================
# FIX: Proper virtual key codes for arrow/navigation keys
# Source: https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes
# ============================================================
VK_CODES={
    "ArrowLeft":37,"ArrowUp":38,"ArrowRight":39,"ArrowDown":40,
    "Home":36,"End":35,"PageUp":33,"PageDown":34,
    "Insert":45,"Delete":46,
    "F1":112,"F2":113,"F3":114,"F4":115,"F5":116,"F6":117,
    "F7":118,"F8":119,"F9":120,"F10":121,"F11":122,"F12":123,
}

class CDPWorker(threading.Thread):
    # FIX: Increased FPS for faster stream
    ACTIVE_FPS=min(30,max(20,CPU_CORES*5)); IDLE_FPS=12; ACTIVE_SECS=2.0
    # FIX: Lower quality = faster JPEG encoding (~2x speedup)
    QUALITY=50 if HAS_GPU else 42
    def __init__(self):
        super().__init__(daemon=True)
        self.cmd_q=queue.Queue(); self.running=True; self.is_mobile=False
        self.vp_width=1280; self.vp_height=720; self.current_ua=""
        self._last_act=0.0; self._dirty=False; self._frame_n=0
        self._state_lock=threading.Lock()
        self._state={"url":"","title":"","ua":"","mobile":False,"cookies":[],"source":"","vp_width":1280,"vp_height":720}
        self._ws=None; self._ws_connected=False; self._msg_id=0; self._msg_lock=threading.Lock()
        self._responses={}; self._resp_lock=threading.Lock(); self._resp_events={}
        self._chrome_proc=None; self._target_id=None; self._session_id=None
        self._chrome_ver="unknown"; self._ws_url=""
        self._page_loaded=threading.Event()
        self._navigating=False
        self._chrome_stderr=""
        self._pending_requests=0
        self._total_requests=0
        self._finished_requests=0
        self._net_lock=threading.Lock()
        self._load_phase="idle"
    def _next_id(self):
        with self._msg_lock: self._msg_id+=1; return self._msg_id
    def _send_cdp(self,method,params=None,timeout=15):
        if not self._ws or not self._ws_connected: return None
        mid=self._next_id(); msg={"id":mid,"method":method}
        if params: msg["params"]=params
        evt=threading.Event()
        with self._resp_lock: self._resp_events[mid]=evt
        try: self._ws.send(json.dumps(msg))
        except:
            with self._resp_lock: self._resp_events.pop(mid,None)
            return None
        evt.wait(timeout=timeout)
        with self._resp_lock: self._resp_events.pop(mid,None); return self._responses.pop(mid,None)
    def _send_session(self,method,params=None,timeout=15):
        if not self._ws or not self._ws_connected: return None
        if not self._session_id: return self._send_cdp(method,params,timeout)
        mid=self._next_id(); msg={"id":mid,"method":method,"sessionId":self._session_id}
        if params: msg["params"]=params
        evt=threading.Event()
        with self._resp_lock: self._resp_events[mid]=evt
        try: self._ws.send(json.dumps(msg))
        except:
            with self._resp_lock: self._resp_events.pop(mid,None)
            return None
        evt.wait(timeout=timeout)
        with self._resp_lock: self._resp_events.pop(mid,None); resp=self._responses.pop(mid,None)
        if DEBUG_CDP and resp and "error" in resp: print(f"[CDP] {method} → {resp['error']}")
        return resp
    def _on_message(self,ws,message):
        try: data=json.loads(message)
        except: return
        if "id" in data:
            mid=data["id"]
            with self._resp_lock:
                self._responses[mid]=data
                evt=self._resp_events.get(mid)
                if evt: evt.set()
        elif "method" in data:
            method=data.get("method","")
            if method=="Page.loadEventFired":
                self._page_loaded.set()
            elif method=="Page.frameStoppedLoading":
                self._page_loaded.set()
            elif method=="Page.lifecycleEvent":
                params=data.get("params",{})
                if params.get("name") in ("load","networkAlmostIdle","networkIdle"):
                    self._page_loaded.set()
            elif method=="Network.requestWillBeSent":
                with self._net_lock:
                    self._pending_requests+=1
                    self._total_requests+=1
            elif method in ("Network.loadingFinished","Network.loadingFailed"):
                with self._net_lock:
                    self._pending_requests=max(0,self._pending_requests-1)
                    self._finished_requests+=1
    def _on_error(self,ws,error):
        if DEBUG_CDP: print(f"[CDP] WS error: {error}")
    def _on_close(self,ws,code,msg):
        self._ws_connected=False
        if DEBUG_CDP: print(f"[CDP] WS closed: {code} {msg}")
    def _on_open(self,ws): self._ws_connected=True
    def _start_chrome(self):
        if not CHROME_BIN: return False
        _kill_existing_chrome()
        args=[CHROME_BIN]+_chrome_args()
        if DEBUG_CDP: print(f"[DBG] Chrome args: {' '.join(args[:10])}...")
        try:
            self._chrome_proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                preexec_fn=os.setsid if os.name!='nt' else None)
            for _ in range(60):
                time.sleep(0.25)
                if self._chrome_proc.poll() is not None:
                    err_out=""
                    try:
                        _,err_bytes=self._chrome_proc.communicate(timeout=3)
                        err_out=err_bytes.decode(errors='replace')[:1000]
                    except:
                        try: err_out=self._chrome_proc.stderr.read().decode(errors='replace')[:1000]
                        except: pass
                    self._chrome_stderr=err_out
                    print(colored(f"[!] Chrome exited (code {self._chrome_proc.returncode})","red"))
                    if err_out.strip(): print(colored(f"    STDERR: {err_out.strip()[:500]}","red"))
                    return False
                try:
                    resp=urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version",timeout=2)
                    info=json.loads(resp.read().decode())
                    self._chrome_ver=info.get("Browser","Chrome")
                    self._ws_url=info.get("webSocketDebuggerUrl","")
                    if self._ws_url: return True
                except: continue
            return False
        except Exception as e: print(colored(f"[!] Launch failed: {e}","red")); return False
    def _connect_cdp(self):
        if not WS_AVAILABLE: return False
        try:
            self._ws=websocket.WebSocketApp(self._ws_url,on_open=self._on_open,on_message=self._on_message,
                on_error=self._on_error,on_close=self._on_close)
            threading.Thread(target=self._ws.run_forever,daemon=True,
                kwargs={"ping_interval":20,"ping_timeout":10,"skip_utf8_validation":True,
                        "suppress_origin":True}).start()
            for _ in range(50):
                if self._ws_connected: return True
                time.sleep(0.1)
            return False
        except Exception as e:
            if DEBUG_CDP: print(f"[CDP] Connect exception: {e}")
            return False
    def _create_target(self):
        resp=self._send_cdp("Target.createTarget",{"url":"about:blank"},timeout=10)
        if not resp or "result" not in resp: return False
        self._target_id=resp["result"].get("targetId")
        if not self._target_id: return False
        resp2=self._send_cdp("Target.attachToTarget",{"targetId":self._target_id,"flatten":True},timeout=10)
        if not resp2 or "result" not in resp2: return False
        self._session_id=resp2["result"].get("sessionId")
        return bool(self._session_id)
    def _enable_domains(self):
        for m in ["Page.enable","Network.enable","Runtime.enable"]:
            self._send_session(m,{},timeout=5)
        self._send_session("Page.setLifecycleEventsEnabled",{"enabled":True},timeout=5)
    def _inject_stealth(self,vw,vh):
        s=f"""(()=>{{Object.defineProperty(navigator,'webdriver',{{get:()=>undefined,configurable:true}});
        if(!window.chrome)window.chrome={{runtime:{{connect:()=>{{}},sendMessage:()=>{{}},onMessage:{{addListener:()=>{{}}}},id:undefined}},loadTimes:()=>({{}}),csi:()=>({{}}),app:{{isInstalled:false}}}};
        const fp=[{{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format',length:1}},{{name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:'',length:1}},{{name:'Native Client',filename:'internal-nacl-plugin',description:'',length:2}}];
        fp.__proto__=PluginArray.prototype;Object.defineProperty(navigator,'plugins',{{get:()=>fp,configurable:true}});
        if(window.outerWidth===0)window.outerWidth={vw};if(window.outerHeight===0)window.outerHeight={vh+85};
        Object.defineProperty(navigator,'hardwareConcurrency',{{get:()=>{CPU_CORES},configurable:true}});
        Object.defineProperty(navigator,'deviceMemory',{{get:()=>8,configurable:true}});
        Object.defineProperty(navigator,'languages',{{get:()=>['en-US','en'],configurable:true}});
        Object.defineProperty(navigator,'userAgent',{{get:()=>navigator.userAgent.replace(/Headless/g,''),configurable:true}});
        Object.defineProperty(navigator,'appVersion',{{get:()=>navigator.appVersion.replace(/Headless/g,''),configurable:true}});
        if(navigator.permissions){{const o=navigator.permissions.query.bind(navigator.permissions);navigator.permissions.query=(p)=>p.name==='notifications'?Promise.resolve({{state:Notification.permission}}):o(p);}}
        const gp=WebGLRenderingContext.prototype.getParameter;WebGLRenderingContext.prototype.getParameter=function(p){{if(p===37445)return'Intel Inc.';if(p===37446)return'Intel Iris OpenGL Engine';return gp.call(this,p);}};}})();"""
        self._send_session("Page.addScriptToEvaluateOnNewDocument",{"source":s},timeout=5)

    def _wait_for_load(self,timeout=20):
        self._load_phase="loading"
        self._page_loaded.clear()
        with self._net_lock:
            self._pending_requests=0; self._total_requests=0; self._finished_requests=0
        if self._page_loaded.wait(timeout=min(timeout,10)):
            if DEBUG_CDP: print("[CDP] Page load event received")
        deadline=time.monotonic()+min(timeout,10)
        stable_count=0
        while time.monotonic()<deadline:
            with self._net_lock: pending=self._pending_requests
            if pending==0:
                stable_count+=1
                if stable_count>=3: break
            else: stable_count=0
            time.sleep(0.4)
        self._load_phase="rendering"
        time.sleep(2.0)
        try:
            resp=self._send_session("Runtime.evaluate",{"expression":"document.readyState","returnByValue":True},timeout=3)
            if resp and "result" in resp:
                state=resp["result"].get("result",{}).get("value","")
                if state!="complete": time.sleep(1.0)
        except: pass
        self._load_phase="complete"
        return True

    def get_load_status(self):
        with self._net_lock:
            total=self._total_requests; finished=self._finished_requests; pending=self._pending_requests
        pct=0
        if total>0: pct=min(100,int((finished/total)*100))
        if self._load_phase=="complete": pct=100
        elif self._load_phase=="idle": pct=0
        return {"phase":self._load_phase,"pending":pending,"total":total,"finished":finished,"percent":pct,"navigating":self._navigating}

    def run(self):
        if not CHROME_BIN: print(colored("[!] No Chrome binary.","red")); return
        if not WS_AVAILABLE: print(colored("[!] pip install websocket-client","red")); return
        if not self._start_chrome():
            print(colored("[!] FATAL: Chrome failed to start.","red")); return
        print(colored(f"[✓] {self._chrome_ver} (PID {self._chrome_proc.pid})"+(" [GPU]" if HAS_GPU else " [CPU]"),"green"))
        if not self._connect_cdp():
            print(colored("[!] FATAL: CDP WebSocket connection failed.","red")); return
        print(colored("[✓] CDP WebSocket connected","green"))
        if not self._create_target(): print(colored("[!] Target attach failed","red")); return
        print(colored(f"[✓] Session: {self._session_id[:20]}...","green"))
        self._enable_domains(); self._inject_stealth(1280,720)
        time.sleep(0.5); self._take_screenshot()
        print(colored("[✓] Screenshot pipeline OK" if LATEST_FRAME else "[!] Initial screenshot empty","green" if LATEST_FRAME else "yellow"))
        print(colored("[✓] RBI Engine ready\n","green"))
        while self.running:
            t0=time.monotonic()
            while True:
                try: cmd=self.cmd_q.get_nowait(); self._exec(cmd); self._last_act=time.monotonic(); self._dirty=True; self.cmd_q.task_done()
                except queue.Empty: break
            self._take_screenshot()
            if self._dirty or self._frame_n%10==0: self._refresh_state(); self._dirty=False
            elapsed=time.monotonic()-t0
            active=(time.monotonic()-self._last_act)<self.ACTIVE_SECS
            budget=max(0.005,1.0/(self.ACTIVE_FPS if active else self.IDLE_FPS)-elapsed)
            try: cmd=self.cmd_q.get(timeout=budget); self._exec(cmd); self._last_act=time.monotonic(); self._dirty=True; self.cmd_q.task_done()
            except queue.Empty: pass

    # ============================================================
    # FIX: Faster screenshots with optimizeForSpeed + fromSurface
    # ============================================================
    def _take_screenshot(self):
        if not self._session_id: return
        for attempt in range(2):
            try:
                resp=self._send_session("Page.captureScreenshot",{
                    "format":"jpeg",
                    "quality":self.QUALITY,
                    "captureBeyondViewport":False,
                    "optimizeForSpeed":True,
                    "fromSurface":True
                },timeout=5)
                if resp and "result" in resp:
                    b64=resp["result"].get("data","")
                    if b64 and len(b64)>100:
                        frame=base64.b64decode(b64)
                        if len(frame)>100:
                            global LATEST_FRAME
                            with FRAME_LOCK: LATEST_FRAME=frame
                            FRAME_EVENT.set(); self._frame_n+=1; return
                elif resp and "error" in resp:
                    if DEBUG_CDP: print(f"[CDP] Screenshot err (attempt {attempt+1}): {resp['error']}")
                    if attempt==0: time.sleep(0.1)
            except Exception as e:
                if DEBUG_CDP: print(f"[CDP] Screenshot exception (attempt {attempt+1}): {e}")
                if attempt==0: time.sleep(0.1)

    def _refresh_state(self):
        try:
            resp=self._send_session("Runtime.evaluate",{"expression":"JSON.stringify({u:location.href,t:document.title})","returnByValue":True},timeout=5)
            if resp and "result" in resp:
                val=resp["result"].get("result",{}).get("value","")
                if val:
                    info=json.loads(val)
                    with self._state_lock: self._state.update({"url":info.get("u",""),"title":info.get("t",""),
                        "ua":self.current_ua,"mobile":self.is_mobile,"vp_width":self.vp_width,"vp_height":self.vp_height})
        except: pass
    def _exec(self,cmd):
        action=cmd["action"]; data=cmd.get("data",{})
        if action=="navigate":
            url=_sanitize_url(data.get("url","")); mobile=bool(data.get("mobile",False))
            if mobile: vp_w,vp_h=390,844; ua=data.get("ua") or "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"; plat="Android"
            else: vp_w,vp_h=1280,720; ua=data.get("ua") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"; plat="Windows"
            self.current_ua=ua; self.is_mobile=mobile; self.vp_width=vp_w; self.vp_height=vp_h
            self._send_session("Emulation.setUserAgentOverride",{"userAgent":ua,"acceptLanguage":"en-US,en;q=0.9","platform":plat},timeout=5)
            self._send_session("Emulation.setDeviceMetricsOverride",{"width":vp_w,"height":vp_h,"deviceScaleFactor":2 if mobile else 1,"mobile":mobile},timeout=5)
            self._send_session("Emulation.setTouchEmulationEnabled",{"enabled":mobile,"maxTouchPoints":5 if mobile else 0},timeout=5)
            self._send_session("Network.setExtraHTTPHeaders",{"headers":{
                "Accept-Language":"en-US,en;q=0.9",
                "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Upgrade-Insecure-Requests":"1"
            }},timeout=5)
            self._inject_stealth(vp_w,vp_h)
            if data.get("cookies"):
                try:
                    for c in json.loads(data["cookies"]): self._send_session("Network.setCookie",c,timeout=3)
                except: pass
            self._page_loaded.clear(); self._navigating=True; self._load_phase="navigating"
            if DEBUG_CDP: print(f"[CDP] Navigating to: {url}")
            nav_resp=self._send_session("Page.navigate",{"url":url},timeout=20)
            if nav_resp and "error" in nav_resp:
                print(colored(f"[!] Navigation error: {nav_resp['error']}","red"))
                self._navigating=False; self._load_phase="idle"; return
            if DEBUG_CDP: print(f"[CDP] Nav response: {nav_resp}")
            self._wait_for_load(timeout=20)
            self._navigating=False
            self._take_screenshot()
            threading.Timer(3.0, self._take_screenshot).start()
        elif action=="reload":
            self._page_loaded.clear(); self._load_phase="navigating"
            self._send_session("Page.reload",{},timeout=10)
            self._wait_for_load(timeout=15)
            self._take_screenshot()
            threading.Timer(3.0, self._take_screenshot).start()
        elif action in ("go_back","go_forward"):
            self._page_loaded.clear(); self._load_phase="navigating"
            resp=self._send_session("Page.getNavigationHistory",{},timeout=5)
            if resp and "result" in resp:
                idx=resp["result"].get("currentIndex",0); entries=resp["result"].get("entries",[])
                if action=="go_back" and idx>0: self._send_session("Page.navigateToHistoryEntry",{"entryId":entries[idx-1]["id"]},timeout=10)
                elif action=="go_forward" and idx<len(entries)-1: self._send_session("Page.navigateToHistoryEntry",{"entryId":entries[idx+1]["id"]},timeout=10)
                self._wait_for_load(timeout=15)
                self._take_screenshot()
                threading.Timer(3.0, self._take_screenshot).start()
        elif action=="get_source":
            resp=self._send_session("Runtime.evaluate",{"expression":"document.documentElement.outerHTML","returnByValue":True},timeout=10)
            if resp and "result" in resp:
                with self._state_lock: self._state["source"]=resp["result"].get("result",{}).get("value","")
        elif action=="interact":
            t=data.get("type")
            if t=="click":
                x,y=float(data.get("x",0)),float(data.get("y",0))
                if self.is_mobile:
                    self._send_session("Input.dispatchTouchEvent",{"type":"touchStart","touchPoints":[{"x":x,"y":y}]},timeout=3)
                    time.sleep(0.05)
                    self._send_session("Input.dispatchTouchEvent",{"type":"touchEnd","touchPoints":[]},timeout=3)
                else:
                    self._send_session("Input.dispatchMouseEvent",{"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1},timeout=3)
                    self._send_session("Input.dispatchMouseEvent",{"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1},timeout=3)
            elif t=="key":
                k=str(data.get("key",""))[:64]; parts=k.split("+"); mods=0; kv=parts[-1] if parts else k
                for p in parts[:-1]:
                    pl=p.lower()
                    if pl=="shift": mods|=8
                    elif pl=="control": mods|=2
                    elif pl=="alt": mods|=1
                    elif pl=="meta": mods|=4
                sp={"Enter":"\r","Backspace":"\b","Tab":"\t","Escape":"\x1b","Delete":"\x7f"," ":" "}
                # ============================================================
                # FIX: Arrow keys with proper windowsVirtualKeyCode
                # VK codes from: https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes
                # ============================================================
                if kv in VK_CODES:
                    vk=VK_CODES[kv]
                    for tp in ("rawKeyDown","keyUp"):
                        self._send_session("Input.dispatchKeyEvent",{
                            "type":tp,"key":kv,"code":kv,
                            "windowsVirtualKeyCode":vk,
                            "nativeVirtualKeyCode":vk,
                            "modifiers":mods
                        },timeout=3)
                elif kv=="CapsLock":
                    for tp in ("rawKeyDown","keyUp"):
                        self._send_session("Input.dispatchKeyEvent",{"type":tp,"key":"CapsLock","code":"CapsLock","windowsVirtualKeyCode":20,"nativeVirtualKeyCode":20,"modifiers":mods},timeout=3)
                else:
                    txt=sp.get(kv,kv); vk=ord(txt[0]) if txt else 0
                    for tp in ("keyDown","keyUp"):
                        self._send_session("Input.dispatchKeyEvent",{"type":tp,"key":kv,"text":txt,"unmodifiedText":txt,"windowsVirtualKeyCode":vk,"nativeVirtualKeyCode":vk,"modifiers":mods},timeout=3)
            elif t in ("scroll_up","scroll_down"):
                px=int(data.get("px",400)); dy=px if t=="scroll_down" else -px
                self._send_session("Input.dispatchMouseEvent",{"type":"mouseWheel","x":self.vp_width//2,"y":self.vp_height//2,"deltaX":0,"deltaY":dy},timeout=3)
            elif t=="touch_scroll":
                dy=float(data.get("dy",0)); cx,cy=self.vp_width//2,self.vp_height//2
                if self.is_mobile:
                    steps=max(4,int(abs(dy))//25); sdy=dy/steps
                    self._send_session("Input.dispatchTouchEvent",{"type":"touchStart","touchPoints":[{"x":cx,"y":cy}]},timeout=3)
                    for i in range(1,steps+1): self._send_session("Input.dispatchTouchEvent",{"type":"touchMove","touchPoints":[{"x":cx,"y":int(cy+sdy*i)}]},timeout=2)
                    self._send_session("Input.dispatchTouchEvent",{"type":"touchEnd","touchPoints":[]},timeout=3)
                else: self._send_session("Input.dispatchMouseEvent",{"type":"mouseWheel","x":cx,"y":cy,"deltaX":0,"deltaY":int(dy)},timeout=3)
        elif action=="change_ua":
            ua=re.sub(r'[\x00-\x1f\x7f]','',str(data.get("ua","")).strip())[:512]
            if ua:
                self.current_ua=ua
                resp=self._send_session("Runtime.evaluate",{"expression":"window.location.href","returnByValue":True},timeout=5)
                cur=""
                if resp and "result" in resp: cur=resp["result"].get("result",{}).get("value","")
                if cur and cur!="about:blank": self.cmd_q.put({"action":"navigate","data":{"url":cur,"ua":ua,"mobile":self.is_mobile}})
    def get_state(self,lite=False):
        with self._state_lock: s=copy.deepcopy(self._state)
        if lite: s.pop("cookies",None); s.pop("source",None)
        return s
    def send(self,action,data=None): self.cmd_q.put({"action":action,"data":data or {}})
    def stop(self):
        self.running=False
        if self._ws:
            try: self._ws.close()
            except: pass
        if self._chrome_proc:
            try: os.killpg(os.getpgid(self._chrome_proc.pid),signal.SIGTERM)
            except:
                try: self._chrome_proc.terminate()
                except: pass

worker=CDPWorker()
if CHROME_BIN and WS_AVAILABLE: worker.start()
else: print(colored("[!] Cannot start RBI engine.","red"))

app=Flask(__name__); app.secret_key=SECRET_KEY; app.permanent_session_lifetime=SESSION_LIFE
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax",SESSION_COOKIE_SECURE=False,MAX_CONTENT_LENGTH=MAX_BODY)
app.after_request(_csp)
def login_required(fn):
    @wraps(fn)
    def w(*a,**kw):
        if not session.get("authenticated"):
            if request.is_json: return jsonify({"error":"Unauthorized"}),401
            return redirect(url_for("login_page"))
        return fn(*a,**kw)
    return w

LOGIN_HTML=r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>QuantumSurf — Login</title><link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@700;900&display=swap" rel="stylesheet"><style>:root{--bg:#080c10;--panel:#0d1117;--acc:#00ff90;--danger:#ff4444;--text:#c8ffd4;--muted:#4a7a58;--glow:0 0 20px #00ff9040}*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);font-family:'Share Tech Mono',monospace;min-height:100vh;display:flex;align-items:center;justify-content:center}body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(0,255,144,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,255,144,.03) 1px,transparent 1px);background-size:40px 40px;animation:g 20s linear infinite;pointer-events:none}@keyframes g{to{background-position:40px 40px}}.card{background:var(--panel);border:1px solid var(--acc);box-shadow:var(--glow);padding:48px 40px;width:min(420px,92vw);position:relative;animation:fi .4s ease}@keyframes fi{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:none}}.card::before,.card::after,.c1,.c2{content:'';position:absolute;width:14px;height:14px;border-color:var(--acc);border-style:solid}.card::before{top:-1px;left:-1px;border-width:2px 0 0 2px}.card::after{top:-1px;right:-1px;border-width:2px 2px 0 0}.c1{bottom:-1px;right:-1px;border-width:0 2px 2px 0}.c2{bottom:-1px;left:-1px;border-width:0 0 2px 2px}.logo{font-family:'Orbitron',sans-serif;font-weight:900;font-size:28px;color:var(--acc);text-align:center;letter-spacing:3px;text-shadow:0 0 30px var(--acc);margin-bottom:6px}.sub{text-align:center;color:var(--muted);font-size:11px;letter-spacing:2px;margin-bottom:36px}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--acc);margin-right:6px;animation:p 1.5s ease-in-out infinite;vertical-align:middle}@keyframes p{0%,100%{opacity:1;box-shadow:0 0 6px var(--acc)}50%{opacity:.4;box-shadow:none}}label{display:block;color:var(--acc);font-size:11px;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px}input[type=text],input[type=password]{width:100%;background:#050809;border:1px solid var(--muted);color:var(--text);font-family:'Share Tech Mono',monospace;font-size:14px;padding:12px 14px;outline:none;transition:border-color .2s;margin-bottom:20px}input:focus{border-color:var(--acc)}.btn{width:100%;background:transparent;border:1px solid var(--acc);color:var(--acc);font-family:'Orbitron',sans-serif;font-size:13px;font-weight:700;letter-spacing:3px;padding:14px;cursor:pointer;text-transform:uppercase;margin-top:6px;transition:background .2s,color .2s}.btn:hover{background:var(--acc);color:#000}.err{background:rgba(255,68,68,.1);border:1px solid var(--danger);color:var(--danger);padding:10px 14px;font-size:12px;margin-bottom:18px}.foot{text-align:center;color:var(--muted);font-size:10px;margin-top:28px}</style></head><body><div class="card"><div class="c1"></div><div class="c2"></div><div class="logo">QUANTUMSURF</div><div class="sub"><span class="dot"></span>Remote Browser Isolation</div>{% if error %}<div class="err">⚠ {{ error }}</div>{% endif %}<form method="POST" action="/login" autocomplete="off"><input type="hidden" name="csrf_token" value="{{ csrf }}"><label>Username</label><input type="text" name="username" placeholder="Enter username" maxlength="64" required autofocus><label>Password</label><input type="password" name="password" placeholder="Enter password" maxlength="128" required><button type="submit" class="btn">▶ Authenticate</button></form><div class="foot">Made by Aryan Giri · Privacy Toolkit</div></div></body></html>"""

APP_HTML=r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1"><title>QuantumSurf</title><link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@500;700&display=swap" rel="stylesheet"><style>:root{--bg:#080c10;--surf:#0d1117;--surf2:#111820;--bdr:#1e2d20;--acc:#00ff90;--acc2:#00cfff;--warn:#ffcc00;--danger:#ff4444;--text:#c8ffd4;--muted:#4a7a58;--glow:0 0 16px #00ff9030}*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--text);font-family:'Share Tech Mono',monospace;height:100dvh;display:flex;flex-direction:column;overflow:hidden}#tb{background:var(--surf);border-bottom:1px solid var(--bdr);height:48px;display:flex;align-items:center;gap:6px;padding:0 10px;flex-shrink:0;z-index:10}.brand{font-family:'Orbitron',sans-serif;font-size:13px;font-weight:700;color:var(--acc);letter-spacing:2px;white-space:nowrap;padding-right:6px;text-shadow:0 0 12px var(--acc);display:none}@media(min-width:640px){.brand{display:block}}.nb{background:none;border:1px solid var(--bdr);color:var(--muted);width:32px;height:32px;border-radius:4px;cursor:pointer;font-size:14px;display:flex;align-items:center;justify-content:center;transition:border-color .15s,color .15s;flex-shrink:0}.nb:hover{border-color:var(--acc);color:var(--acc)}#addr{flex:1;background:#050809;border:1px solid var(--bdr);color:var(--text);font-family:'Share Tech Mono',monospace;font-size:13px;padding:0 12px;height:32px;outline:none;transition:border-color .2s,box-shadow .2s;min-width:0}#addr:focus{border-color:var(--acc);box-shadow:var(--glow)}#btn-go{background:var(--acc);color:#000;border:none;font-family:'Orbitron',sans-serif;font-size:11px;font-weight:700;letter-spacing:1px;padding:0 14px;height:32px;cursor:pointer;flex-shrink:0;transition:opacity .15s}#btn-go:hover{opacity:.85}#btn-tb{background:none;border:1px solid var(--acc2);color:var(--acc2);font-family:'Orbitron',sans-serif;font-size:10px;font-weight:700;letter-spacing:1px;padding:0 10px;height:32px;cursor:pointer;flex-shrink:0;white-space:nowrap;transition:background .15s,color .15s}#btn-tb:hover{background:var(--acc2);color:#000}#btn-out{background:none;border:1px solid var(--bdr);color:var(--danger);font-size:12px;width:32px;height:32px;border-radius:4px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}#vp{flex:1;background:#000;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}#load-bar{position:absolute;top:0;left:0;height:3px;background:linear-gradient(90deg,var(--acc),var(--acc2));transition:width .3s ease;z-index:100;box-shadow:0 0 10px var(--acc);border-radius:0 2px 2px 0}#feed{max-width:100%;max-height:100%;display:block;cursor:crosshair;object-fit:contain}#ov{position:absolute;inset:0;background:rgba(0,0,0,.85);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;color:var(--muted);font-family:'Orbitron',sans-serif;font-size:13px;letter-spacing:2px}.spin{width:40px;height:40px;border:3px solid var(--bdr);border-top-color:var(--acc);border-radius:50%;animation:spin .8s linear infinite;display:none}@keyframes spin{to{transform:rotate(360deg)}}#ov-msg{text-align:center;padding:0 20px;line-height:1.6}#ov-pct{font-family:'Orbitron',sans-serif;font-size:22px;font-weight:700;color:var(--acc);text-shadow:0 0 20px var(--acc)}#sb{background:var(--surf);border-top:1px solid var(--bdr);height:22px;display:flex;align-items:center;padding:0 10px;gap:14px;font-size:10px;color:var(--muted);flex-shrink:0;letter-spacing:.5px;overflow:hidden}.si{display:flex;align-items:center;gap:5px;white-space:nowrap}.dot{width:5px;height:5px;border-radius:50%;background:var(--acc);animation:blink 2s ease-in-out infinite}@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}#tbx{position:fixed;top:0;right:-340px;width:320px;height:100%;background:var(--surf);border-left:1px solid var(--acc2);box-shadow:-4px 0 30px rgba(0,207,255,.1);z-index:200;transition:right .25s cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;overflow:hidden}#tbx.open{right:0}.th{background:var(--surf2);border-bottom:1px solid var(--bdr);padding:14px 16px;display:flex;align-items:center;justify-content:space-between}.tt{font-family:'Orbitron',sans-serif;font-size:13px;font-weight:700;color:var(--acc2);letter-spacing:2px}.tc{background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer;line-height:1;transition:color .15s}.tc:hover{color:var(--danger)}.tabs{display:flex;border-bottom:1px solid var(--bdr)}.tab{flex:1;background:none;border:none;color:var(--muted);font-family:'Share Tech Mono',monospace;font-size:11px;padding:10px 4px;cursor:pointer;border-bottom:2px solid transparent;transition:color .15s,border-color .15s;letter-spacing:.5px;text-transform:uppercase}.tab.on{color:var(--acc2);border-color:var(--acc2)}.tbody{flex:1;overflow-y:auto;padding:16px}.tbody::-webkit-scrollbar{width:4px}.tbody::-webkit-scrollbar-thumb{background:var(--bdr)}.tsec{display:none}.tsec.on{display:block}.lbl{color:var(--acc2);font-size:10px;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;display:block}.fi,.fs,.fta{width:100%;background:#050809;border:1px solid var(--bdr);color:var(--text);font-family:'Share Tech Mono',monospace;font-size:12px;padding:8px 10px;outline:none;transition:border-color .2s;margin-bottom:12px}.fi:focus,.fs:focus,.fta:focus{border-color:var(--acc2)}.fta{height:80px;resize:vertical}.fs option{background:#0d1117}.xbtn{width:100%;background:transparent;border:1px solid var(--acc2);color:var(--acc2);font-family:'Share Tech Mono',monospace;font-size:12px;padding:9px;cursor:pointer;letter-spacing:1px;text-transform:uppercase;transition:background .15s,color .15s;margin-bottom:8px}.xbtn:hover{background:var(--acc2);color:#000}.xbtn.g{border-color:var(--acc);color:var(--acc)}.xbtn.g:hover{background:var(--acc);color:#000}.ua-pre{background:var(--surf2);border:1px solid var(--bdr);color:var(--text);font-size:11px;padding:7px 10px;margin-bottom:6px;cursor:pointer;width:100%;text-align:left;border-left:3px solid transparent;transition:border-left-color .15s}.ua-pre:hover{border-left-color:var(--acc2)}.pills{display:flex;gap:8px;margin-bottom:12px}.pill{flex:1;background:var(--surf2);border:1px solid var(--bdr);color:var(--muted);font-family:'Orbitron',sans-serif;font-size:11px;padding:9px;cursor:pointer;text-align:center;letter-spacing:1px;transition:all .15s}.pill.on{border-color:var(--acc);color:var(--acc);background:#001a0d}.srcbox{background:#050809;border:1px solid var(--bdr);color:var(--acc);font-size:10px;height:280px;overflow:auto;padding:8px;white-space:pre-wrap;word-break:break-all;font-family:'Share Tech Mono',monospace;line-height:1.5;margin-bottom:8px}.kb-row{display:flex;gap:4px;margin-bottom:4px;flex-wrap:wrap}.key{background:var(--surf2);border:1px solid var(--bdr);color:var(--text);font-family:'Share Tech Mono',monospace;font-size:13px;min-width:30px;height:36px;padding:0 6px;cursor:pointer;display:flex;align-items:center;justify-content:center;border-radius:3px;transition:background .1s,transform .1s;user-select:none;-webkit-user-select:none;flex:1}.key:active,.key.hit{background:var(--acc2);color:#000;border-color:var(--acc2);transform:scale(.92)}.key.w{flex:2}.key.xw{flex:3}.key.sp{flex:5;min-width:80px}.key.sk{color:var(--acc2);border-color:var(--acc2);background:rgba(0,207,255,.06);font-size:11px}.key.dk{color:var(--danger);border-color:var(--danger);background:rgba(255,68,68,.06)}.srow{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}#op-wrap{margin-top:16px;padding:12px 10px 10px;background:var(--surf2);border:1px solid var(--bdr);border-radius:3px}.op-lbl{display:flex;justify-content:space-between;align-items:center;font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:var(--acc2);margin-bottom:10px}#op-val{font-family:'Orbitron',sans-serif;font-size:11px;font-weight:700;color:var(--acc);min-width:38px;text-align:right}.op-tw{display:flex;align-items:center;gap:8px;margin-bottom:10px}.oi{font-size:14px;flex-shrink:0}.oi.dim{color:var(--muted)}.oi.br{color:var(--warn);text-shadow:0 0 8px var(--warn)}.op-track{flex:1;position:relative;height:28px;display:flex;align-items:center}#op-bar{position:absolute;left:0;top:50%;transform:translateY(-50%);height:4px;border-radius:2px;background:linear-gradient(90deg,rgba(0,255,144,.15),var(--acc));pointer-events:none}.op-rng{-webkit-appearance:none;appearance:none;width:100%;height:4px;background:var(--bdr);border-radius:2px;outline:none;cursor:pointer;position:relative;z-index:1}.op-rng::-webkit-slider-thumb{-webkit-appearance:none;width:22px;height:22px;border-radius:50%;background:var(--acc);border:3px solid #000;box-shadow:0 0 8px rgba(0,255,144,.5);cursor:pointer}.op-rng:active::-webkit-slider-thumb{box-shadow:0 0 16px rgba(0,255,144,.8)}.op-rng::-moz-range-thumb{width:20px;height:20px;border-radius:50%;background:var(--acc);border:3px solid #000}.op-pre{display:flex;gap:6px}.op-btn{flex:1;background:var(--surf);border:1px solid var(--bdr);color:var(--muted);font-family:'Share Tech Mono',monospace;font-size:10px;padding:5px 0;cursor:pointer;letter-spacing:.5px;text-transform:uppercase;border-radius:2px}.op-btn:hover,.op-btn.on{border-color:var(--acc2);color:var(--acc2);background:rgba(0,207,255,.08)}#bk{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:199}@media(max-width:540px){#tbx{width:100%;right:-100%}#tbx.open{right:0}#bk.show{display:block}}</style></head><body><div id="tb"><span class="brand">QS</span><button class="nb" onclick="nav_back()">&#9668;</button><button class="nb" onclick="nav_fwd()">&#9658;</button><button class="nb" onclick="nav_reload()">&#8635;</button><input id="addr" type="text" placeholder="https://example.com" spellcheck="false"><button id="btn-go" onclick="go()">GO</button><button id="btn-tb" onclick="openTB()">⚙ TOOLBOX</button><button id="btn-out" onclick="logout()" title="Logout">⏻</button></div><div id="vp"><div id="load-bar" style="width:0%"></div><img id="feed" alt=""><div id="ov"><div class="spin" id="spin"></div><span id="ov-pct"></span><span id="ov-msg">⬆ ENTER A URL AND PRESS GO</span></div></div><div id="sb"><div class="si"><span class="dot"></span><span id="s-url">IDLE</span></div><div class="si">│ <span id="s-mode">DESKTOP</span></div><div class="si" style="margin-left:auto"><span id="s-hw" style="color:var(--muted)">⚙ detecting...</span></div></div><div id="bk" onclick="closeTB()"></div><div id="tbx"><div class="th"><span class="tt">⚙ TOOLBOX</span><button class="tc" onclick="closeTB()">✕</button></div><div class="tabs"><button class="tab on" onclick="tab('ua')">UA</button><button class="tab" onclick="tab('view')">View</button><button class="tab" onclick="tab('kb')">⌨ KB</button><button class="tab" onclick="tab('src')">Src</button></div><div class="tbody"><div id="t-ua" class="tsec on"><span class="lbl">Quick Presets</span><button class="ua-pre" onclick="setUA('cw')">🖥 Chrome — Windows</button><button class="ua-pre" onclick="setUA('cm')">🍎 Chrome — macOS</button><button class="ua-pre" onclick="setUA('fw')">🦊 Firefox — Windows</button><button class="ua-pre" onclick="setUA('sm')">🧭 Safari — macOS</button><button class="ua-pre" onclick="setUA('ew')">🌐 Edge — Windows</button><button class="ua-pre" onclick="setUA('ip')">📱 iPhone 16</button><button class="ua-pre" onclick="setUA('an')">🤖 Android Chrome</button><button class="ua-pre" onclick="setUA('gb')">🤖 Googlebot</button><span class="lbl" style="margin-top:12px">Custom UA</span><textarea class="fta" id="ua-custom" placeholder="Paste custom user agent..."></textarea><button class="xbtn g" onclick="applyUA()">▶ Apply & Reload</button><div id="ua-cur" style="font-size:10px;color:var(--muted);margin-top:4px;word-break:break-all;line-height:1.4"></div></div><div id="t-view" class="tsec"><span class="lbl">Viewport Mode</span><div class="pills"><div class="pill on" id="p-desk" onclick="setMode('desktop')">🖥 DESKTOP</div><div class="pill" id="p-mob" onclick="setMode('mobile')">📱 MOBILE</div></div><span class="lbl">Mobile Presets</span><select class="fs" id="mob-pre" onchange="applyMobPre()"><option value="">-- Select preset --</option><option value="ip16">iPhone 16 Pro (393×852)</option><option value="ipse">iPhone SE (375×667)</option><option value="px8">Pixel 8 (412×915)</option><option value="ss24">Samsung S24 (360×780)</option><option value="ipad">iPad Pro (1024×1366)</option></select><span class="lbl">Scroll Speed</span><input class="fi" type="range" id="spd" min="100" max="1200" value="400" style="padding:0;background:none;border:none;cursor:pointer"><div style="font-size:10px;color:var(--muted);margin-top:-8px;margin-bottom:12px">Pixels/scroll: <span id="spd-val">400</span></div><button class="xbtn" onclick="scrollUp()">▲ Scroll Up</button><button class="xbtn" onclick="scrollDown()">▼ Scroll Down</button></div><div id="t-kb" class="tsec"><div style="color:var(--acc2);font-size:10px;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px">⌨ On-Screen Keyboard</div><div style="display:flex;gap:6px;margin-bottom:10px"><input class="fi" id="kb-in" type="text" placeholder="Type text to send..." style="flex:1;margin-bottom:0" autocorrect="off" autocapitalize="off" spellcheck="false"><button class="xbtn" onclick="kbSend()" style="width:auto;padding:0 12px;margin-bottom:0;flex-shrink:0">SEND</button></div><div id="kb-rows"></div><div style="margin-top:8px"><div style="color:var(--muted);font-size:10px;letter-spacing:1px;margin-bottom:6px">SPECIAL KEYS</div><div class="srow" id="kb-spec"></div></div><div style="color:var(--muted);font-size:10px;margin-top:10px;line-height:1.5">Tap keys → sent to remote browser.<br>Use text field for longer input.</div><div id="op-wrap"><div class="op-lbl"><span>☀ Panel Transparency</span><span id="op-val">100%</span></div><div class="op-tw"><span class="oi dim">◐</span><div class="op-track"><div id="op-bar" style="width:100%"></div><input type="range" id="op-rng" min="15" max="100" value="100" class="op-rng" oninput="setOpacity(this.value)"></div><span class="oi br">☀</span></div><div class="op-pre"><button class="op-btn" onclick="setOpacity(30)">Ghost</button><button class="op-btn" onclick="setOpacity(55)">Half</button><button class="op-btn" onclick="setOpacity(80)">Dim</button><button class="op-btn on" onclick="setOpacity(100)">Full</button></div></div></div><div id="t-src" class="tsec"><button class="xbtn g" onclick="fetchSrc()">⟳ Fetch Source</button><div id="src-box" class="srcbox">Click "Fetch Source" to load...</div><button class="xbtn" onclick="copySrc()">⎘ Copy</button><button class="xbtn" onclick="dlSrc()">⬇ Download .html</button></div></div></div><script>const S={mobile:false,vpW:1280,vpH:720,ua:'',scrollPx:400,nav:false,srcCache:'',lastTouch:false,feedActive:false};const UAS={cw:'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',cm:'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',fw:'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0',sm:'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',ew:'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',ip:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1',an:'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36',gb:'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'};async function api(ep,data={}){try{const r=await fetch(ep,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify(data),credentials:'same-origin'});if(r.status===401){location.href='/login';return null}return await r.json()}catch{return null}}const feed=document.getElementById('feed'),addr=document.getElementById('addr'),ov=document.getElementById('ov'),spin=document.getElementById('spin'),omsg=document.getElementById('ov-msg'),opct=document.getElementById('ov-pct'),loadBar=document.getElementById('load-bar');function showLoad(on,msg){ov.style.display=on?'flex':'none';spin.style.display=on?'block':'none';if(msg)omsg.textContent=msg;if(!on){opct.textContent='';loadBar.style.width='0%'}}function startFeed(){if(S.feedActive)return;S.feedActive=true;feed.src='/video_feed?t='+Date.now()}let loadIv=null;function pollLoadStatus(){if(loadIv)clearInterval(loadIv);loadIv=setInterval(async()=>{try{const r=await fetch('/load_status',{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});if(!r.ok)return;const d=await r.json();const pct=d.percent||0;loadBar.style.width=pct+'%';if(d.phase==='navigating'){omsg.textContent='CONNECTING...';opct.textContent=''}else if(d.phase==='loading'){omsg.textContent=`LOADING RESOURCES (${d.finished}/${d.total})`;opct.textContent=pct+'%'}else if(d.phase==='rendering'){omsg.textContent='RENDERING PAGE...';opct.textContent=pct+'%'}else if(d.phase==='complete'){omsg.textContent='✓ PAGE LOADED';opct.textContent='100%';loadBar.style.width='100%';setTimeout(()=>{showLoad(false);clearInterval(loadIv);loadIv=null},800)}else if(d.phase==='idle'&&!S.nav){clearInterval(loadIv);loadIv=null}}catch{}},400)}function go(){const url=addr.value.trim();if(!url)return;S.nav=true;showLoad(true,'CONNECTING...');loadBar.style.width='5%';api('/navigate',{url,ua:S.ua,mobile:S.mobile});startFeed();pollLoadStatus();setTimeout(()=>{if(S.nav){showLoad(false);if(loadIv){clearInterval(loadIv);loadIv=null}}},25000)}addr.addEventListener('keydown',e=>{if(e.key==='Enter')go()});function nav_back(){api('/action',{action:'go_back'})}function nav_fwd(){api('/action',{action:'go_forward'})}function nav_reload(){S.nav=true;showLoad(true,'RELOADING...');api('/action',{action:'reload'});pollLoadStatus();setTimeout(()=>{showLoad(false);if(loadIv){clearInterval(loadIv);loadIv=null}},20000)}function logout(){if(confirm('Logout?'))location.href='/logout'}feed.onload=()=>{};feed.onerror=()=>{if(S.nav){S.feedActive=false;setTimeout(startFeed,1000)}};setInterval(async()=>{const res=await api('/get_state',{lite:true});if(!res)return;if(res.url&&res.url!=='about:blank'&&document.activeElement!==addr)addr.value=res.url;if(S.nav&&res.url&&res.url!=='about:blank'){S.nav=false}document.getElementById('s-url').textContent=(res.title||res.url||'IDLE').substring(0,70);if(res.vp_width)S.vpW=res.vp_width;if(res.vp_height)S.vpH=res.vp_height;if(typeof res.mobile==='boolean'&&res.mobile!==S.mobile){S.mobile=res.mobile;document.getElementById('p-desk').classList.toggle('on',!S.mobile);document.getElementById('p-mob').classList.toggle('on',S.mobile);document.getElementById('s-mode').textContent=S.mobile?'MOBILE':'DESKTOP'}S.ua=res.ua||'';document.getElementById('ua-cur').textContent=S.ua?'Current: '+S.ua.substring(0,80)+'...':''},2000);(async()=>{try{const r=await fetch('/hw_info',{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});if(!r.ok)return;const h=await r.json();const el=document.getElementById('s-hw');el.textContent=`${h.gpu_accel?'⚡':'🖥'} ${h.gpu_accel?h.gpu:'CPU'} │ ${h.cpu_cores}c │ ${h.ram_gb}GB │ ${h.active_fps}fps │ CDP`;el.style.color=h.gpu_accel?'var(--acc)':'var(--warn)'}catch{}})();let tx0=0,ty0=0,ty_l=0,t0=0,sw=false,lsc=0;const SP=8,TH=60;function coords(cx,cy){const r=feed.getBoundingClientRect();return{x:(cx-r.left)*(S.vpW/r.width),y:(cy-r.top)*(S.vpH/r.height)}}feed.addEventListener('click',e=>{if(S.lastTouch){S.lastTouch=false;return}const{x,y}=coords(e.clientX,e.clientY);api('/interact',{type:'click',x,y})});feed.addEventListener('touchstart',e=>{e.preventDefault();S.lastTouch=true;const t=e.touches[0];tx0=t.clientX;ty0=ty_l=t.clientY;t0=Date.now();sw=false;lsc=0},{passive:false});feed.addEventListener('touchmove',e=>{e.preventDefault();const t=e.touches[0];const dy=ty0-t.clientY,dx=tx0-t.clientX;if(!sw&&(Math.abs(dy)>SP||Math.abs(dx)>SP))sw=true;if(!sw||Math.abs(dy)<Math.abs(dx))return;const now=Date.now();if(now-lsc<TH)return;lsc=now;const delta=ty_l-t.clientY;ty_l=t.clientY;if(Math.abs(delta)<2)return;const r=feed.getBoundingClientRect();api('/interact',{type:'touch_scroll',dy:delta*(S.vpH/r.height)})},{passive:false});feed.addEventListener('touchend',e=>{e.preventDefault();if(!sw&&Date.now()-t0<400){const{x,y}=coords(tx0,ty0);api('/interact',{type:'click',x,y})}sw=false},{passive:false});document.addEventListener('keydown',e=>{if(document.activeElement===addr)return;if(document.activeElement&&document.activeElement.id==='kb-in')return;const sp=['Enter','Backspace','Tab','Escape','Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'];const k=sp.includes(e.key)?e.key:(e.key.length===1?e.key:null);if(k){e.preventDefault();api('/interact',{type:'key',key:k})}});function openTB(){document.getElementById('tbx').classList.add('open');document.getElementById('bk').classList.add('show')}function closeTB(){document.getElementById('tbx').classList.remove('open');document.getElementById('bk').classList.remove('show')}function tab(t){['ua','view','kb','src'].forEach((n,i)=>{document.querySelectorAll('.tab')[i].classList.toggle('on',n===t);document.getElementById('t-'+n).classList.toggle('on',n===t)})}function setUA(k){const ua=UAS[k]||'';document.getElementById('ua-custom').value=ua;S.ua=ua;if(['ip','an'].includes(k))setMode('mobile');api('/action',{action:'change_ua',data:{ua}})}function applyUA(){const ua=document.getElementById('ua-custom').value.trim();if(!ua)return;S.ua=ua;api('/action',{action:'change_ua',data:{ua}})}function setMode(m){S.mobile=m==='mobile';document.getElementById('p-desk').classList.toggle('on',!S.mobile);document.getElementById('p-mob').classList.toggle('on',S.mobile);document.getElementById('s-mode').textContent=S.mobile?'MOBILE':'DESKTOP';if(addr.value)go()}function applyMobPre(){if(document.getElementById('mob-pre').value)setMode('mobile')}document.getElementById('spd').addEventListener('input',function(){S.scrollPx=parseInt(this.value);document.getElementById('spd-val').textContent=this.value});function scrollUp(){api('/interact',{type:'scroll_up',px:S.scrollPx})}function scrollDown(){api('/interact',{type:'scroll_down',px:S.scrollPx})}async function fetchSrc(){document.getElementById('src-box').textContent='Fetching...';await api('/action',{action:'get_source'});await new Promise(r=>setTimeout(r,700));const res=await api('/get_state',{});if(res&&res.source){S.srcCache=res.source;document.getElementById('src-box').textContent=res.source}else document.getElementById('src-box').textContent='No source available.'}function copySrc(){if(!S.srcCache){alert('Fetch first.');return}navigator.clipboard.writeText(S.srcCache).then(()=>alert('Copied!'))}function dlSrc(){if(!S.srcCache){alert('Fetch first.');return}const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([S.srcCache],{type:'text/html'}));a.download='source_'+Date.now()+'.html';a.click()}function setOpacity(v){v=Math.max(15,Math.min(100,parseInt(v)));document.getElementById('op-rng').value=v;document.getElementById('op-bar').style.width=v+'%';document.getElementById('op-val').textContent=v+'%';const p=document.getElementById('tbx');p.style.opacity=(v/100).toFixed(2);p.style.pointerEvents=v<30?'none':'auto';document.querySelectorAll('.op-btn').forEach((b,i)=>b.classList.toggle('on',[30,55,80,100][i]===v))}(function(){const ROWS=[['`','1','2','3','4','5','6','7','8','9','0','-','='],['q','w','e','r','t','y','u','i','o','p','[',']','\\'],['a','s','d','f','g','h','j','k','l',';',"'"],['z','x','c','v','b','n','m',',','.','/']];const SHF={'`':'~','1':'!','2':'@','3':'#','4':'$','5':'%','6':'^','7':'&','8':'*','9':'(','0':')','-':'_','=':'+','[':'{',']':'}','\\':'|',';':':',"'":'"',',':'<','.':'>','/':'?'};const SPEC=[{l:'TAB',k:'Tab',c:'w sk'},{l:'CAPS',k:'CapsLock',c:'w sk'},{l:'SHIFT',k:'Shift',c:'w sk'},{l:'CTRL',k:'Control',c:'w sk'},{l:'ALT',k:'Alt',c:'w sk'},{l:'ESC',k:'Escape',c:'w sk dk'},{l:'⌫',k:'Backspace',c:'w dk'},{l:'↵',k:'Enter',c:'w sk'},{l:'␣ SPACE',k:' ',c:'sp'},{l:'DEL',k:'Delete',c:'w dk'},{l:'↑',k:'ArrowUp',c:'sk'},{l:'↓',k:'ArrowDown',c:'sk'},{l:'←',k:'ArrowLeft',c:'sk'},{l:'→',k:'ArrowRight',c:'sk'}];let shft=false,caps=false,mods={Shift:false,Control:false,Alt:false};const rows=document.getElementById('kb-rows'),spec=document.getElementById('kb-spec');function send(k){let ch='';if(mods.Control)ch+='Control+';if(mods.Alt)ch+='Alt+';if(mods.Shift||shft)ch+='Shift+';api('/interact',{type:'key',key:ch?ch+k:k});document.querySelectorAll('.key').forEach(b=>{if(b.dataset.k===k){b.classList.add('hit');setTimeout(()=>b.classList.remove('hit'),150)}});if(mods.Control){mods.Control=false;updM('Control')}if(mods.Alt){mods.Alt=false;updM('Alt')}if(shft&&!caps){shft=false;build();updM('Shift')}}function updM(n){document.querySelectorAll('.key').forEach(b=>{if(b.dataset.k===n){const on=mods[n]||(n==='Shift'&&shft)||(n==='CapsLock'&&caps);b.style.background=on?'var(--acc2)':'';b.style.color=on?'#000':'';b.style.borderColor=on?'var(--acc2)':''}})}function build(){rows.innerHTML='';ROWS.forEach(row=>{const d=document.createElement('div');d.className='kb-row';row.forEach(k=>{const b=document.createElement('button');b.className='key';b.dataset.k=k;b.textContent=(caps||shft)?(SHF[k]||k.toUpperCase()):k;b.addEventListener('pointerdown',ev=>{ev.preventDefault();send((caps||shft)?(SHF[k]||k.toUpperCase()):k)});d.appendChild(b)});rows.appendChild(d)})}SPEC.forEach(s=>{const b=document.createElement('button');b.className='key '+(s.c||'');b.dataset.k=s.k;b.textContent=s.l;b.addEventListener('pointerdown',ev=>{ev.preventDefault();if(s.k==='CapsLock'){caps=!caps;updM('CapsLock');build()}else if(s.k==='Shift'){shft=!shft;updM('Shift');build()}else if(['Control','Alt'].includes(s.k)){mods[s.k]=!mods[s.k];updM(s.k)}else send(s.k)});spec.appendChild(b)});build();document.getElementById('kb-in').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();kbSend()}e.stopPropagation()})})();function kbSend(){const el=document.getElementById('kb-in'),txt=el.value;if(!txt)return;[...txt].forEach((c,i)=>setTimeout(()=>api('/interact',{type:'key',key:c}),i*30));setTimeout(()=>{el.value='';el.focus()},txt.length*30+50)}</script></body></html>"""

@app.route("/")
@login_required
def index(): return Response(APP_HTML,mimetype="text/html")
@app.route("/login",methods=["GET"])
def login_page():
    if session.get("authenticated"): return redirect(url_for("index"))
    csrf=secrets.token_hex(16); session["csrf"]=csrf
    return render_template_string(LOGIN_HTML,error=None,csrf=csrf)
@app.route("/login",methods=["POST"])
def login_post():
    _check_body(); ip=_real_ip()
    if _rate_limited(ip):
        csrf=secrets.token_hex(16); session["csrf"]=csrf
        return render_template_string(LOGIN_HTML,error="Too many attempts. Wait 60s.",csrf=csrf),429
    fc=request.form.get("csrf_token","")
    if not fc or fc!=session.get("csrf"):
        csrf=secrets.token_hex(16); session["csrf"]=csrf
        return render_template_string(LOGIN_HTML,error="Invalid request.",csrf=csrf),400
    u=html.escape(request.form.get("username","").strip()); p=request.form.get("password","").strip()
    if check_auth(u,p):
        session.clear(); session.permanent=True; session["authenticated"]=True; session["username"]=u
        return redirect(url_for("index"))
    _record(ip); csrf=secrets.token_hex(16); session["csrf"]=csrf
    return render_template_string(LOGIN_HTML,error="Invalid credentials.",csrf=csrf),401
@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login_page"))
@app.route("/load_status")
@login_required
def load_status():
    return jsonify(worker.get_load_status())
@app.route("/video_feed")
@login_required
def video_feed():
    def gen():
        last=None
        while True:
            # FIX: Reduced wait from 0.15 to 0.05 for faster frame delivery
            FRAME_EVENT.wait(timeout=0.05); FRAME_EVENT.clear()
            with FRAME_LOCK: frame=LATEST_FRAME
            if frame is not None and frame is not last:
                last=frame
                yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'+frame+b'\r\n'
    return Response(gen(),mimetype='multipart/x-mixed-replace; boundary=frame',headers={"Cache-Control":"no-cache,no-store","X-Accel-Buffering":"no"})
@app.route("/navigate",methods=["POST"])
@login_required
def navigate():
    _check_body(); data=request.get_json(silent=True) or {}
    data["url"]=_sanitize_url(data.get("url",""))
    if "cookies" in data and len(str(data["cookies"]))>8192: data.pop("cookies")
    worker.send("navigate",data); return jsonify({"status":"ok"})
@app.route("/action",methods=["POST"])
@login_required
def action():
    _check_body(); data=request.get_json(silent=True) or {}
    act=data.get("action","")
    if act not in {"go_back","go_forward","reload","get_source","change_ua"}: return jsonify({"error":"unknown"}),400
    inner=data.get("data",{})
    if act=="change_ua": inner={"ua":re.sub(r'[\x00-\x1f\x7f]','',str(inner.get("ua","")).strip())[:512]}
    worker.send(act,inner); return jsonify({"status":"ok"})
@app.route("/interact",methods=["POST"])
@login_required
def interact():
    _check_body(); data=request.get_json(silent=True) or {}
    t=data.get("type")
    if t not in {"click","key","scroll_up","scroll_down","touch_scroll"}: return jsonify({"error":"invalid"}),400
    if t=="click":
        try: data["x"]=max(0.0,min(float(data.get("x",0)),4096.0)); data["y"]=max(0.0,min(float(data.get("y",0)),4096.0))
        except: return jsonify({"error":"bad coords"}),400
    elif t=="key": data["key"]=str(data.get("key",""))[:64]
    elif t in ("scroll_up","scroll_down"):
        try: data["px"]=max(10,min(int(data.get("px",400)),2000))
        except: data["px"]=400
    elif t=="touch_scroll":
        try: data["dy"]=max(-2000.0,min(float(data.get("dy",0)),2000.0))
        except: data["dy"]=0.0
    worker.send("interact",data); return jsonify({"status":"sent"})
@app.route("/get_state",methods=["POST"])
@login_required
def get_state():
    _check_body(); data=request.get_json(silent=True) or {}
    return jsonify(worker.get_state(lite=bool(data.get("lite"))))
@app.route("/hw_info")
@login_required
def hw_info():
    try: import psutil; ram=round(psutil.virtual_memory().total/(1024**3),1)
    except: ram="?"
    return jsonify({"gpu":GPU_VENDOR if HAS_GPU else "CPU","gpu_accel":HAS_GPU,"cpu_cores":CPU_CORES,"ram_gb":ram,
        "active_fps":CDPWorker.ACTIVE_FPS,"idle_fps":CDPWorker.IDLE_FPS,"engine":"CDP","chrome":worker._chrome_ver,
        "container":IN_CONTAINER})
@app.route("/debug_cdp")
@login_required
def debug_cdp():
    return jsonify({"ws_connected":worker._ws_connected,"session_id":worker._session_id,"target_id":worker._target_id,
        "chrome_ver":worker._chrome_ver,"chrome_bin":CHROME_BIN,"frame_count":worker._frame_n,
        "has_frame":LATEST_FRAME is not None,"frame_size":len(LATEST_FRAME) if LATEST_FRAME else 0,
        "navigating":worker._navigating,"pending_requests":worker._pending_requests,
        "load_status":worker.get_load_status(),
        "chrome_stderr":worker._chrome_stderr[:500] if worker._chrome_stderr else "",
        "container":IN_CONTAINER})

if __name__=="__main__":
    try: import psutil
    except: pass
    os.system('cls' if os.name=='nt' else 'clear')
    banner=pyfiglet.figlet_format("QuantumSurf",font="slant")
    print(colored(banner,"cyan"))
    print(colored("="*60,"cyan"))
    print(colored("  QuantumSurf — Made by Aryan Giri","yellow",attrs=["bold"]))
    print(colored("  Privacy Toolkit — Remote Browser Isolation","white"))
    print(colored("  Engine: Direct CDP · No Playwright · No Snap","green"))
    print(colored("="*60,"cyan"))
    print(colored(f"  Chrome : {CHROME_BIN or 'NOT FOUND'}","green" if CHROME_BIN else "red"))
    print(colored(f"  GPU    : {'YES ('+GPU_VENDOR+')' if HAS_GPU else 'No — CPU mode'}","green" if HAS_GPU else "yellow"))
    print(colored(f"  CPU    : {CPU_CORES} cores","white"))
    print(colored(f"  Container: {'YES' if IN_CONTAINER else 'No'}","yellow" if IN_CONTAINER else "white"))
    print(colored(f"  FPS    : {CDPWorker.ACTIVE_FPS} active / {CDPWorker.IDLE_FPS} idle","white"))
    print(colored(f"  Debug  : {'ON' if DEBUG_CDP else 'OFF (QS_DEBUG=1 to enable)'}","white"))
    print(colored("="*60+"\n","cyan"))
    print(colored("[*] Default credentials is in auth.txt","magenta"))
    print(colored("[*] http://0.0.0.0:8000\n","green"))
    if not CHROME_BIN:
        print(colored("[!] FATAL: No working Chrome/Chromium!","red"))
        print(colored("    Run: sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 libnss3 libxshmfence1","yellow"))
        exit(1)
    app.run(host="0.0.0.0",port=8000,threaded=True)
