# 🌊 QuantumSurf

> **Remote Browser Isolation (RBI) Privacy Toolkit**
> **Direct CDP · Real Chrome · No Playwright · No Snap · Single File**

**Made by:** [Aryan Giri](https://github.com/giriaryan694-a11y)
**Repository:** [QuantumSurf](https://github.com/giriaryan694-a11y/QuantumSurf)
**Version:** Development

---

## 🛡️ Disclaimer

QuantumSurf is intended for **authorized security research, red teaming, privacy testing, and educational use only**.
Always ensure you have explicit permission before testing or interacting with any system.

The author is not responsible for misuse, abuse, or any illegal activity.

---

## 🚀 What QuantumSurf Does

QuantumSurf is a **Remote Browser Isolation (RBI)** tool that runs a browser on a remote machine and streams only the visual output back to the client.

That means:

* websites execute on the remote host, not on your local device
* your local machine only receives screenshots / visual frames
* user input is sent back to the remote browser
* the browser can be controlled through **Chrome DevTools Protocol (CDP)**

This makes QuantumSurf useful for:

* privacy-focused browsing
* browser isolation experiments
* phishing / malvertising analysis
* safe interaction with untrusted web content
* automation and security research

---

## ✨ Features

* **True Remote Browser Isolation**
* **Direct CDP control**
* **Real Chrome support**
* **No Playwright dependency**
* **No Snap / Flatpak required**
* **Portable Chrome download and management**
* **Stealth-oriented browser fingerprint masking**
* **Virtual keyboard support**
* **Desktop / mobile viewport switching**
* **User-Agent spoofing**
* **Source viewing and page download tools**
* **Container-friendly**
* **Works well in Google Cloud Shell, GitHub Codespaces, Docker, WSL, and VPS environments**

---

## 🔐 Default Login

If authentication is enabled in your build, the default credentials are:

* **Username:** `test`
* **Password:** `test`

You can change them by editing the `auth.txt` file in the project root.

Example format:

```text
test:test
```

---

## 🖼️ Screenshots

Add your screenshots here:

<img width="1919" height="912" alt="Screenshot 2026-07-25 133151" src="https://github.com/user-attachments/assets/afd3daaa-267f-4b45-a3f8-fca82b472f9f" />
<img width="924" height="821" alt="Screenshot 2026-07-25 133237" src="https://github.com/user-attachments/assets/200e3e60-5d14-43f4-b39b-8e8ba368f9e3" />

You can replace or add more images later.

---

## 🧠 How It Works

QuantumSurf follows a simple model:

1. A browser runs on a remote server.
2. The server captures the rendered page as screenshots.
3. The screenshots are streamed to the client.
4. Mouse and keyboard actions are translated into browser input events.
5. The target site never runs on the local machine.

---

## 🏗️ Architecture

```text
[ User Browser ]
       |
       |  Screenshot Stream / UI
       v
[ Flask Server ]  <---->  [ CDP Worker ]
                               |
                               v
                        [ Real Chrome ]
```

### Core idea

* **Flask** handles the web UI and requests
* a **CDP worker** manages Chrome
* Chrome renders the page remotely
* the frontend displays the live stream and sends input events back

---

## 📋 Prerequisites

* **Linux / Ubuntu / Debian / WSL2 / macOS**
* **Python 3.8+**
* **Outbound internet access**
* **Chrome-compatible environment**

---

## ⚙️ Installation

### 1) Clone the repository

```bash
git clone https://github.com/giriaryan694-a11y/QuantumSurf.git
cd QuantumSurf
```

### 2) Install system dependencies

On Linux, Chrome needs shared libraries for rendering and audio support.
If these are missing, Chrome may fail to start or show a blank screen.

```bash
sudo apt-get update && sudo apt-get install -y \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 \
  libxshmfence1 libx11-xcb1 libxcb-dri3-0
```

> On older Ubuntu versions, `libasound2` may be used instead of `libasound2t64`.

### 3) Install Python dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4) Start QuantumSurf

```bash
python3 main.py
```

---

## 🎮 Usage

Once the server is running, open the local web interface in your browser.

```text
http://localhost:8000
```

### Default login

* **Username:** `test`
* **Password:** `test`

To change credentials, edit the `auth.txt` file in the project root.

---

## 🔧 Debugging

If Chrome does not start, the screen is blank, or the page freezes, try running in debug mode.

```bash
pkill -9 -f "chrome" 2>/dev/null; sleep 1
QS_DEBUG=1 python3 main.py
```

### Useful debug signals

* `Chrome exited (code X)` → Chrome crashed
* `error while loading shared libraries` → missing system dependency
* `Handshake status 403` → CDP WebSocket origin issue
* blank screen after launch → often a Chrome startup / library problem

---

## 🛠️ Troubleshooting

### Blank screen after loading

Usually means Chrome crashed or a required shared library is missing.
Reinstall the system dependencies and rerun with `QS_DEBUG=1`.

### Port 9222 already in use

An old Chrome process may still be alive.

```bash
pkill -9 -f "chrome"
```

### Wrong websocket package installed

Use `websocket-client`, not `websocket`.

```bash
pip uninstall websocket -y
pip install websocket-client
```

### Laggy stream

This can happen in low-CPU environments or containers with limited resources.
Using a stronger machine or fewer background tasks may help.

---

## 🌍 Running Through a Tunnel

To access QuantumSurf from outside your machine, you can expose the local server through a tunnel.

### Cloudflared

```bash
cloudflared tunnel --url http://localhost:8000
```

### Ngrok

```bash
ngrok http 8000
```

---

## ☁️ Easy Deployment Options

QuantumSurf is especially handy in **free cloud environments** like:

* **Google Cloud Shell**
* **GitHub Codespaces**

These are useful because they give you a quick Linux environment without local setup.
You can run the server in one terminal and a tunnel in another terminal for remote access.

---

## 🔐 Browser Control Features

QuantumSurf supports browser-side controls such as:

* changing the **User-Agent**
* loading cookies
* switching viewport profiles
* mobile emulation
* virtual key input
* direct interaction with remote Chrome

---

## 📚 Project Notes

QuantumSurf is not:

* a VPN
* a proxy
* a traditional browser automation wrapper

It is a **remote viewing and interaction layer** for web content.

---

## 🧩 Requirements

Example dependencies:

```txt
flask
pyfiglet
termcolor
colorama
websocket-client
psutil
```

---

## 📜 License

This project is intended for educational and authorized security research purposes.

---

## 🧬 Final Note

QuantumSurf is a **secure viewing glass** between you and the modern web.

It keeps execution remote, keeps the client lightweight, and keeps the browser under direct control.

**Built with Python. Powered by Chrome.**

**Made by Aryan Giri**
