#!/usr/bin/env python3
"""
QuantumSurf — Remote Browser Isolation (Pure Chromium GUI via noVNC)
Made by Aryan Giri | giriaryan694-a11y
GitHub: https://github.com/giriaryan694-a11y

Architecture:
  Xvfb :99 → Chromium (HEADED, fills entire screen)
  x11vnc → mirrors :99 on 127.0.0.1:5901 (LOOPBACK ONLY)
  Flask :8000 (0.0.0.0) → auth gate + resolution API + serves noVNC UI
                          + WebSocket<->RFB bridge to 127.0.0.1:5901

FIXED: Stale Xvfb lock file /tmp/.X99-lock cleanup before start
FIXED: -listen tcp for Xvfb 21.1+ compatibility
"""
import os, re, json, time, html, hmac, hashlib, secrets, base64, tempfile, sys
import threading, shutil, subprocess, multiprocessing, signal, io, tarfile, socket
import urllib.request, zipfile, platform, ctypes.util
from pathlib import Path
from functools import wraps
from datetime import timedelta
from urllib.parse import urlparse, urlencode
from flask import (Flask, request, Response, jsonify, session, redirect,
                   url_for, render_template_string, abort, make_response,
                   send_from_directory)
import pyfiglet
from termcolor import colored
from colorama import init as colorama_init
colorama_init(autoreset=True)

try:
    from flask_sock import Sock
except ImportError:
    print(colored("[!] flask-sock is required for the secure VNC bridge.", "red"))
    print(colored("    Install it with:  pip install flask-sock", "yellow"))
    sys.exit(1)

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════
BASE = Path(__file__).parent.resolve()
AUTH_FILE = BASE / "auth.txt"
SECRET_KEY = secrets.token_hex(32)
SESSION_LIFE = timedelta(hours=4)
MAX_BODY = 32_768
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = 60
DEBUG = os.environ.get("QS_DEBUG", "0") == "1"

CHROME_DIR = BASE / ".chromium"
LIBS_DIR = CHROME_DIR / "libs"
NOVNC_DIR = BASE / ".novnc"

XVFB_DISPLAY = ":99"
XVFB_DISPLAY_NUM = 99  # Numeric part for lock file paths
VNC_PORT = 5901       # x11vnc — 127.0.0.1 ONLY, never exposed
NOVNC_PORT = 6080     # kept as a constant for reference only; nothing binds to it anymore
FLASK_PORT = 8000     # the only port that listens on 0.0.0.0

CURRENT_W = 1920
CURRENT_H = 1080
MIN_W, MIN_H = 800, 600
MAX_W, MAX_H = 3840, 2160

_login_attempts = {}
_attempts_lock = threading.Lock()
_auth_cache = {}
_auth_cache_ts = 0.0

PROCS = {}
NOVNC_WEB_ROOT = None
NOVNC_ENTRY = "vnc.html"
_resizer_running = False
_stack_lock = threading.Lock()

_stack_log = []
_stack_log_lock = threading.Lock()

def _log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] [{level}] {msg}"
    with _stack_log_lock:
        _stack_log.append(entry)
        if len(_stack_log) > 100: _stack_log.pop(0)
    if level == "ERROR": print(colored(f"  {entry}", "red"))
    elif level == "WARN": print(colored(f"  {entry}", "yellow"))
    else: print(colored(f"  {entry}", "green"))

# ═══════════════════════════════════════════════════════════
# SYSTEM DETECTION
# ═══════════════════════════════════════════════════════════
def _in_container():
    if Path("/.dockerenv").exists(): return True
    if Path("/run/.containerenv").exists(): return True
    try:
        cg = Path("/proc/1/cgroup").read_text()
        if any(k in cg for k in ("docker","kubepods","containerd")): return True
    except: pass
    try:
        if "container" in Path("/proc/1/environ").read_text(): return True
    except: pass
    if os.environ.get("CODESPACES") or os.environ.get("GITHUB_CODESPACE_TOKEN"): return True
    if "/workspaces/" in str(BASE): return True
    return False

IN_CONTAINER = _in_container()

def _detect_arch():
    m = platform.machine().lower()
    if m in ("x86_64","amd64"): return "amd64"
    if m in ("aarch64","arm64","armv8l"): return "arm64"
    return None

ARCH = _detect_arch()
ARCH_LABEL = platform.machine()
CPU_CORES = multiprocessing.cpu_count()

# ═══════════════════════════════════════════════════════════
# CHROMIUM DISCOVERY + FAST AUTO-INSTALL
# ═══════════════════════════════════════════════════════════
def _build_lib_env():
    env = os.environ.copy()
    if LIBS_DIR.is_dir():
        existing = env.get("LD_LIBRARY_PATH","")
        extra = [str(LIBS_DIR)]
        for d in CHROME_DIR.glob("*/usr/lib/*/"):
            if d.is_dir(): extra.append(str(d))
        new_path = ":".join(extra)
        if existing: new_path += ":" + existing
        env["LD_LIBRARY_PATH"] = new_path
    return env

def _binary_exists(path):
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return False
    try:
        with open(path,'rb') as f: magic = f.read(4)
        return magic[:4] == b'\x7fELF' or magic[:2] == b'#!'
    except: return False

def _validate_chromium(path):
    if not _binary_exists(path): return False
    try:
        env = _build_lib_env()
        r = subprocess.run([path,"--version"], capture_output=True, timeout=8,
                          text=True, env=env)
        o = (r.stdout + r.stderr).lower()
        if "snap" in o: return False
        if r.returncode != 0: return False
        return any(k in o for k in ("chromium","chrome"))
    except: return False

