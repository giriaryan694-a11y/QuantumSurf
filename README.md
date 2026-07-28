# 🌊 QuantumSurf — Remote Browser Isolation

**Pure Chromium GUI streamed to your browser via noVNC. No CDP. No spoofing. No fake keyboards. Just real Chromium.**

Made by **Aryan Giri** | [giriaryan694-a11y](https://github.com/giriaryan694-a11y)

---

## What It Does

QuantumSurf runs a **real Chromium browser** on a virtual display (Xvfb), mirrors it via VNC, and streams it to your browser using noVNC (WebSocket). You see and interact with Chromium’s **exact native GUI** — real address bar, real tabs, real right-click menus, real DevTools.

```text
Your Browser
    ↓
Flask :8000 (auth gate)
    ↓
iframe → noVNC :6080 (websockify)
    ↓
VNC → x11vnc :5901
    ↓
Xvfb :99 → Chromium (headed, full native UI)
```

---

## Features

* **Pure Chromium GUI** — native browser UI, not screenshots or CDP hacks
* **Auto-install Chromium** — detects amd64/arm64 and downloads the right build automatically
* **Screen fingerprinting** — auto-detects browser viewport size and matches the virtual display
* **Manual resolution control** — presets like 720p / 768p / 1080p / 1440p + custom
* **Stack monitoring** — live error log + restart button in the GUI
* **Auth system** — username/password with rate limiting and CSRF protection
* **Multi-arch** — works on x86_64 (amd64) and aarch64 (arm64)
* **Cloud-friendly** — tested on GitHub Codespaces Ubuntu and TermuxCodeSpace

---

## Screenshots

> Add your own screenshots here.

### Login Screen

<img width="862" height="792" alt="Screenshot 2026-07-27 223254" src="https://github.com/user-attachments/assets/d01d52db-a5b7-4aa7-a7dd-0d47ddd31751" />


### Chromium View

<img width="1806" height="973" alt="Screenshot 2026-07-27 223215" src="https://github.com/user-attachments/assets/989c4564-60eb-42fc-8df8-fc269cd0252c" />

### Terminal View

<img width="1383" height="840" alt="Screenshot 2026-07-27 223316" src="https://github.com/user-attachments/assets/bd5ed7e1-7ff9-4283-b6fc-2dc351fcc46e" />


---

## Quick Start

### 1. Install prerequisites

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip x11-utils
pip3 install flask pyfiglet termcolor colorama flask-sock
```

### 2. Run

```bash
git clone https://github.com/giriaryan694-a11y/QuantumSurf.git
cd QuantumSurf
python3 main.py
```

### 3. Login

Open `http://YOUR_IP:8000` and log in with:

* **Username:** `admin`
* **Password:** `admin`

---

## Authentication

QuantumSurf uses `auth.txt` for local login control.

### Default credentials

If `auth.txt` is missing, the default credentials are:

```text
admin:admin
```

### Custom credentials

Create `auth.txt` in the project directory:

```text
yourusername:yourpassword
anotheruser:anotherpassword
```

Passwords can be plaintext or SHA-256 hashed.

---

## Optional: Stronger Auth with SessionGuard

For more control around local web tools, you can put QuantumSurf behind **SessionGuard**.

SessionGuard is a secure HTTP authentication gateway and session enforcement shield for local web tools.

It sits in front of your app, handles authentication, enforces per-user concurrent session limits, and forwards only approved traffic to your backend service.

**Flow:**

```text
client ──► cloudflared ──► SessionGuard :8000 ──► local tool (127.0.0.1:PORT)
```

Repository: [SessionGuard](https://github.com/giriaryan694-a11y/SessionGuard)

---

## Optional HTTPS Layer with http2https

If you want to access an HTTP-only local service through HTTPS, use **http2https**.

It is a Python-based CLI tool that helps you access an HTTP-only local service through HTTPS.

**Example flow:**

```text
HTTP server:  localhost:8000
HTTPS proxy:  localhost:8080
```

So instead of opening:

```text
http://IP:8000
```

you open:

```text
https://IP:8080
```

Repository: [http2https](https://github.com/giriaryan694-a11y/http2https)

---

## What the Script Auto-Installs

The script detects missing components and installs them automatically.

| Component           | What                             | How                                 | When                           |
| ------------------- | -------------------------------- | ----------------------------------- | ------------------------------ |
| Chromium (amd64)    | Chrome for Testing               | ZIP from Google CDN                 | If no Chromium found           |
| Chromium (arm64)    | Ungoogled Chromium Portable      | tar.xz from GitHub Releases         | If no Chromium found           |
| Chromium (fallback) | `chromium` or `chromium-browser` | `apt-get install`                   | If above methods fail          |
| Xvfb                | Virtual framebuffer X server     | `apt-get install xvfb`              | If `Xvfb` is not in PATH       |
| x11vnc              | VNC server for X displays        | `apt-get install x11vnc`            | If `x11vnc` is not in PATH     |
| websockify          | WebSocket-to-TCP proxy           | `pip install websockify` or `apt`   | If `websockify` is not in PATH |
| noVNC               | HTML5 VNC client                 | Downloaded from GitHub              | If not found on system         |
| xdotool             | X11 window manipulation          | `apt-get install xdotool`           | For window resize enforcement  |
| xsetroot            | X root window config             | `apt-get install x11-xserver-utils` | For black background           |
| Fonts               | Liberation, DejaVu, Noto Emoji   | `apt-get install fonts-*`           | If no system fonts found       |
| Shared libraries    | GTK3, NSS, ALSA, etc.            | `apt-get install` (from `ldd`)      | If Chromium has missing libs   |

---

## What You Must Install

These are not auto-installed.

| Package     | Why                          | Install                        |
| ----------- | ---------------------------- | ------------------------------ |
| Python 3.8+ | Runtime                      | `sudo apt install python3`     |
| pip         | Python package manager       | `sudo apt install python3-pip` |
| Flask       | Web server / auth            | `pip3 install flask`           |
| pyfiglet    | ASCII banner                 | `pip3 install pyfiglet`        |
| termcolor   | Colored terminal output      | `pip3 install termcolor`       |
| colorama    | Cross-platform color support | `pip3 install colorama`        |
| x11-utils   | `xdpyinfo` display check     | `sudo apt install x11-utils`   |
| sudo access | For apt installs             | Required                       |

### One-liner prerequisite install

```bash
sudo apt-get update && sudo apt-get install -y python3 python3-pip x11-utils && \
pip3 install flask pyfiglet termcolor colorama flask-sock
```

---

## Architecture

```text
┌─────────────────────────────────────────────────────┐
│  Your Browser (any device)                          │
│  ┌───────────────────────────────────────────────┐  │
│  │  Flask :8000 (login page / auth)              │  │
│  │  ┌─────────────────────────────────────────┐  │  │
│  │  │  iframe → noVNC vnc.html                │  │  │
│  │  │  ?autoconnect=true&resize=scale         │  │  │
│  │  │  ┌───────────────────────────────────┐  │  │  │
│  │  │  │  websockify :6080                 │  │  │  │
│  │  │  │  (WebSocket → TCP proxy)          │  │  │  │
│  │  │  │  ┌─────────────────────────────┐  │  │  │  │
│  │  │  │  │  x11vnc :5901              │  │  │  │  │
│  │  │  │  │  (-noshm -setdesktopsize)  │  │  │  │  │
│  │  │  │  │  ┌───────────────────────┐ │  │  │  │  │
│  │  │  │  │  │  Xvfb :99            │ │  │  │  │  │
│  │  │  │  │  │  ┌─────────────────┐ │ │  │  │  │  │
│  │  │  │  │  │  │  Chromium       │ │ │  │  │  │  │
│  │  │  │  │  │  │  (HEADED mode)  │ │ │  │  │  │  │
│  │  │  │  │  │  │  xdotool resize │ │ │  │  │  │  │
│  │  │  │  │  │  └─────────────────┘ │ │  │  │  │  │
│  │  │  │  │  └───────────────────────┘ │  │  │  │  │
│  │  │  │  └─────────────────────────────┘  │  │  │  │
│  │  │  └───────────────────────────────────┘  │  │  │
│  │  └─────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## Chromium Auto-Install Details

### amd64 (x86_64)

| Priority | Method                      | Source          | Size    | Speed     |
| -------- | --------------------------- | --------------- | ------- | --------- |
| 1        | Chrome for Testing          | Google CDN      | ~170 MB | ~20–40s   |
| 2        | Ungoogled Chromium Portable | GitHub Releases | ~139 MB | ~30–50s   |
| 3        | `apt install chromium`      | System repos    | varies  | up to 90s |

### arm64 (aarch64)

| Priority | Method                      | Source          | Size    | Speed     |
| -------- | --------------------------- | --------------- | ------- | --------- |
| 1        | Ungoogled Chromium Portable | GitHub Releases | ~126 MB | ~30–50s   |
| 2        | `apt install chromium`      | System repos    | varies  | up to 90s |

> Chrome for Testing does **not** support linux-arm64. Ungoogled Portable is the primary method for ARM.

---

## GUI Controls

After login, move your mouse to reveal floating controls.

| Control    | Location  | Function                     |
| ---------- | --------- | ---------------------------- |
| **⚙ RES**  | Top-right | Opens display settings panel |
| **⏻ EXIT** | Top-right | Logout                       |

### Display Settings Panel

| Feature           | Description                                                                   |
| ----------------- | ----------------------------------------------------------------------------- |
| **AUTO-DETECT**   | Reads your browser viewport size and restarts the virtual display to match it |
| **Manual W×H**    | Type any resolution from 800×600 to 3840×2160                                 |
| **Presets**       | One-click 1080p, 768p, 1440p, 720p                                            |
| **RESTART STACK** | Restarts Xvfb + Chromium + x11vnc                                             |
| **Stack Log**     | Live error/status log from all components                                     |

---

## Ports

| Port     | Service             | Binding          |
| -------- | ------------------- | ---------------- |
| **8000** | Flask (login + GUI) | `0.0.0.0`        |
| **6080** | noVNC / websockify  | `0.0.0.0`        |
| **5901** | x11vnc (VNC)        | `127.0.0.1` only |

> VNC is localhost-only. External access goes through Flask auth and the noVNC iframe.

---

## Troubleshooting

### “Can't read lock file /tmp/.X99-lock”

A stale Xvfb lock file from a previous crash. Remove it:

```bash
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
```

### “Failed to connect to server”

The stack did not start. In the GUI, click **⚙ RES** → **RESTART STACK**.

### Chromium will not start

Check missing libraries:

```bash
ldd /path/to/chrome | grep "not found"
```

Install common dependencies:

```bash
sudo apt-get install -y libgtk-3-0 libnss3 libasound2 libx11-6 \
  libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
  libcairo2 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2
```

### Port already in use

```bash
pkill -f "Xvfb :99"
pkill -f x11vnc
pkill -f websockify
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
```

### noVNC shows black screen

Chromium might not have started. Check the stack log in the **⚙ RES** panel. The VNC connection can still work even if Chromium is not running.

---

## File Structure

```text
QuantumSurf/
├── main.py              # Single-file application
├── requirements.txt     # Python dependencies
├── README.md            # This file
├── auth.txt             # Credentials (optional, create manually)
├── .chromium/           # Auto-downloaded Chromium (gitignored)
│   ├── chrome-linux64/  # Chrome for Testing (amd64)
│   └── ungoogled/       # Ungoogled Portable (arm64)
├── .novnc/              # Auto-downloaded noVNC files
└── .x11vnc.log          # x11vnc log file
```

---

## Security Notes

* VNC listens on `127.0.0.1` only
* All external access goes through Flask session auth
* CSRF tokens on login form
* Rate limiting on repeated login attempts
* Session cookies use HttpOnly and SameSite=Lax
* 4-hour session lifetime
* No VNC password needed because VNC is localhost-only and protected by Flask

---

## Supported Platforms

| OS                  | Arch            | Status          |
| ------------------- | --------------- | --------------- |
| Debian / Ubuntu     | x86_64 (amd64)  | ✅ Full support  |
| Debian / Ubuntu     | aarch64 (arm64) | ✅ Full support  |
| Docker / Containers | amd64 / arm64   | ✅ Auto-detected |
| GitHub Codespaces   | amd64           | ✅ Tested        |
| TermuxCodeSpace     | arm64           | ✅ Tested        |
| Other Linux         | amd64 / arm64   | ⚠️ Requires apt |
| macOS / Windows     | —               | ❌ Linux only    |

---

## Credits

**Made By Aryan Giri | giriaryan694-a11y**

GitHub: https://github.com/giriaryan694-a11y

### Built With

* noVNC — HTML5 VNC client
* websockify — WebSocket proxy
* x11vnc — VNC server for X11
* Xvfb — Virtual framebuffer
* Chrome for Testing — Self-contained Chrome (amd64)
* Ungoogled Chromium Portable — Portable Chromium (arm64)
* Flask — Web framework

---
