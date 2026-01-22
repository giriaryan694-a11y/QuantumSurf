#!/usr/bin/env python3
"""
QuantumSurf - https://github.com/giriaryan694-a11y/QuantumSurf/
Made by Aryan Giri
Features:
 - Back / Forward Navigation
 - Auto-Sync URL
 - Live MJPEG Video Stream
 - Advanced Config (UserAgent, Cookies, Mobile View)
"""

import os
import json
import time
import threading
import queue
import traceback
from pathlib import Path
from functools import wraps

# Web / UI
from flask import Flask, request, Response, jsonify
import pyfiglet
from termcolor import colored
from colorama import init as colorama_init

# Playwright
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Stealth (Optional)
try:
    from playwright_stealth import stealth_sync
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

# ---------------- Config ----------------
colorama_init(autoreset=True)
BASE = Path(__file__).parent.resolve()
AUTH_FILE = BASE / "auth.txt"

# ---------------- Global State ----------------
LATEST_FRAME = None
FRAME_LOCK = threading.Lock()

# ---------------- Startup Banner ----------------
def print_banner():
    os.system('cls' if os.name == 'nt' else 'clear')
    banner_text = pyfiglet.figlet_format("QuantumSurf", font="slant")
    print(colored(banner_text, "cyan"))
    print(colored("="*60, "cyan"))
    print(colored("  QuantumSurf - Made by Aryan Giri", "yellow", attrs=["bold"]))
    print(colored("  Development Build", "white"))
    print(colored("="*60 + "\n", "cyan"))

    if not STEALTH_AVAILABLE:
        print(colored("[!] WARNING: 'playwright-stealth' not installed.", "yellow"))
    else:
        print(colored("[*] Stealth Mode: ACTIVATED", "green"))

# ---------------- Browser Worker ----------------
class BrowserWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.command_queue = queue.Queue()
        self.active_page = None
        self.browser = None
        self.context = None
        self.running = True
        
        # State tracking
        self.current_ua = ""
        self.is_mobile = False
        self.last_known_url = ""

    def run(self):
        if not PLAYWRIGHT_AVAILABLE: return

        with sync_playwright() as p:
            self.browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-infobars"]
            )
            print(colored("[*] Browser Engine Started", "green"))

            while self.running:
                # 1. Process Commands
                while not self.command_queue.empty():
                    cmd = self.command_queue.get()
                    try:
                        self.process_command(cmd)
                    except Exception as e:
                        print(colored(f"[!] Cmd Error: {e}", "red"))
                    self.command_queue.task_done()

                # 2. Capture Frame & State
                if self.active_page:
                    try:
                        self.last_known_url = self.active_page.url
                        screenshot_bytes = self.active_page.screenshot(type="jpeg", quality=60)
                        with FRAME_LOCK:
                            global LATEST_FRAME
                            LATEST_FRAME = screenshot_bytes
                    except Exception:
                        pass
                
                time.sleep(0.05)

    def process_command(self, cmd):
        action = cmd["action"]
        data = cmd.get("data", {})

        if action == "navigate":
            url = data["url"]
            ua = data.get("ua")
            cookies_json = data.get("cookies")
            mobile_mode = data.get("mobile", False)

            needs_new_context = False
            if not self.context: needs_new_context = True
            if ua and ua != self.current_ua: needs_new_context = True
            if mobile_mode != self.is_mobile: needs_new_context = True

            if needs_new_context:
                if self.context:
                    try: self.context.close()
                    except: pass
                
                if mobile_mode:
                    viewport = {"width": 375, "height": 667}
                    default_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Mobile/15E148 Safari/604.1"
                    has_touch = True
                else:
                    viewport = {"width": 1280, "height": 720}
                    default_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
                    has_touch = False

                final_ua = ua if ua and ua.strip() else default_ua
                self.current_ua = final_ua
                self.is_mobile = mobile_mode

                self.context = self.browser.new_context(
                    viewport=viewport,
                    user_agent=final_ua,
                    has_touch=has_touch,
                    is_mobile=mobile_mode
                )
                
                if cookies_json:
                    try:
                        cookies_list = json.loads(cookies_json)
                        if isinstance(cookies_list, list):
                            for c in cookies_list:
                                if "domain" not in c:
                                    try: c["domain"] = url.split("://")[1].split("/")[0]
                                    except: pass
                            self.context.add_cookies(cookies_list)
                    except: pass
                
                self.active_page = self.context.new_page()
                if STEALTH_AVAILABLE and not mobile_mode:
                    stealth_sync(self.active_page)

            try:
                self.active_page.goto(url, timeout=15000, wait_until="commit")
            except: pass

        elif action == "go_back":
            if self.active_page:
                try: self.active_page.go_back()
                except: pass

        elif action == "go_forward":
            if self.active_page:
                try: self.active_page.go_forward()
                except: pass

        elif action == "interact":
            if not self.active_page: return
            t = data["type"]
            if t == "click":
                try: self.active_page.mouse.click(data["x"], data["y"])
                except: pass
            elif t == "key":
                k = data["key"]
                if len(k) == 1: self.active_page.keyboard.type(k)
                else: 
                    try: self.active_page.keyboard.press(k)
                    except: pass
            elif t == "scroll_down":
                self.active_page.mouse.wheel(0, 400)
            elif t == "scroll_up":
                self.active_page.mouse.wheel(0, -400)

    def get_live_state(self):
        cookies = []
        if self.context:
            try: cookies = self.context.cookies()
            except: pass
        
        return {
            "url": self.last_known_url,
            "ua": self.current_ua,
            "mobile": self.is_mobile,
            "cookies": cookies
        }

    def send_cmd(self, action, data=None):
        self.command_queue.put({"action": action, "data": data or {}})

