# 🌊 QuantumSurf

> **Remote Browser Isolation (RBI) Privacy Toolkit**
> **Direct CDP · Real Chrome · No Playwright · No Snap · Single File**

**Author:** [Aryan Giri](https://github.com/giriaryan694-a11y)
**Repository:** [QuantumSurf](https://github.com/giriaryan694-a11y/QuantumSurf)
**Version:** Development
**License:** Educational / Authorized Security Research Use Only

---

## 🛡️ Disclaimer

**QuantumSurf is intended exclusively for authorized security research, red teaming, privacy testing, and educational purposes.**

> ⚠️ Always obtain explicit permission before testing or interacting with any system you do not own.
> The author assumes no liability for misuse, abuse, or illegal activity conducted with this tool.

---

## 🚀 What QuantumSurf Does

QuantumSurf is a **Remote Browser Isolation (RBI)** tool that runs web content on a remote machine and streams only the visual output back to the client.

### Core Principle

| Local Machine                             | Remote Server                                       |
| ----------------------------------------- | --------------------------------------------------- |
| Receives screenshots / visual frames only | Executes all web content (JavaScript, DOM, cookies) |
| Sends mouse and keyboard input            | Renders pages using real Chrome                     |
| Never loads target site directly          | Handles all network requests                        |

### Use Cases

* **Privacy-focused browsing** — isolate your identity from websites
* **Browser isolation experiments** — test sandboxing techniques
* **Phishing & malvertising analysis** — inspect suspicious content safely
* **Safe interaction with untrusted web content** — analyse without exposure
* **Automation & security research** — controlled browser environments

---

## ✨ Features

* ✅ **True Remote Browser Isolation** — full execution separation
* ✅ **Direct CDP Control** — native Chrome DevTools Protocol, no abstraction layers
* ✅ **Real Chrome Support** — uses Chrome directly
* ✅ **No Playwright Dependency** — lightweight and simple
* ✅ **No Snap / Flatpak Required** — direct binary execution
* ✅ **Portable Chrome Management** — automatic download and setup
* ✅ **Stealth-Oriented Fingerprint Masking** — reduce detection footprint
* ✅ **Virtual Keyboard Support** — type safely without local keylogging exposure
* ✅ **Desktop / Mobile Viewport Switching** — responsive testing on demand
* ✅ **User-Agent Spoofing** — mask browser identity
* ✅ **Source Viewing & Page Download Tools** — inspect raw content
* ✅ **Container-Friendly** — works in Docker, WSL, cloud shells, and VPS environments
* ✅ **Cloud-Ready** — tested on GitHub Codespaces and Google Cloud Shell

---

## 🖼️ Screenshots

### Main Interface

![QuantumSurf Main Interface](https://github.com/user-attachments/assets/afd3daaa-267f-4b45-a3f8-fca82b472f9f)

### Terminal Viewport Mode

![QuantumSurf Terminal Mode](https://github.com/user-attachments/assets/200e3e60-5d14-43f4-b39b-8e8ba368f9e3)

> Keep or replace these screenshots in the `screenshots/` directory as needed.

---

## 🧠 How It Works

QuantumSurf uses a simple browser-isolation flow:

```text
┌─────────────────┐      Screenshot Stream      ┌─────────────────┐
│                 │◄────────────────────────────│                 │
│  User Browser   │                             │  Flask Server   │
│  (Client)       │────────────────────────────►│  (Port 8000)    │
│                 │    Mouse / Keyboard Input   │                 │
└─────────────────┘                             └────────┬────────┘
                                                         │
                                                         │ CDP Commands
                                                         ▼
                                                   ┌───────────────┐
                                                   │  CDP Worker   │
                                                   │  (WebSocket)  │
                                                   └───────┬───────┘
                                                           │
                                                           ▼
                                                   ┌───────────────┐
                                                   │  Real Chrome   │
                                                   │  (Headless)    │
                                                   └───────────────┘
```

### Architecture Breakdown

| Component      | Technology       | Responsibility                                               |
| -------------- | ---------------- | ------------------------------------------------------------ |
| Web UI         | Flask (Python)   | Serves frontend, handles HTTP requests, manages sessions     |
| CDP Worker     | WebSocket Client | Translates user actions to Chrome DevTools Protocol commands |
| Browser Engine | Google Chrome    | Renders web pages, executes JavaScript, manages cookies      |

### Data Flow

1. User opens the web interface at `http://localhost:8000`
2. Flask authenticates the user and serves the control UI
3. CDP Worker launches Chrome with remote debugging enabled on port `9222`
4. Chrome navigates to the target URL and renders the page
5. Screenshot stream captures frames and sends them to the client
6. User input is forwarded to Chrome via CDP
7. The target site never executes on the local machine — only pixels are transmitted

---

## 📋 Prerequisites

| Requirement          | Details                                                     |
| -------------------- | ----------------------------------------------------------- |
| Operating System     | Linux (Ubuntu/Debian), WSL2, macOS, or compatible container |
| Python               | 3.8 or higher                                               |
| Internet Access      | Required for Chrome download and browsing                   |
| Memory               | Minimum 2 GB RAM recommended (4 GB+ for smoother streaming) |
| Chrome Compatibility | GLIBC 2.31+ and required shared libraries                   |

> **Note:** This tool has been primarily tested on **GitHub Codespaces (Ubuntu)** with a **4-core instance**.

---

## ⚙️ Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/giriaryan694-a11y/QuantumSurf.git
cd QuantumSurf
```

### Step 2: Install System Dependencies

Chrome needs specific shared libraries for rendering, audio, and GPU acceleration. Missing libraries can cause crashes or a blank screen.

**Ubuntu 22.04+ / Debian 12+:**

```bash
sudo apt-get update && sudo apt-get install -y \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 \
  libxshmfence1 libx11-xcb1 libxcb-dri3-0
```

**Older Ubuntu (20.04 and below):**

```bash
sudo apt-get update && sudo apt-get install -y \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
  libxshmfence1 libx11-xcb1 libxcb-dri3-0
```

> Tip: On Ubuntu 20.04 and earlier, use `libasound2` instead of `libasound2t64`.

### Step 3: Set Up Python Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 4: Fix Chrome Permissions (If Needed)

```bash
chmod +x /workspaces/QuantumSurf/.chrome/chrome-linux64/chrome_crashpad_handler
chmod +x /workspaces/QuantumSurf/.chrome/chrome-linux64/chrome
```

> Adjust the path if your installation directory differs from the default Codespaces path.

### Step 5: Start QuantumSurf

```bash
python3 main.py
```

The server will start on `http://localhost:8000` by default.

---

## 🎮 Usage

### Access the Web Interface

Open your browser and go to:

```text
http://localhost:8000
```

### Default Login Credentials

| Field    | Value  |
| -------- | ------ |
| Username | `test` |
| Password | `test` |

### Changing Credentials

Edit the `auth.txt` file in the project root:

```bash
nano auth.txt
```

Format:

```text
test:test
```

You can change the credentials at any time by editing `auth.txt` and restarting the server.

> If `auth.txt` is missing, QuantumSurf falls back to the default `test:test` login.

---

## 🔐 Authentication Options

QuantumSurf supports simple local authentication through `auth.txt`.

### Built-in Auth File

* One credential pair per line
* Format: `username:password`
* Restart the server after changes

### Optional Layer: SessionGuard

For stronger control around local web tools, you can place QuantumSurf behind **SessionGuard**.

**SessionGuard** is a secure HTTP authentication gateway and session enforcement shield for local web tools.

It sits in front of your app, handles authentication, enforces per-user concurrent session limits, and forwards only approved traffic to your backend service.

**Flow:**

```text
client ──► cloudflared ──► SessionGuard :8000 ──► local tool (127.0.0.1:PORT)
```

Use it when you want:

* per-user session control
* a web admin panel
* a cleaner gateway in front of private tools

Repository: [SessionGuard](https://github.com/giriaryan694-a11y/SessionGuard)

### Optional HTTPS Layer: http2https

If a browser or client expects HTTPS, you can put **http2https** in front of an HTTP-only local service.

**http2https** is a Python-based CLI tool that lets you access an HTTP-only local service through HTTPS.

Example flow:

```text
HTTP server:   localhost:8000
HTTPS proxy:   localhost:8080
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

## 🧭 Tested Environments

| Environment        | Notes                                   | Status       |
| ------------------ | --------------------------------------- | ------------ |
| GitHub Codespaces  | Ubuntu / 4-core instance                | ✅ Tested     |
| Ubuntu             | Standard Linux desktop/server           | ✅ Tested     |
| TermuxCodeSpace    | Termux-based isolated Ubuntu codespaces | ✅ Tested     |
| Google Cloud Shell | Works with dependency updates           | ✅ Tested     |
| Kali Linux         | Useful for lab usage                    | ✅ Compatible |

TermuxCodeSpace repository: [TermuxCodeSpace](https://github.com/giriaryan694-a11y/TermuxCodeSpace)

---

## 🔧 Debugging

If Chrome fails to start, the screen stays blank, or the page freezes, enable debug mode:

```bash
# Kill any existing Chrome processes
pkill -9 -f "chrome" 2>/dev/null
sleep 1

# Start with debug logging
QS_DEBUG=1 python3 main.py
```

### Common Debug Signals

| Error Message                          | Meaning                       | Solution                           |
| -------------------------------------- | ----------------------------- | ---------------------------------- |
| `Chrome exited (code X)`               | Chrome crashed unexpectedly   | Check missing libraries with `ldd` |
| `error while loading shared libraries` | Missing system dependency     | Reinstall system dependencies      |
| `Handshake status 403`                 | CDP WebSocket origin rejected | Check firewall / proxy settings    |
| Blank screen after launch              | Chrome startup failure        | Run `QS_DEBUG=1` and check logs    |
| `Port 9222 already in use`             | Zombie Chrome process         | Run `pkill -9 -f "chrome"`         |

---

## 🛠️ Troubleshooting

### Blank Screen After Loading

**Cause:** Chrome crashed or a required shared library is missing.

**Solution:**

1. Reinstall system dependencies
2. Run with `QS_DEBUG=1`
3. Verify Chrome binary permissions are executable

### Port 9222 Already in Use

**Cause:** A previous Chrome process is still running.

**Solution:**

```bash
pkill -9 -f "chrome"
```

### Wrong WebSocket Package Installed

**Cause:** The `websocket` package conflicts with `websocket-client`.

**Solution:**

```bash
pip uninstall websocket -y
pip install websocket-client
```

### Laggy or Stuttering Stream

**Cause:** Insufficient CPU or memory allocation.

**Solutions:**

* Use a machine with more resources
* Close unnecessary background applications
* Reduce browser viewport size
* Lower screenshot capture frequency if configurable

### Chrome Permission Denied

**Cause:** Chrome binaries lack execute permissions.

**Solution:**

```bash
chmod +x /workspaces/QuantumSurf/.chrome/chrome-linux64/chrome
chmod +x /workspaces/QuantumSurf/.chrome/chrome-linux64/chrome_crashpad_handler
```

---

## 🌍 Remote Access & Tunnelling

To access QuantumSurf from outside your local machine, use a secure tunnel.

### Option 1: Cloudflared (Recommended)

```bash
cloudflared tunnel --url http://localhost:8000
```

### Option 2: Ngrok

```bash
ngrok http 8000
```

### Option 3: LocalTunnel

```bash
npx localtunnel --port 8000
```

> Security note: when exposing via tunnel, always set strong credentials in `auth.txt`. Do not use defaults in public deployments.

---

## 📚 Project Notes

### What QuantumSurf Is

* A **remote viewing and interaction layer** for web content
* A **browser isolation experiment** for privacy research
* A **security analysis tool** for controlled web interaction

### What QuantumSurf Is Not

* A VPN
* A proxy for all system traffic
* A traditional browser automation wrapper

### Security Model

QuantumSurf assumes the **remote server is trusted** and the **client is potentially untrusted**. All web execution happens server-side; the client only receives visual frames.

---

## 📦 Requirements

Core dependencies:

```text
flask>=2.0.0
pyfiglet>=0.8.0
termcolor>=2.0.0
colorama>=0.4.4
websocket-client>=1.0.0
psutil>=5.8.0
```

Install them with:

```bash
pip install -r requirements.txt
```

---

## 🤝 Contributing

Contributions are welcome for educational and security research purposes.

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

Please keep contributions aligned with the project’s intended use case and include documentation where needed.

---

## 📜 License

This project is provided for **educational and authorized security research purposes only**.

By using this software, you agree to:

* Use it only on systems you own or have explicit permission to test
* Not use it for illegal, unethical, or unauthorized activities
* Assume full responsibility for your actions

---

## 🧬 Final Note

QuantumSurf is a **secure viewing glass** between you and the modern web.

It keeps execution remote, the client lightweight, and the browser under your direct control.

**Built with Python. Powered by Chrome. Designed for Privacy.**

---

**Made by [Aryan Giri](https://github.com/giriaryan694-a11y)**