def _find_chromium():
    candidates = [
        CHROME_DIR/"chrome-linux64"/"chrome",
        Path("/usr/lib/chromium/chromium"),
        Path("/usr/lib/chromium-browser/chromium-browser"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/google-chrome"),
    ]
    ungoogled_dir = CHROME_DIR / "ungoogled"
    if ungoogled_dir.is_dir():
        for b in ungoogled_dir.rglob("chrome"):
            if _binary_exists(str(b)): candidates.insert(0, b)
    for p in candidates:
        if _binary_exists(str(p)):
            if _validate_chromium(str(p)): return str(p)
    for n in ["chromium","chromium-browser","google-chrome","google-chrome-stable"]:
        p = shutil.which(n)
        if p and _validate_chromium(p): return p
    for p in candidates:
        if _binary_exists(str(p)): return str(p)
    return None

def _download_file(url, dest, timeout=300):
    print(colored(f"[*] Downloading: {url.split('/')[-1]}","cyan"))
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0 (X11; Linux)"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest,"wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        while True:
            buf = r.read(262144)
            if not buf: break
            f.write(buf)
            downloaded += len(buf)
            if total > 0:
                pct = int(downloaded * 100 / total)
                print(f"\r    {downloaded//(1024*1024)}MB / {total//(1024*1024)}MB ({pct}%)", end="", flush=True)
    print()
    return dest

def _install_chrome_for_testing():
    if ARCH != "amd64": return None
    print(colored("[*] Installing Chrome for Testing (amd64)...","yellow"))
    try:
        api_url = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
        req = urllib.request.Request(api_url, headers={"User-Agent":"Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        stable = data.get("channels",{}).get("Stable",{})
        version = stable.get("version","")
        if not version: return None
        zip_url = None
        for d in stable.get("downloads",{}).get("chrome",[]):
            if d.get("platform") == "linux64": zip_url = d.get("url"); break
        if not zip_url:
            zip_url = f"https://storage.googleapis.com/chrome-for-testing-public/{version}/linux64/chrome-linux64.zip"
        CHROME_DIR.mkdir(parents=True, exist_ok=True)
        zp = CHROME_DIR / "chrome-linux64.zip"
        _download_file(zip_url, str(zp))
        if zp.stat().st_size < 50*1024*1024: zp.unlink(missing_ok=True); return None
        with zipfile.ZipFile(str(zp)) as z: z.extractall(str(CHROME_DIR))
        zp.unlink(missing_ok=True)
        cb = CHROME_DIR / "chrome-linux64" / "chrome"
        if _binary_exists(str(cb)):
            cb.chmod(0o755)
            _log(f"Chrome for Testing {version} installed (amd64)")
            return str(cb)
    except Exception as e:
        _log(f"Chrome for Testing failed: {e}", "ERROR")
    return None

def _install_ungoogled_portable():
    if not ARCH: return None
    asset_pattern = "x86_64_linux.tar.xz" if ARCH == "amd64" else "arm64_linux.tar.xz"
    print(colored(f"[*] Installing Ungoogled Chromium Portable ({ARCH})...","yellow"))
    try:
        api_url = "https://api.github.com/repos/ungoogled-software/ungoogled-chromium-portablelinux/releases/latest"
        req = urllib.request.Request(api_url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/vnd.github.v3+json"})
        release = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        tag = release.get("tag_name","")
        download_url = asset_name = None
        for a in release.get("assets",[]):
            name = a.get("name","")
            if asset_pattern in name and name.endswith(".tar.xz"):
                download_url = a.get("browser_download_url"); asset_name = name; break
        if not download_url:
            _log(f"No {asset_pattern} in release {tag}", "ERROR"); return None
        CHROME_DIR.mkdir(parents=True, exist_ok=True)
        txz = CHROME_DIR / asset_name
        _download_file(download_url, str(txz))
        if txz.stat().st_size < 50*1024*1024: txz.unlink(missing_ok=True); return None
        extract_dir = CHROME_DIR / "ungoogled"
        if extract_dir.exists(): shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        print(colored("[*] Extracting tar.xz...","cyan"))
        with tarfile.open(str(txz), mode='r:xz') as tf:
            for member in tf.getmembers():
                mp = Path(member.name.lstrip('./'))
                if '..' in mp.parts: continue
                target = extract_dir / mp
                if member.isdir(): target.mkdir(parents=True, exist_ok=True)
                elif member.issym():
                    if target.exists() or target.is_symlink(): target.unlink()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(member.linkname, str(target))
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    src = tf.extractfile(member)
                    if src:
                        with open(target,'wb') as dst: shutil.copyfileobj(src, dst)
                        if member.mode & 0o111: os.chmod(target, member.mode & 0o7777)
        txz.unlink(missing_ok=True)
        for binary in extract_dir.rglob("chrome"):
            if _binary_exists(str(binary)):
                binary.chmod(0o755)
                _log(f"Ungoogled Chromium {tag} installed ({ARCH})")
                return str(binary)
        _log("Extracted but no chrome binary found", "ERROR")
    except Exception as e:
        _log(f"Ungoogled portable failed: {e}", "ERROR")
    return None

def _apt_install_chromium():
    if not ARCH: return None
    print(colored("[*] Fallback: apt install chromium...","yellow"))
    try:
        subprocess.run(["sudo","apt-get","update","-qq"], timeout=45, capture_output=True)
        for pkg in ["chromium","chromium-browser"]:
            r = subprocess.run(["sudo","apt-get","install","-y","-qq",pkg], timeout=90, capture_output=True, text=True)
            if r.returncode == 0:
                for p in [f"/usr/bin/{pkg}", f"/usr/lib/{pkg}/{pkg}",
                          "/usr/lib/chromium/chromium","/usr/lib/chromium-browser/chromium-browser"]:
                    if _binary_exists(p):
                        _log(f"Installed {pkg} via apt")
                        return p
    except subprocess.TimeoutExpired:
        _log("apt install timed out (90s)", "ERROR")
    except Exception as e:
        _log(f"apt install failed: {e}", "ERROR")
    return None

def _ensure_chromium():
    t0 = time.monotonic()
    print(colored(f"[*] Architecture: {ARCH_LABEL} → {ARCH or 'UNSUPPORTED'}","cyan"))
    if not ARCH:
        _log(f"Unsupported architecture: {ARCH_LABEL}", "ERROR")
        return None
    print(colored("[*] Checking for existing Chromium...","yellow"))
    existing = _find_chromium()
    if existing:
        _log(f"Found: {existing} ({time.monotonic()-t0:.1f}s)")
        return existing
    print(colored("[*] Not found. Auto-installing...","yellow"))
    result = None
    if ARCH == "amd64":
        result = _install_chrome_for_testing()
        if not result: result = _install_ungoogled_portable()
    elif ARCH == "arm64":
        result = _install_ungoogled_portable()
    if not result: result = _apt_install_chromium()
    elapsed = time.monotonic() - t0
    if result: _log(f"Chromium ready: {result} ({elapsed:.1f}s)")
    else: _log(f"FAILED to install Chromium ({elapsed:.1f}s)", "ERROR")
    return result

def _install_fonts():
    for d in [Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]:
        if d.is_dir():
            try:
                if any(d.rglob("*.ttf")) or any(d.rglob("*.otf")): return
            except: pass
    try:
        subprocess.run(["sudo","apt-get","update","-qq"], timeout=30, capture_output=True)
        subprocess.run(["sudo","apt-get","install","-y","-qq",
                       "fonts-liberation","fonts-dejavu-core","fontconfig","fonts-noto-color-emoji"],
                      timeout=60, capture_output=True)
        subprocess.run(["fc-cache","-f","-s"], timeout=15, capture_output=True)
        _log("Fonts installed")
    except: pass

def _check_and_install_libs(chromium_path):
    missing = []
    try:
        r = subprocess.run(["ldd", chromium_path], capture_output=True, timeout=8,
                          text=True, env=_build_lib_env())
        for line in r.stdout.splitlines():
            if "not found" in line:
                missing.append(line.strip().split("=>")[0].strip())
    except: pass
    if not missing: return
    _log(f"Missing libs: {', '.join(missing[:10])}", "WARN")
    pkg_map = {
        "libX11.so.6":"libx11-6","libXext.so.6":"libxext6","libxcb.so.1":"libxcb1",
        "libXcomposite.so.1":"libxcomposite1","libXdamage.so.1":"libxdamage1",
        "libXfixes.so.3":"libxfixes3","libXrandr.so.2":"libxrandr2",
        "libgtk-3.so.0":"libgtk-3-0","libnss3.so":"libnss3","libnspr4.so":"libnspr4",
        "libasound.so.2":"libasound2","libasound.so":"libasound2",
        "libatk-1.0.so.0":"libatk1.0-0","libatk-bridge-2.0.so.0":"libatk-bridge2.0-0",
        "libpango-1.0.so.0":"libpango-1.0-0","libcairo.so.2":"libcairo2",
        "libcups.so.2":"libcups2","libdrm.so.2":"libdrm2","libgbm.so.1":"libgbm1",
        "libdbus-1.so.3":"libdbus-1-3","libexpat.so.1":"libexpat1",
        "libjpeg.so.62":"libjpeg62-turbo","libpng16.so.16":"libpng16-16",
        "libwebp.so.7":"libwebp7","libfreetype.so.6":"libfreetype6",
        "libfontconfig.so.1":"libfontconfig1","libxkbcommon.so.0":"libxkbcommon0",
        "libX11-xcb.so.1":"libx11-xcb1","libxcb-dri3.so.0":"libxcb-dri3-0",
        "libxshmfence.so.1":"libxshmfence1","libglib-2.0.so.0":"libglib2.0-0",
        "libgio-2.0.so.0":"libglib2.0-0","libgobject-2.0.so.0":"libglib2.0-0",
    }
    pkgs = set()
    for lib in missing:
        for k,v in pkg_map.items():
            if k in lib: pkgs.add(v); break
    if pkgs:
        try:
            subprocess.run(["sudo","apt-get","update","-qq"], timeout=30, capture_output=True)
            r = subprocess.run(["sudo","apt-get","install","-y","-qq"]+list(pkgs),
                         timeout=90, capture_output=True, text=True)
            if r.returncode == 0: _log(f"Installed {len(pkgs)} lib packages")
            else: _log(f"apt install libs failed: {r.stderr[:200]}", "WARN")
        except Exception as e:
            _log(f"Lib install error: {e}", "WARN")

# ═══════════════════════════════════════════════════════════
# STACK MANAGEMENT
# ═══════════════════════════════════════════════════════════
def _install_pkg(pkg):
    try:
        r = subprocess.run(["sudo","apt-get","install","-y","-qq",pkg],
                          timeout=60, capture_output=True, text=True)
        if r.returncode != 0:
            _log(f"apt install {pkg} failed: {r.stderr[:150]}", "WARN")
        return r.returncode == 0
    except Exception as e:
        _log(f"apt install {pkg} error: {e}", "WARN")
        return False

def _wait_for_display(display, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = subprocess.run(["xdpyinfo","-display",display], capture_output=True, timeout=3)
            if r.returncode == 0: return True
        except: pass
        time.sleep(0.3)
    return False

def _kill_proc(name):
    p = PROCS.pop(name, None)
    if p and p.poll() is None:
        try: p.terminate(); p.wait(timeout=3)
        except:
            try: p.kill()
            except: pass

def _cleanup_x_stale_files():
    """Remove stale X lock files and sockets that prevent Xvfb from starting.

    When Xvfb crashes or is killed, it leaves behind:
      /tmp/.X99-lock         — lock file (contains PID)
      /tmp/.X11-unix/X99     — Unix domain socket

    If these exist but no Xvfb is actually running, Xvfb refuses to start
    with: "(EE) Can't read lock file /tmp/.X99-lock"

    Ref: https://unix.stackexchange.com/questions/166016
    Ref: https://github.com/moby/moby/issues/40939
    """
    lock_file = Path(f"/tmp/.X{XVFB_DISPLAY_NUM}-lock")
    socket_file = Path(f"/tmp/.X11-unix/X{XVFB_DISPLAY_NUM}")

    # Check if the lock file points to a running process
    if lock_file.exists():
        try:
            pid_str = lock_file.read_text().strip()
            pid = int(pid_str)
            # Check if that PID is actually alive
            os.kill(pid, 0)  # Signal 0 = existence check
            # Process IS alive — check if it's actually Xvfb
            try:
                cmdline = Path(f"/proc/{pid}/cmdline").read_text()
                if "Xvfb" in cmdline:
                    _log(f"Xvfb already running (PID {pid}), killing it...", "WARN")
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(1)
                    try: os.kill(pid, signal.SIGKILL)
                    except: pass
                    time.sleep(0.5)
            except:
                pass  # Can't read cmdline, kill it anyway
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass  # PID not valid or process not running — stale file

    # Remove stale files
    removed = []
    if lock_file.exists():
        try:
            lock_file.unlink()
            removed.append(str(lock_file))
        except: pass
    if socket_file.exists():
        try:
            socket_file.unlink()
            removed.append(str(socket_file))
        except: pass
    # Also try removing via rm -f (handles permission edge cases)
    try:
        subprocess.run(["rm","-f",f"/tmp/.X{XVFB_DISPLAY_NUM}-lock",
                       f"/tmp/.X11-unix/X{XVFB_DISPLAY_NUM}"],
                      capture_output=True, timeout=3)
    except: pass
    if removed:
        _log(f"Cleaned stale X files: {', '.join(removed)}")

def _set_root_background():
    env = os.environ.copy(); env["DISPLAY"] = XVFB_DISPLAY
    if not shutil.which("xsetroot"): _install_pkg("x11-xserver-utils")
    if shutil.which("xsetroot"):
        try: subprocess.run(["xsetroot","-solid","black"], env=env, capture_output=True, timeout=3)
        except: pass

def _start_xvfb(w, h):
    """Start Xvfb with proper stale file cleanup.

    FIX: Removes /tmp/.X99-lock and /tmp/.X11-unix/X99 before starting.
    FIX: Adds -listen tcp for Xvfb 21.1+ compatibility.
    Ref: https://unix.stackexchange.com/questions/166016
    """
    if not shutil.which("Xvfb"):
        _log("Xvfb not found, installing...", "WARN")
        _install_pkg("xvfb")
    if not shutil.which("Xvfb"):
        _log("Xvfb not available after install attempt", "ERROR")
        return False

    # Check if display is already working
    if _wait_for_display(XVFB_DISPLAY, timeout=2):
        _log(f"Xvfb already running on {XVFB_DISPLAY}")
        return True

    # Kill any existing Xvfb process on our display
    subprocess.run(["pkill","-f",f"Xvfb {XVFB_DISPLAY}"], capture_output=True, timeout=3)
    time.sleep(0.5)

    # CRITICAL FIX: Remove stale lock file and socket
    _cleanup_x_stale_files()

    res = f"{w}x{h}x24"
    try:
        # -listen tcp: Override Xvfb 21.1+ default -nolisten tcp
        # -listen local: Ensure Unix socket works
        # Ref: https://github.com/moby/moby/issues/40939#issuecomment-663175763
        p = subprocess.Popen(
            ["Xvfb", XVFB_DISPLAY, "-screen", "0", res,
             "-ac",
             "-listen", "tcp",
             "-listen", "local",
             "+extension", "GLX",
             "+extension", "MIT-SHM",
             "+render",
             "-noreset"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(1.5)
        if p.poll() is not None:
            _, err = p.communicate(timeout=3)
            err_text = err.decode(errors='replace')[:400]
            _log(f"Xvfb exited (code {p.returncode}): {err_text}", "ERROR")
            # If it's STILL a lock file error, try harder
            if "lock file" in err_text.lower():
                _log("Retrying after aggressive cleanup...", "WARN")
                subprocess.run(["rm","-rf",f"/tmp/.X{XVFB_DISPLAY_NUM}-lock",
                               f"/tmp/.X11-unix/X{XVFB_DISPLAY_NUM}"],
                              capture_output=True, timeout=3)
                # Also try fuser to kill anything holding the socket
                subprocess.run(["fuser","-k",f"/tmp/.X11-unix/X{XVFB_DISPLAY_NUM}"],
                              capture_output=True, timeout=3)
                time.sleep(1)
                p = subprocess.Popen(
                    ["Xvfb", XVFB_DISPLAY, "-screen", "0", res,
                     "-ac", "-listen", "tcp", "-listen", "local",
                     "+extension", "GLX", "+extension", "MIT-SHM",
                     "+render", "-noreset"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                time.sleep(1.5)
                if p.poll() is not None:
                    _, err2 = p.communicate(timeout=3)
                    _log(f"Xvfb retry failed: {err2.decode(errors='replace')[:300]}", "ERROR")
                    return False
            else:
                return False
        if not _wait_for_display(XVFB_DISPLAY, timeout=10):
            _log("Xvfb started but display not responding", "ERROR")
            return False
        PROCS["xvfb"] = p
        _set_root_background()
        _log(f"Xvfb {res} on {XVFB_DISPLAY}")
        return True
    except Exception as e:
        _log(f"Xvfb exception: {e}", "ERROR")
        return False

def _start_x11vnc():
    """Start x11vnc bound strictly to 127.0.0.1 (the -localhost flag below
    already ensures this — it is NOT reachable from other machines)."""
    if not shutil.which("x11vnc"):
        _log("x11vnc not found, installing...", "WARN")
        _install_pkg("x11vnc")
    if not shutil.which("x11vnc"):
        _log("x11vnc not available after install attempt", "ERROR")
        return False
    subprocess.run(["pkill","-f","x11vnc"], capture_output=True, timeout=3)
    time.sleep(0.5)
    if not _wait_for_display(XVFB_DISPLAY, timeout=5):
        _log("X display not ready for x11vnc", "ERROR")
        return False
    base_cmd = ["x11vnc","-display",XVFB_DISPLAY,"-forever","-shared",
           "-rfbport",str(VNC_PORT),"-localhost","-noshm",
           "-noxdamage","-noxfixes","-noxrecord",
           "-ncache","0","-nopw","-wait","5",
           "-o",str(BASE/".x11vnc.log")]
    attempts = [
        base_cmd + ["-xkb", "-setdesktopsize"],
        base_cmd + ["-xkb"],
        base_cmd,
    ]
    for i, cmd in enumerate(attempts):
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(2.0)
            if p.poll() is None:
                PROCS["x11vnc"] = p
                _log(f"x11vnc on 127.0.0.1:{VNC_PORT} (attempt {i+1})")
                return True
            else:
                _, err = p.communicate(timeout=3)
                _log(f"x11vnc attempt {i+1} failed: {err.decode(errors='replace')[:300]}", "WARN")
        except Exception as e:
            _log(f"x11vnc attempt {i+1} exception: {e}", "WARN")
    _log("x11vnc failed all attempts", "ERROR")
    return False

def _start_novnc():
    """Locate (or download) the noVNC static web assets only.

    NOTE: This used to also spawn `websockify` bound to 0.0.0.0:6080,
    which is what gave unauthenticated network clients a direct path to
    the VNC session. That network listener has been removed entirely —
    the WebSocket<->RFB bridge is now handled inside Flask at /ws,
    guarded by @login_required, and it connects only to 127.0.0.1:5901.
    """
    global NOVNC_WEB_ROOT, NOVNC_ENTRY
    novnc_web = None
    for p in ["/usr/share/novnc","/usr/share/noVNC","/opt/novnc"]:
        if Path(p).is_dir() and (Path(p)/"vnc.html").exists(): novnc_web = p; break
    if not novnc_web and NOVNC_DIR.is_dir():
        for d in NOVNC_DIR.iterdir():
            if d.is_dir() and (d/"vnc.html").exists(): novnc_web = str(d); break
        if not novnc_web and (NOVNC_DIR/"vnc.html").exists(): novnc_web = str(NOVNC_DIR)
    if not novnc_web:
        _log("Downloading noVNC from GitHub...", "WARN")
        try:
            NOVNC_DIR.mkdir(parents=True, exist_ok=True)
            url = "https://github.com/novnc/noVNC/archive/refs/heads/master.tar.gz"
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r: data = r.read()
            tf = tarfile.open(fileobj=io.BytesIO(data), mode='r:gz')
            tf.extractall(str(NOVNC_DIR)); tf.close()
            for d in NOVNC_DIR.iterdir():
                if d.is_dir() and (d/"vnc.html").exists(): novnc_web = str(d); break
        except Exception as e:
            _log(f"noVNC download failed: {e}", "ERROR")
    if not novnc_web:
        _log("noVNC files not found", "ERROR")
        return False
    NOVNC_WEB_ROOT = novnc_web; NOVNC_ENTRY = "vnc.html"
    _log(f"noVNC static files ready at {novnc_web} (served via Flask /novnc/, no network listener)")
    return True

def _launch_chromium(w, h):
    if not CHROME_BIN:
        _log("No Chromium binary", "ERROR")
        return False
    env = _build_lib_env(); env["DISPLAY"] = XVFB_DISPLAY
    ud = tempfile.mkdtemp(prefix="qs_profile_")
    args = [CHROME_BIN, f"--user-data-dir={ud}",
        "--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage",
        "--no-first-run","--no-default-browser-check",
        "--disable-background-networking","--disable-sync","--disable-extensions",
        "--mute-audio","--disable-default-apps","--password-store=basic",
        "--disable-gpu","--disable-gpu-compositing",
        "--use-gl=angle","--use-angle=swiftshader",
        "--force-color-profile=srgb","--force-device-scale-factor=1",
        f"--window-size={w},{h}","--window-position=0,0",
        "about:blank"]
    try:
        p = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(3.0)
        if p.poll() is not None:
            _, err = p.communicate(timeout=3)
            err_text = err.decode(errors='replace')[:500]
            _log(f"Chromium exited (code {p.returncode}): {err_text}", "ERROR")
            if "error while loading shared libraries" in err_text:
                m = re.search(r'error while loading shared libraries:\s+(\S+?):', err_text)
                if m:
                    _log(f"Missing runtime lib: {m.group(1)} — installing...", "WARN")
                    _check_and_install_libs(CHROME_BIN)
                    p2 = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    time.sleep(3.0)
                    if p2.poll() is None:
                        PROCS["chromium"] = p2
                        _log(f"Chromium {w}x{h} (PID {p2.pid}) — retry OK")
                        time.sleep(1.0); _force_resize_window(w, h)
                        return True
            return False
        PROCS["chromium"] = p
        _log(f"Chromium {w}x{h} (PID {p.pid})")
        time.sleep(1.0); _force_resize_window(w, h)
        return True
    except Exception as e:
        _log(f"Chromium launch exception: {e}", "ERROR")
        return False

def _ensure_xdotool():
    if not shutil.which("xdotool"): _install_pkg("xdotool")
    return shutil.which("xdotool") is not None

def _force_resize_window(w, h):
    if not _ensure_xdotool(): return
    env = os.environ.copy(); env["DISPLAY"] = XVFB_DISPLAY
    try:
        wid = None
        for cls in ["chromium","google-chrome","chrome","Chromium","Google-chrome"]:
            try:
                r = subprocess.run(["xdotool","search","--class",cls],
                                  capture_output=True, text=True, timeout=3, env=env)
                wins = [x.strip() for x in r.stdout.strip().splitlines() if x.strip()]
                if wins: wid = wins[0]; break
            except: continue
        if not wid:
            try:
                r = subprocess.run(["xdotool","search","--name",""],
                                  capture_output=True, text=True, timeout=3, env=env)
                wins = [x.strip() for x in r.stdout.strip().splitlines() if x.strip()]
                if wins: wid = wins[0]
            except: pass
        if not wid: return
        subprocess.run(["xdotool","windowmove",wid,"0","0"], capture_output=True, timeout=3, env=env)
        subprocess.run(["xdotool","windowsize",wid,str(w),str(h)], capture_output=True, timeout=3, env=env)
        subprocess.run(["xdotool","windowactivate","--sync",wid], capture_output=True, timeout=3, env=env)
    except: pass

def _start_resizer_thread():
    global _resizer_running
    if _resizer_running: return
    _resizer_running = True
    def _loop():
        global _resizer_running
        while _resizer_running:
            time.sleep(3)
            cp = PROCS.get("chromium")
            if not cp or cp.poll() is not None:
                _resizer_running = False; break
            _force_resize_window(CURRENT_W, CURRENT_H)
    threading.Thread(target=_loop, daemon=True, name="resizer").start()

def _start_full_stack(w=None, h=None):
    global CURRENT_W, CURRENT_H, _resizer_running, STACK_OK
    if w is None: w = CURRENT_W
    if h is None: h = CURRENT_H
    with _stack_lock:
        _log(f"Starting stack at {w}x{h}...")
        _resizer_running = False
        time.sleep(0.3)
        CURRENT_W, CURRENT_H = w, h
        xvfb_ok = _start_xvfb(w, h)
        if not xvfb_ok:
            _log("Stack FAILED: Xvfb could not start", "ERROR")
            STACK_OK = False
            return False
        _launch_chromium(w, h)
        vnc_ok = _start_x11vnc()
        novnc_ok = _start_novnc()
        _start_resizer_thread()
        STACK_OK = vnc_ok and novnc_ok
        if STACK_OK: _log(f"Stack OK at {w}x{h}")
        else:
            if not vnc_ok: _log("Stack partial: x11vnc failed", "ERROR")
            if not novnc_ok: _log("Stack partial: noVNC files unavailable", "ERROR")
        return STACK_OK

def _restart_stack(w, h):
    global _resizer_running
    w = max(MIN_W, min(int(w), MAX_W))
    h = max(MIN_H, min(int(h), MAX_H))
    _kill_proc("chromium")
    _kill_proc("x11vnc")
    _kill_proc("xvfb")
    time.sleep(0.5)
    return _start_full_stack(w, h)

# ═══════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════
def _hash(pw): return hashlib.sha256(pw.encode()).hexdigest()
def _load_auth():
    global _auth_cache, _auth_cache_ts
    if time.monotonic() - _auth_cache_ts < 30: return
    c = {}
    if AUTH_FILE.exists():
        for line in AUTH_FILE.read_text().splitlines():
            if ":" in line:
                u, p = line.strip().split(":", 1)
                c[u.strip()] = p.strip()
    _auth_cache, _auth_cache_ts = c, time.monotonic()

def check_auth(u, p):
    if not u or not p: return False
    u, p = u.strip()[:64], p.strip()[:128]
    if not AUTH_FILE.exists():
        return hmac.compare_digest(u,"admin") and hmac.compare_digest(p,"admin")
    _load_auth()
    s = _auth_cache.get(u)
    if not s: hmac.compare_digest(p,"x"); return False
    return hmac.compare_digest(s, _hash(p)) or hmac.compare_digest(s, p)

def _real_ip(): return request.remote_addr or "0.0.0.0"
def _rate_limited(ip):
    now = time.monotonic()
    with _attempts_lock:
        a = [t for t in _login_attempts.get(ip,[]) if now - t < ATTEMPT_WINDOW]
        _login_attempts[ip] = a
        return len(a) >= MAX_ATTEMPTS
def _record(ip):
    with _attempts_lock: _login_attempts.setdefault(ip,[]).append(time.monotonic())
def _get_client_host():
    try: return urlparse(request.base_url).hostname or "127.0.0.1"
    except: return (request.host or "127.0.0.1").split(":")[0]

# ═══════════════════════════════════════════════════════════
# FLASK APP
# ═══════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = SESSION_LIFE
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=False, MAX_CONTENT_LENGTH=MAX_BODY)
sock = Sock(app)

def _security_headers(r):
    r.headers.update({"X-Content-Type-Options":"nosniff","X-XSS-Protection":"1; mode=block",
                      "Referrer-Policy":"no-referrer"})
    return r
app.after_request(_security_headers)

def login_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        if not session.get("authenticated"):
            if request.is_json: return jsonify({"error":"Unauthorized"}), 401
            return redirect(url_for("login_page"))
        return fn(*a, **kw)
    return w

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QuantumSurf — Login</title>
<style>
:root{--bg:#0a0e14;--panel:#111820;--acc:#00e88f;--text:#d0ffe8;--muted:#3a6a50;--danger:#ff4455;--link:#00cfff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);font-family:'Courier New',monospace;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:var(--panel);border:1px solid var(--acc);padding:44px 36px;width:min(420px,92vw);box-shadow:0 0 30px rgba(0,232,143,.08)}
.logo{font-size:24px;font-weight:900;color:var(--acc);text-align:center;letter-spacing:4px;margin-bottom:4px;text-shadow:0 0 20px rgba(0,232,143,.3)}
.sub{text-align:center;color:var(--muted);font-size:11px;letter-spacing:2px;margin-bottom:32px}
label{display:block;color:var(--acc);font-size:10px;letter-spacing:2px;text-transform:uppercase;margin-bottom:5px}
input{width:100%;background:#080c10;border:1px solid var(--muted);color:var(--text);font-family:inherit;font-size:14px;padding:11px 13px;outline:none;margin-bottom:18px}
input:focus{border-color:var(--acc)}
.btn{width:100%;background:transparent;border:1px solid var(--acc);color:var(--acc);font-family:inherit;font-size:12px;font-weight:700;letter-spacing:3px;padding:13px;cursor:pointer;text-transform:uppercase;transition:all .15s}
.btn:hover{background:var(--acc);color:#000}
.err{background:rgba(255,68,85,.08);border:1px solid var(--danger);color:var(--danger);padding:9px 12px;font-size:11px;margin-bottom:16px}
.credits{text-align:center;margin-top:28px;padding-top:16px;border-top:1px solid #1a2a1e}
.credits .made{color:var(--muted);font-size:10px;letter-spacing:1px;margin-bottom:6px}
.credits .author{color:var(--acc);font-size:11px;font-weight:700;letter-spacing:1px}
.credits .gh{display:inline-block;margin-top:8px;color:var(--link);font-size:10px;text-decoration:none;border:1px solid var(--link);padding:4px 12px;letter-spacing:1px;transition:all .15s}
.credits .gh:hover{background:var(--link);color:#000}
</style></head><body>
<div class="card">
  <div class="logo">QUANTUMSURF</div>
  <div class="sub">REMOTE BROWSER ISOLATION</div>
  {% if error %}<div class="err">⚠ {{ error }}</div>{% endif %}
  <form method="POST" action="/login" autocomplete="off">
    <input type="hidden" name="csrf_token" value="{{ csrf }}">
    <label>Username</label><input type="text" name="username" maxlength="64" required autofocus>
    <label>Password</label><input type="password" name="password" maxlength="128" required>
    <button type="submit" class="btn">▶ Authenticate</button>
  </form>
  <div class="credits">
    <div class="made">Made By Aryan Giri | giriaryan694-a11y</div>
    <div class="author">Pure Chromium GUI · noVNC · Auto-Install</div>
    <a class="gh" href="https://github.com/giriaryan694-a11y" target="_blank" rel="noopener">⭐ GitHub</a>
  </div>
</div></body></html>"""

APP_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QuantumSurf — Chromium</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:#000;font-family:'Courier New',monospace}
#frame{position:fixed;top:0;left:0;width:100%;height:100%;border:none;display:block;background:#000}
.ctrl{position:fixed;z-index:9999;opacity:0;transition:opacity .4s;pointer-events:none}
.ctrl.show{opacity:1;pointer-events:auto}
#logout-btn{top:6px;right:6px;background:rgba(13,17,23,.9);border:1px solid #ff4455;color:#ff4455;font-size:10px;letter-spacing:1px;padding:4px 10px;cursor:pointer;font-family:inherit}
#logout-btn:hover{background:#ff4455;color:#000}
#settings-btn{top:6px;right:60px;background:rgba(13,17,23,.9);border:1px solid #00e88f;color:#00e88f;font-size:10px;letter-spacing:1px;padding:4px 10px;cursor:pointer;font-family:inherit}
#settings-btn:hover{background:#00e88f;color:#000}
#settings{position:fixed;top:36px;right:6px;z-index:9999;background:rgba(13,17,23,.95);border:1px solid #1a2a1e;padding:14px;width:280px;display:none;font-size:11px;color:#3a6a50;max-height:80vh;overflow-y:auto}
#settings.open{display:block}
#settings h3{color:#00e88f;font-size:11px;letter-spacing:2px;margin-bottom:10px;font-weight:700}
#settings label{display:block;color:#00e88f;font-size:9px;letter-spacing:1px;text-transform:uppercase;margin:8px 0 3px}
#settings input[type=number]{width:100%;background:#080c10;border:1px solid #1a2a1e;color:#d0ffe8;font-family:inherit;font-size:12px;padding:6px 8px;outline:none}
#settings input:focus{border-color:#00e88f}
#settings .row{display:flex;gap:8px}
#settings .row>div{flex:1}
#settings button{width:100%;background:transparent;border:1px solid #00e88f;color:#00e88f;font-family:inherit;font-size:10px;letter-spacing:1px;padding:7px;cursor:pointer;text-transform:uppercase;margin-top:8px;transition:all .15s}
#settings button:hover{background:#00e88f;color:#000}
#settings button.auto{border-color:#00cfff;color:#00cfff}
#settings button.auto:hover{background:#00cfff;color:#000}
#settings button.retry{border-color:#ffcc00;color:#ffcc00}
#settings button.retry:hover{background:#ffcc00;color:#000}
#settings .info{font-size:9px;color:#3a6a50;margin-top:8px;line-height:1.5}
#settings .cur{color:#00e88f;font-size:10px;margin-bottom:8px}
#settings .presets{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}
#settings .presets button{width:auto;flex:1;min-width:50px;padding:4px 2px;font-size:9px;margin-top:0}
#settings .stack-log{background:#080c10;border:1px solid #1a2a1e;padding:8px;margin-top:8px;font-size:9px;line-height:1.6;max-height:150px;overflow-y:auto;color:#3a6a50;white-space:pre-wrap;word-break:break-all}
#settings .stack-log .err{color:#ff4455}
#settings .stack-log .ok{color:#00e88f}
#status-msg{position:fixed;bottom:8px;left:50%;transform:translateX(-50%);z-index:9999;background:rgba(13,17,23,.9);border:1px solid #1a2a1e;color:#3a6a50;font-size:10px;padding:4px 14px;opacity:0;transition:opacity .4s;pointer-events:none;font-family:inherit}
#status-msg.show{opacity:1}
#fallback{display:none;position:fixed;inset:0;background:#0a0e14;flex-direction:column;align-items:center;justify-content:center;gap:12px;color:#3a6a50;font-size:12px;text-align:center;padding:20px;z-index:9998}
#fallback a{color:#00e88f;text-decoration:none;border:1px solid #00e88f;padding:10px 20px;font-size:11px}
#fallback a:hover{background:#00e88f;color:#000}
#fallback .title{color:#00e88f;font-size:16px;font-weight:700;letter-spacing:3px}
#fallback .retry-btn{background:transparent;border:1px solid #ffcc00;color:#ffcc00;padding:10px 20px;font-size:11px;cursor:pointer;font-family:inherit;letter-spacing:1px}
#fallback .retry-btn:hover{background:#ffcc00;color:#000}
#fallback .log{background:#080c10;border:1px solid #1a2a1e;padding:10px;font-size:9px;max-width:500px;max-height:200px;overflow-y:auto;text-align:left;line-height:1.6;white-space:pre-wrap;word-break:break-all;color:#3a6a50}
</style></head><body>
<iframe id="frame" src="{{ novnc_url }}" allow="clipboard-read; clipboard-write; fullscreen"></iframe>
<button id="settings-btn" class="ctrl" onclick="toggleSettings()">⚙ RES</button>
<button id="logout-btn" class="ctrl" onclick="if(confirm('Logout?'))location.href='/logout'">⏻ EXIT</button>
<div id="settings">
  <h3>⚙ DISPLAY SETTINGS</h3>
  <div class="cur" id="cur-res">Current: detecting...</div>
  <button class="auto" onclick="autoDetect()">🔍 AUTO-DETECT MY SCREEN</button>
  <label>Manual Resolution</label>
  <div class="row">
    <div><input type="number" id="res-w" min="800" max="3840" placeholder="Width"></div>
    <div><input type="number" id="res-h" min="600" max="2160" placeholder="Height"></div>
  </div>
  <button onclick="applyManual()">▶ APPLY RESOLUTION</button>
  <label>Presets</label>
  <div class="presets">
    <button onclick="applyPreset(1920,1080)">1080p</button>
    <button onclick="applyPreset(1366,768)">768p</button>
    <button onclick="applyPreset(2560,1440)">1440p</button>
    <button onclick="applyPreset(1280,720)">720p</button>
  </div>
  <label>Stack Status</label>
  <button class="retry" onclick="retryStack()">⟳ RESTART STACK</button>
  <div class="stack-log" id="stack-log">Loading...</div>
  <div class="info" id="screen-info">Detecting screen...</div>
</div>
<div id="status-msg"></div>
<div id="fallback">
  <div class="title">QUANTUMSURF</div>
  <p id="fallback-msg">Connecting to Chromium...</p>
  <button class="retry-btn" onclick="retryStack()">⟳ RESTART STACK & RETRY</button>
  <a href="{{ novnc_url }}" target="_blank" rel="noopener">▶ OPEN noVNC IN NEW TAB</a>
  <div class="log" id="fallback-log"></div>
</div>
<script>
(function(){
  var frame=document.getElementById('frame'),btns=document.querySelectorAll('.ctrl'),
  settings=document.getElementById('settings'),statusMsg=document.getElementById('status-msg'),
  fallback=document.getElementById('fallback'),loaded=false,hideTimer=null,statusTimer=null;
  function showCtrls(){btns.forEach(function(b){b.classList.add('show')});clearTimeout(hideTimer);
    hideTimer=setTimeout(function(){btns.forEach(function(b){b.classList.remove('show')})},3000);}
  document.addEventListener('mousemove',showCtrls);document.addEventListener('touchstart',showCtrls);showCtrls();
  function showStatus(msg,dur){statusMsg.textContent=msg;statusMsg.classList.add('show');clearTimeout(statusTimer);
    statusTimer=setTimeout(function(){statusMsg.classList.remove('show')},dur||3000);}
  function getScreenInfo(){var dpr=window.devicePixelRatio||1;
    return{screenW:screen.width,screenH:screen.height,availW:screen.availWidth,availH:screen.availHeight,
    viewportW:window.innerWidth,viewportH:window.innerHeight,dpr:dpr,
    physW:Math.round(screen.width*dpr),physH:Math.round(screen.height*dpr)};}
  function updateScreenInfo(){var s=getScreenInfo();
    document.getElementById('screen-info').innerHTML='Screen: '+s.screenW+'x'+s.screenH+' @'+s.dpr+'x<br>'+
    'Viewport: '+s.viewportW+'x'+s.viewportH+'<br>Available: '+s.availW+'x'+s.availH;}
  updateScreenInfo();
  function refreshCurRes(){fetch('/api/get_resolution',{credentials:'same-origin'}).then(function(r){return r.json()})
    .then(function(d){document.getElementById('cur-res').textContent='Current: '+d.width+'x'+d.height;
    document.getElementById('res-w').value=d.width;document.getElementById('res-h').value=d.height;}).catch(function(){});}
  refreshCurRes();
  function refreshStackLog(){fetch('/api/stack_status',{credentials:'same-origin'}).then(function(r){return r.json()})
    .then(function(d){var el=document.getElementById('stack-log'),html='';
    d.log.forEach(function(line){var cls=line.indexOf('[ERROR]')>=0?'err':(line.indexOf('[WARN]')>=0?'':'ok');
    html+='<span class="'+cls+'">'+line+'</span>\n';});el.innerHTML=html;el.scrollTop=el.scrollHeight;
    var fl=document.getElementById('fallback-log');if(fl)fl.innerHTML=html;
    var fm=document.getElementById('fallback-msg');
    if(fm)fm.textContent=d.stack_ok?'Stack running. If blank, try retry.':'Stack NOT running. Click retry.';}).catch(function(){});}
  window.retryStack=function(){showStatus('Restarting stack...',8000);
    fetch('/api/restart_stack',{method:'POST',credentials:'same-origin'}).then(function(r){return r.json()})
    .then(function(d){showStatus(d.status==='ok'?'✓ Restarted — reloading...':'✗ '+d.error,5000);
    refreshStackLog();setTimeout(function(){frame.src=frame.src;},3000);}).catch(function(e){showStatus('✗ '+e,5000);});};
  window.autoDetect=function(){var s=getScreenInfo();showStatus('Auto: '+s.viewportW+'x'+s.viewportH);applyResolution(s.viewportW,s.viewportH);};
  window.applyManual=function(){applyResolution(parseInt(document.getElementById('res-w').value)||1920,parseInt(document.getElementById('res-h').value)||1080);};
  window.applyPreset=function(w,h){document.getElementById('res-w').value=w;document.getElementById('res-h').value=h;applyResolution(w,h);};
  function applyResolution(w,h){showStatus('Applying '+w+'x'+h+'...');
    fetch('/api/set_resolution',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({width:w,height:h}),credentials:'same-origin'}).then(function(r){return r.json()})
    .then(function(d){if(d.status==='ok'){showStatus('✓ '+d.width+'x'+d.height,5000);refreshCurRes();
    setTimeout(function(){frame.src=frame.src;},3000);}else showStatus('✗ '+(d.error||'error'),5000);}).catch(function(e){showStatus('✗ '+e,5000);});}
  window.toggleSettings=function(){settings.classList.toggle('open');
    if(settings.classList.contains('open')){updateScreenInfo();refreshCurRes();refreshStackLog();}};
  frame.addEventListener('load',function(){loaded=true;});
  frame.addEventListener('error',function(){if(!loaded){fallback.style.display='flex';refreshStackLog();}});
  setTimeout(function(){if(!loaded){try{var x=frame.contentWindow.location.href;}catch(e){loaded=true;return;}
    fallback.style.display='flex';refreshStackLog();}},10000);
  window.addEventListener('resize',updateScreenInfo);
})();
</script>
</body></html>"""

@app.route("/")
@login_required
def index():
    # noVNC silently PREFIXES a relative `path` value with the directory
    # it's served from (here, /novnc/), so path=ws was actually being
    # requested as /novnc/ws — which doesn't exist. Passing a full
    # absolute ws:// URL instead sidesteps that prefixing entirely.
    # Ref: https://github.com/novnc/noVNC/pull/1058
    scheme = "wss" if request.is_secure else "ws"
    ws_abs_url = f"{scheme}://{request.host}/ws"
    query = urlencode({
        "autoconnect": "true",
        "resize": "scale",
        "view_clip": "1",
        "show_dot": "true",
        "reconnect": "true",
        "reconnect_delay": "2000",
        "bell": "off",
        "path": ws_abs_url,
    })
    novnc_url = f"/novnc/{NOVNC_ENTRY}?{query}"
    return render_template_string(APP_HTML, novnc_url=novnc_url, novnc_port=NOVNC_PORT)

@app.route("/login", methods=["GET"])
def login_page():
    if session.get("authenticated"): return redirect(url_for("index"))
    csrf = secrets.token_hex(16); session["csrf"] = csrf
    resp = make_response(render_template_string(LOGIN_HTML, error=None, csrf=csrf))
    resp.headers["X-Frame-Options"] = "DENY"
    return resp

@app.route("/login", methods=["POST"])
def login_post():
    if request.content_length and request.content_length > MAX_BODY: abort(413)
    ip = _real_ip()
    if _rate_limited(ip):
        csrf = secrets.token_hex(16); session["csrf"] = csrf
        return render_template_string(LOGIN_HTML, error="Too many attempts. Wait 60s.", csrf=csrf), 429
    fc = request.form.get("csrf_token","")
    if not fc or fc != session.get("csrf"):
        csrf = secrets.token_hex(16); session["csrf"] = csrf
        return render_template_string(LOGIN_HTML, error="Invalid request.", csrf=csrf), 400
    u = html.escape(request.form.get("username","").strip())
    p = request.form.get("password","").strip()
    if check_auth(u, p):
        session.clear(); session.permanent = True
        session["authenticated"] = True; session["username"] = u
        return redirect(url_for("index"))
    _record(ip)
    csrf = secrets.token_hex(16); session["csrf"] = csrf
    return render_template_string(LOGIN_HTML, error="Invalid credentials.", csrf=csrf), 401

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/novnc/<path:filename>")
@login_required
def novnc_static(filename):
    """Serve the noVNC web client's static files. This replaces websockify's
    old built-in web server, which used to also listen on 0.0.0.0:6080."""
    if not NOVNC_WEB_ROOT:
        abort(404)
    return send_from_directory(NOVNC_WEB_ROOT, filename)

@sock.route("/ws")
def ws_vnc_bridge(ws):
    """WebSocket <-> raw-RFB bridge, replacing websockify's network listener.

    Only reachable through Flask on 0.0.0.0:FLASK_PORT, and only after a
    valid login session — this is the sole path from the network to the
    VNC session. Internally it connects only to 127.0.0.1:VNC_PORT.
    """
    if not session.get("authenticated"):
        try: ws.close()
        except Exception: pass
        return

    try:
        vnc_sock = socket.create_connection(("127.0.0.1", VNC_PORT), timeout=5)
        vnc_sock.setblocking(False)
    except Exception as e:
        _log(f"ws_vnc_bridge: cannot reach 127.0.0.1:{VNC_PORT}: {e}", "ERROR")
        try: ws.close()
        except Exception: pass
        return

    _log(f"ws_vnc_bridge: connected to 127.0.0.1:{VNC_PORT}, bridging")

    # Single-threaded, select()-based multiplexer — avoids any concurrency
    # issues from calling ws.send()/ws.receive() on two different threads.
    try:
        import select as _select
        while True:
            readable, _, _ = _select.select([vnc_sock], [], [], 0.02)
            if vnc_sock in readable:
                try:
                    data = vnc_sock.recv(65536)
                except BlockingIOError:
                    data = None
                if data == b"":
                    _log("ws_vnc_bridge: VNC side closed connection", "WARN")
                    break
                if data:
                    ws.send(data)

            msg = ws.receive(timeout=0.02)
            if msg is not None:
                if isinstance(msg, str):
                    msg = msg.encode("utf-8", "ignore")
                vnc_sock.sendall(msg)
    except Exception as e:
        _log(f"ws_vnc_bridge: bridge error: {e}", "ERROR")
    finally:
        try: vnc_sock.close()
        except Exception: pass
        _log("ws_vnc_bridge: connection closed")

@app.route("/api/get_resolution")
@login_required
def api_get_resolution():
    return jsonify({"width":CURRENT_W,"height":CURRENT_H,"min_w":MIN_W,"min_h":MIN_H,"max_w":MAX_W,"max_h":MAX_H})

@app.route("/api/set_resolution", methods=["POST"])
@login_required
def api_set_resolution():
    data = request.get_json(silent=True) or {}
    try: w = int(data.get("width", CURRENT_W)); h = int(data.get("height", CURRENT_H))
    except: return jsonify({"status":"error","error":"Invalid"}), 400
    w = max(MIN_W, min(w, MAX_W)); h = max(MIN_H, min(h, MAX_H))
    if w == CURRENT_W and h == CURRENT_H:
        return jsonify({"status":"ok","width":w,"height":h,"changed":False})
    threading.Thread(target=lambda: _restart_stack(w, h), daemon=True).start()
    return jsonify({"status":"ok","width":w,"height":h,"changed":True})

@app.route("/api/stack_status")
@login_required
def api_stack_status():
    alive = {k: (v.poll() is None) for k, v in PROCS.items()}
    with _stack_log_lock: log_copy = list(_stack_log[-30:])
    return jsonify({"stack_ok":STACK_OK,"processes":alive,
        "resolution":f"{CURRENT_W}x{CURRENT_H}","chromium_bin":CHROME_BIN,
        "arch":ARCH_LABEL,"log":log_copy})

@app.route("/api/restart_stack", methods=["POST"])
@login_required
def api_restart_stack():
    threading.Thread(target=lambda: _restart_stack(CURRENT_W, CURRENT_H), daemon=True).start()
    return jsonify({"status":"ok","message":"Stack restart initiated"})

@app.route("/health")
@login_required
def health():
    alive = {k: (v.poll() is None) for k, v in PROCS.items()}
    return jsonify({"processes":alive,"chromium_bin":CHROME_BIN,
        "resolution":f"{CURRENT_W}x{CURRENT_H}","arch":ARCH_LABEL,
        "container":IN_CONTAINER,"stack_ok":STACK_OK})

# ═══════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════
def _cleanup(*_):
    global _resizer_running
    _resizer_running = False
    print(colored("\n[*] Shutting down...","yellow"))
    for name in ["chromium","x11vnc","xvfb"]:
        _kill_proc(name)
    # Clean up X files on exit too
    _cleanup_x_stale_files()
    sys.exit(0)

signal.signal(signal.SIGINT, _cleanup)
signal.signal(signal.SIGTERM, _cleanup)

# ═══════════════════════════════════════════════════════════
# BOOT
# ═══════════════════════════════════════════════════════════
CHROME_BIN = _ensure_chromium()

if CHROME_BIN:
    _install_fonts()
    _check_and_install_libs(CHROME_BIN)

STACK_OK = False
if CHROME_BIN:
    STACK_OK = _start_full_stack(CURRENT_W, CURRENT_H)
else:
    _log("No Chromium — stack not started", "ERROR")

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    os.system('cls' if os.name == 'nt' else 'clear')
    banner = pyfiglet.figlet_format("QuantumSurf", font="slant")
    print(colored(banner, "cyan"))
    print(colored("=" * 60, "cyan"))
    print(colored("  QuantumSurf — Made by Aryan Giri", "yellow", attrs=["bold"]))
    print(colored("  GitHub: https://github.com/giriaryan694-a11y", "cyan"))
    print(colored("  Pure Chromium GUI · noVNC · Auto-Install · Fingerprint", "white"))
    print(colored("=" * 60, "cyan"))
    print(colored(f"  Architecture : {ARCH_LABEL} ({ARCH or 'UNSUPPORTED'})", "white"))
    print(colored(f"  Chromium     : {CHROME_BIN or 'NOT FOUND'}", "green" if CHROME_BIN else "red"))
    print(colored(f"  Resolution   : {CURRENT_W}x{CURRENT_H} (auto-adjustable)", "green"))
    print(colored(f"  x11vnc       : 127.0.0.1:{VNC_PORT} (loopback only)", "green"))
    print(colored(f"  VNC bridge   : served via Flask at /ws (auth-gated, no separate port)", "green"))
    print(colored(f"  Flask        : 0.0.0.0:{FLASK_PORT} (the only network-exposed port)", "green"))
    print(colored(f"  Container    : {'YES' if IN_CONTAINER else 'No'}", "yellow" if IN_CONTAINER else "white"))
    print(colored(f"  Stack OK     : {'YES ✓' if STACK_OK else 'NO ✗'}", "green" if STACK_OK else "red"))
    print(colored("=" * 60, "cyan"))
    print()
    print(colored(f"  → Login:    http://0.0.0.0:{FLASK_PORT}", "green", attrs=["bold"]))
    print(colored(f"  → Default:  admin / admin (or auth.txt)", "magenta"))
    print()
    if not CHROME_BIN:
        print(colored("[!] FATAL: Could not find or install Chromium!","red"))
        sys.exit(1)
    if not STACK_OK:
        print(colored("[!] Stack incomplete — use ⚙ RES → RESTART STACK in GUI","yellow"))
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True)