worker = BrowserWorker()
if PLAYWRIGHT_AVAILABLE:
    worker.start()

# ---------------- Auth ----------------
def check_auth(u, p):
    # Default to test:test if file doesn't exist
    if not AUTH_FILE.exists():
        return u == "test" and p == "test"
        
    with open(AUTH_FILE, "r") as f:
        for line in f:
            if ":" in line:
                user, pw = line.strip().split(":", 1)
                if user == u and pw == p: return True
    return False

def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if auth and check_auth(auth.username, auth.password):
            return fn(*args, **kwargs)
        j = request.get_json(silent=True) or {}
        if check_auth(j.get("user"), j.get("pass")):
             return fn(*args, **kwargs)
        return jsonify({"error": "Unauthorized"}), 401
    return wrapper

# ---------------- Flask App ----------------
app = Flask(__name__)

HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QuantumSurf - Made by Aryan Giri</title>
<style>
    /* Reset & Base */
    * { box-sizing: border-box; }
    body { background-color:#121212; color:#0f0; font-family: 'Courier New', monospace; margin:0; padding:0; height: 100vh; display: flex; flex-direction: column; }
    
    /* Header */
    #header { 
        background: #000; 
        color: #ffcc00; 
        padding: 10px; 
        text-align: center; 
        border-bottom: 2px solid #0f0;
        flex-shrink: 0;
    }
    #header h3 { margin: 0; font-size: 1.5rem; text-shadow: 0 0 5px #ff0000; font-weight: bold; text-transform: uppercase; }

    /* Toolbar */
    #toolbar { 
        background: #1e1e1e; 
        padding: 10px; 
        border-bottom: 1px solid #333; 
        display: flex; 
        flex-wrap: wrap; 
        gap: 8px; 
        align-items: center;
        flex-shrink: 0;
    }
    input { background: #333; color: #fff; border: 1px solid #555; padding: 6px; border-radius: 4px; }
    button { cursor:pointer; background:#005500; color:#fff; border:none; padding:6px 12px; border-radius: 4px; font-weight: bold;}
    button:hover { background: #007700; }
    button.alt { background: #004466; }
    button.alt:hover { background: #006699; }
    button.nav { background: #444; font-size: 1.1rem; padding: 4px 10px; }
    button.nav:hover { background: #666; }
    
    /* Responsive Viewport */
    #viewport-container {
        flex-grow: 1;
        background: #000;
        display: flex;
        justify-content: center;
        align-items: flex-start;
        overflow: auto;
        padding: 10px;
    }
    
    #live-feed { 
        max-width: 100%; 
        max-height: 100%; 
        border: 2px solid #444; 
        display: block; 
        cursor: crosshair;
        object-fit: contain;
    }

    /* Config Modal */
    #config-modal {
        display: none;
        position: fixed;
        top: 50%; left: 50%;
        transform: translate(-50%, -50%);
        background: #222;
        border: 2px solid #0f0;
        padding: 20px;
        z-index: 1000;
        width: 90%;
        max-width: 600px;
        box-shadow: 0 0 20px rgba(0,255,0,0.2);
    }
    #config-modal h4 { margin-top: 0; color: #fff; border-bottom: 1px solid #555; padding-bottom: 5px; }
    #config-modal label { display: block; margin-top: 10px; color: #ccc; font-weight: bold; }
    #config-modal textarea { width: 100%; height: 100px; background: #111; color: #0f0; border: 1px solid #555; font-family: monospace; font-size: 12px; padding: 5px; }
    #overlay { display: none; position: fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.7); z-index: 999; }
    
    .status-bar { color: #888; font-size: 0.8rem; margin-left: auto; }
</style>
</head>
<body>

<div id="header">
    <h3>QuantumSurf - Made by Aryan Giri</h3>
</div>

<div id="toolbar">
    <input id="user" value="" style="width:70px" placeholder="User">
    <input id="pass" type="password" value="" style="width:70px" placeholder="Pass">
    
    <button class="nav" onclick="goBack()" title="Back">◀</button>
    <button class="nav" onclick="goForward()" title="Forward">▶</button>
    
    <input id="url" placeholder="https://google.com" style="flex-grow: 1; min-width: 150px;">
    
    <button onclick="navigate()">GO LIVE</button>
    <button class="alt" onclick="openConfig()">Config</button>
    <button onclick="sendKey('Enter')">Enter</button>
    <button onclick="scrollUp()">▲ Up</button>
    <button onclick="scrollDown()">▼ Down</button>
    
    <span id="status" class="status-bar">Ready</span>
</div>

<div id="viewport-container">
    <img id="live-feed" draggable="false">
</div>

<div id="overlay" onclick="closeConfig()"></div>
<div id="config-modal">
    <h4>Configuration & State</h4>
    <label><input type="checkbox" id="mobile-view"> <b>Mobile View (iPhone Mode)</b></label>
    <label>Current User Agent (Edit to Change)</label>
    <textarea id="custom-ua" placeholder="Loading..."></textarea>
    <label>Live Cookies (JSON) - Edit to Inject</label>
    <textarea id="custom-cookies" placeholder="Loading..."></textarea>
    <div style="margin-top: 15px; display: flex; justify-content: space-between;">
        <span style="color: #666; font-size: 12px;">Changes apply on "Save & Reload"</span>
        <button onclick="saveAndReload()">Save & Reload</button>
    </div>
</div>

<script>
    const feed = document.getElementById('live-feed');
    const urlInput = document.getElementById('url');
    let syncInterval = null;

    async function api(endpoint, data) {
        data.user = document.getElementById('user').value;
        data.pass = document.getElementById('pass').value;
        try {
            let r = await fetch(endpoint, {
                method: 'POST', 
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            return await r.json();
        } catch(e) { console.error(e); return null; }
    }

    function startSync() {
        if(syncInterval) clearInterval(syncInterval);
        syncInterval = setInterval(async () => {
            let res = await api('/get_state', {});
            if(res && res.url) {
                if (document.activeElement !== urlInput) {
                    if (urlInput.value !== res.url && res.url !== "about:blank") {
                        urlInput.value = res.url;
                    }
                }
            }
        }, 1000);
    }

    function navigate() {
        let u = document.getElementById('user').value;
        let p = document.getElementById('pass').value;
        let url = urlInput.value;
        let ua = document.getElementById('custom-ua').value;
        let cookies = document.getElementById('custom-cookies').value;
        let mobile = document.getElementById('mobile-view').checked;

        if(!u || !p) return alert("Please enter User and Pass");
        if(!url) return alert("Enter URL");
        
        document.getElementById('status').innerText = "Loading...";
        
        api('/navigate', { 
            url: url, ua: ua, cookies: cookies, mobile: mobile
        });
        
        feed.src = `/video_feed?t=${Date.now()}&u=${u}&p=${p}`;
        document.getElementById('status').innerText = mobile ? "LIVE (Mobile)" : "LIVE (Desktop)";
        
        startSync();
    }

    function goBack() { api('/action', { action: 'go_back' }); }
    function goForward() { api('/action', { action: 'go_forward' }); }
    function scrollUp() { api('/interact', { type: 'scroll_up' }); }
    function scrollDown() { api('/interact', { type: 'scroll_down' }); }
    function sendKey(k) { api('/interact', { type: 'key', key: k }); }

    async function openConfig() {
        document.getElementById('config-modal').style.display = 'block';
        document.getElementById('overlay').style.display = 'block';
        let res = await api('/get_state', {});
        if (res) {
            document.getElementById('custom-ua').value = res.ua || "";
            document.getElementById('mobile-view').checked = res.mobile || false;
            document.getElementById('custom-cookies').value = JSON.stringify(res.cookies || [], null, 2);
        }
    }
    function closeConfig() {
        document.getElementById('config-modal').style.display = 'none';
        document.getElementById('overlay').style.display = 'none';
    }
    function saveAndReload() {
        closeConfig();
        navigate();
    }

    feed.addEventListener('click', (e) => {
        let rect = feed.getBoundingClientRect();
        let naturalWidth = document.getElementById('mobile-view').checked ? 375 : 1280;
        let naturalHeight = document.getElementById('mobile-view').checked ? 667 : 720;
        let scaleX = naturalWidth / rect.width;
        let scaleY = naturalHeight / rect.height;
        let x = (e.clientX - rect.left) * scaleX;
        let y = (e.clientY - rect.top) * scaleY;
        api('/interact', { type: 'click', x: x, y: y });
    });
    
    document.addEventListener('keydown', (e) => {
        if(['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) return;
        if(['ArrowUp','ArrowDown','Space'].includes(e.key)) e.preventDefault();
        api('/interact', { type: 'key', key: e.key });
    });
</script>
</body>
</html>
"""

# ---------------- Routes ----------------
@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html")

def generate_feed_frames():
    while True:
        with FRAME_LOCK: frame = LATEST_FRAME
        if frame:
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.05)

@app.route("/video_feed")
def video_feed():
    u, p = request.args.get("u"), request.args.get("p")
    if not check_auth(u, p): return Response("Unauthorized", 401)
    return Response(generate_feed_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/navigate", methods=["POST"])
@require_auth
def navigate():
    worker.send_cmd("navigate", request.get_json())
    return jsonify({"status": "ok"})

@app.route("/action", methods=["POST"])
@require_auth
def action():
    j = request.get_json()
    worker.send_cmd(j["action"], {})
    return jsonify({"status": "ok"})

@app.route("/get_state", methods=["POST"])
@require_auth
def get_state():
    return jsonify(worker.get_live_state())

@app.route("/interact", methods=["POST"])
@require_auth
def interact():
    worker.send_cmd("interact", request.get_json())
    return jsonify({"status": "sent"})

# ---------------- Run ----------------
if __name__ == "__main__":
    print_banner()
    
    print(colored("Default user:pass is test:test", "magenta"))
    print(colored("You can change it in auth.txt", "white"))
    
    if not AUTH_FILE.exists():
        print(colored("auth.txt not found - Using default 'test:test'", "yellow"))
    else:
        print(colored("Loaded auth.txt", "green"))
    
    app.run(host="0.0.0.0", port=8000, threaded=True)
