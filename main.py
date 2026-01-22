#!/usr/bin/env python3
"""
QuantumSurf ( https://github.com/giriaryan694-a11y/QuantumSurf/ )
Made by Aryan Giri
"""

import os
import json
import time
import threading
import queue
import traceback
from pathlib import Path
from functools import wraps
import copy

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

# Stealth
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
    print(colored("  Development Build (Stable)", "white"))
    print(colored("="*60 + "\n", "cyan"))

# ---------------- Browser Worker ----------------
class BrowserWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.command_queue = queue.Queue()
        self.active_page = None
        self.browser = None
        self.context = None
        self.running = True
        self.current_ua = ""
        self.is_mobile = False
        self.state_lock = threading.Lock()
        self.cached_state = {"url": "", "ua": "", "mobile": False, "cookies": []}

    def run(self):
        if not PLAYWRIGHT_AVAILABLE: return
        with sync_playwright() as p:
            self.browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-infobars"])
            print(colored("[*] Browser Engine Started", "green"))
            while self.running:
                while not self.command_queue.empty():
                    cmd = self.command_queue.get()
                    try:
                        self.process_command(cmd)
                        self.update_cached_state() 
                    except Exception as e:
                        print(colored(f"[!] Cmd Error: {e}", "red"))
                    self.command_queue.task_done()
                if self.active_page:
                    try:
                        screenshot_bytes = self.active_page.screenshot(type="jpeg", quality=60)
                        with FRAME_LOCK:
                            global LATEST_FRAME
                            LATEST_FRAME = screenshot_bytes
                        self.update_cached_state()
                    except Exception: pass
                time.sleep(0.05)

    def update_cached_state(self):
        try:
            if self.active_page:
                url = self.active_page.url
                cookies = self.context.cookies()
                with self.state_lock:
                    self.cached_state["url"] = url
                    self.cached_state["cookies"] = cookies
                    self.cached_state["ua"] = self.current_ua
                    self.cached_state["mobile"] = self.is_mobile
        except: pass

    def process_command(self, cmd):
        action = cmd["action"]
        data = cmd.get("data", {})
        if action == "navigate":
            raw_url = data["url"].strip()
            url = raw_url if "://" in raw_url else "https://" + raw_url
            if self.context: self.context.close()
            mode = data.get("mobile", False)
            if mode:
                vp, ua = {"width": 375, "height": 667}, "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Mobile/15E148 Safari/604.1"
            else:
                vp, ua = {"width": 1280, "height": 720}, data.get("ua") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            self.current_ua, self.is_mobile = ua, mode
            self.context = self.browser.new_context(viewport=vp, user_agent=ua, has_touch=mode, is_mobile=mode, ignore_https_errors=True)
            if data.get("cookies"):
                try: self.context.add_cookies(json.loads(data["cookies"]))
                except: pass
            self.active_page = self.context.new_page()
            if STEALTH_AVAILABLE and not mode: stealth_sync(self.active_page)
            try: self.active_page.goto(url, timeout=15000, wait_until="commit")
            except: pass
        elif action == "go_back": self.active_page.go_back() if self.active_page else None
        elif action == "go_forward": self.active_page.go_forward() if self.active_page else None
        elif action == "interact":
            if not self.active_page: return
            t = data["type"]
            if t == "click": self.active_page.mouse.click(data["x"], data["y"])
            elif t == "key": 
                if len(data["key"]) == 1: self.active_page.keyboard.type(data["key"])
                else: self.active_page.keyboard.press(data["key"])
            elif t == "scroll_down": self.active_page.mouse.wheel(0, 400)
            elif t == "scroll_up": self.active_page.mouse.wheel(0, -400)

    def get_safe_state(self):
        with self.state_lock: return copy.deepcopy(self.cached_state)
    def send_cmd(self, action, data=None): self.command_queue.put({"action": action, "data": data or {}})

worker = BrowserWorker()
if PLAYWRIGHT_AVAILABLE: worker.start()

def check_auth(u, p):
    if not AUTH_FILE.exists(): return u == "test" and p == "test"
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
        if auth and check_auth(auth.username, auth.password): return fn(*args, **kwargs)
        j = request.get_json(silent=True)
        if j and check_auth(j.get("user"), j.get("pass")): return fn(*args, **kwargs)
        return jsonify({"error": "Unauthorized"}), 401
    return wrapper

app = Flask(__name__)

HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>QuantumSurf - Made by Aryan Giri</title>
<style>
    body { background-color:#121212; color:#0f0; font-family: monospace; margin:0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
    #header { background: #000; padding: 10px; text-align: center; border-bottom: 2px solid #0f0; color: #ffcc00; }
    #toolbar { background: #1e1e1e; padding: 10px; display: flex; gap: 8px; align-items: center; border-bottom: 1px solid #333; }
    input, button { background: #333; color: #fff; border: 1px solid #555; padding: 6px; border-radius: 4px; }
    button { cursor: pointer; font-weight: bold; }
    button:hover { background: #444; }
    #viewport-container { flex-grow: 1; background: #000; display: flex; justify-content: center; overflow: auto; padding: 10px; }
    #live-feed { max-width: 100%; border: 1px solid #444; cursor: crosshair; object-fit: contain; }
    #overlay { display: none; position: fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); z-index: 99; }
    #config-modal { display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: #222; border: 1px solid #0f0; padding: 20px; z-index: 100; width: 80%; max-width: 500px; }
    textarea { width: 100%; height: 80px; background: #111; color: #0f0; border: 1px solid #555; margin-bottom: 10px; }
</style>
</head>
<body>
<div id="header"><h3>QuantumSurf - Made by Aryan Giri</h3></div>
<div id="toolbar">
    <input id="user" placeholder="User" style="width:70px">
    <input id="pass" type="password" placeholder="Pass" style="width:70px">
    <button onclick="goBack()">◀</button>
    <button onclick="goForward()">▶</button>
    <input id="url" placeholder="google.com" style="flex-grow: 1;">
    <button onclick="navigate()" style="background:#005500">GO LIVE</button>
    <button onclick="openConfig()">Config</button>
    <button onclick="scrollUp()">▲</button>
    <button onclick="scrollDown()">▼</button>
</div>
<div id="viewport-container"><img id="live-feed"></div>
<div id="overlay" onclick="closeConfig()"></div>
<div id="config-modal">
    <h4>Settings</h4>
    <label><input type="checkbox" id="mobile-view"> Mobile Mode</label><br><br>
    <label>User Agent:</label><textarea id="custom-ua"></textarea>
    <label>Cookies (JSON):</label><textarea id="custom-cookies"></textarea>
    <button onclick="saveAndReload()">Save & Reload</button>
</div>
<script>
    const feed = document.getElementById('live-feed');
    const urlInput = document.getElementById('url');
    let syncLocked = false;

    async function api(endpoint, data) {
        data.user = document.getElementById('user').value;
        data.pass = document.getElementById('pass').value;
        try {
            let r = await fetch(endpoint, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
            return await r.json();
        } catch(e) { return null; }
    }

    // URL Sync Loop
    setInterval(async () => {
        if (syncLocked || document.activeElement === urlInput) return;
        let res = await api('/get_state', {});
        if(res && res.url && res.url !== "about:blank") urlInput.value = res.url;
    }, 2000);

    function navigate() {
        let u = document.getElementById('user').value, p = document.getElementById('pass').value;
        if(!u || !p) return alert("Enter User/Pass");
        
        // Lock Sync so the UI doesn't snap back while loading
        syncLocked = true;
        api('/navigate', { 
            url: urlInput.value, 
            ua: document.getElementById('custom-ua').value, 
            cookies: document.getElementById('custom-cookies').value, 
            mobile: document.getElementById('mobile-view').checked 
        });
        feed.src = `/video_feed?t=${Date.now()}&u=${u}&p=${p}`;
        
        // Unlock after 5 seconds
        setTimeout(() => { syncLocked = false; }, 5000);
    }

    function goBack() { api('/action', { action: 'go_back' }); }
    function goForward() { api('/action', { action: 'go_forward' }); }
    function scrollUp() { api('/interact', { type: 'scroll_up' }); }
    function scrollDown() { api('/interact', { type: 'scroll_down' }); }
    
    async function openConfig() {
        document.getElementById('config-modal').style.display = 'block';
        document.getElementById('overlay').style.display = 'block';
        let res = await api('/get_state', {});
        if(res) {
            document.getElementById('custom-ua').value = res.ua;
            document.getElementById('custom-cookies').value = JSON.stringify(res.cookies, null, 2);
            document.getElementById('mobile-view').checked = res.mobile;
        }
    }
    function closeConfig() { document.getElementById('config-modal').style.display = 'none'; document.getElementById('overlay').style.display = 'none'; }
    function saveAndReload() { closeConfig(); navigate(); }

    feed.addEventListener('click', (e) => {
        let rect = feed.getBoundingClientRect();
        let nw = document.getElementById('mobile-view').checked ? 375 : 1280;
        let nh = document.getElementById('mobile-view').checked ? 667 : 720;
        api('/interact', { type: 'click', x: (e.clientX - rect.left) * (nw / rect.width), y: (e.clientY - rect.top) * (nh / rect.height) });
    });
</script>
</body>
</html>
"""

@app.route("/")
def index(): return Response(HTML_PAGE, mimetype="text/html")

@app.route("/video_feed")
def video_feed():
    u, p = request.args.get("u"), request.args.get("p")
    if not check_auth(u, p): return Response("Unauthorized", 401)
    def gen():
        while True:
            with FRAME_LOCK: frame = LATEST_FRAME
            if frame: yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.05)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route("/navigate", methods=["POST"])
@require_auth
def navigate():
    worker.send_cmd("navigate", request.get_json())
    return jsonify({"status": "ok"})

@app.route("/action", methods=["POST"])
@require_auth
def action():
    worker.send_cmd(request.get_json()["action"])
    return jsonify({"status": "ok"})

@app.route("/get_state", methods=["POST"])
@require_auth
def get_state(): return jsonify(worker.get_safe_state())

@app.route("/interact", methods=["POST"])
@require_auth
def interact():
    worker.send_cmd("interact", request.get_json())
    return jsonify({"status": "sent"})

if __name__ == "__main__":
    print_banner()
    print(colored("Default user:pass is test:test", "magenta"))
    app.run(host="0.0.0.0", port=8000, threaded=True)
