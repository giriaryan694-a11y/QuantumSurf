# 🌊 QuantumSurf

> **Remote Browser Isolation (RBI) Privacy Toolkit**
>
> **Direct CDP · Real Chrome · No Playwright · No Snap · Single File**

**Author:** [Aryan Giri](https://github.com/giriaryan694-a11y)  
**Repository:** [QuantumSurf](https://github.com/giriaryan694-a11y/QuantumSurf)  
**Version:** Development  
**License:** Educational / Authorized Security Research Use Only

---

## 🛡️ Disclaimer

**QuantumSurf is intended exclusively for authorized security research, red teaming, privacy testing, and educational purposes.**

> ⚠️ **Always obtain explicit written permission before testing or interacting with any system you do not own.**
>
> The author assumes no liability for misuse, abuse, or any illegal activity conducted with this tool. Users are solely responsible for ensuring compliance with all applicable laws and regulations in their jurisdiction.

---

## 🚀 What QuantumSurf Does

QuantumSurf is a **Remote Browser Isolation (RBI)** tool that executes web content on a remote machine and streams only the visual output back to the client.

### Core Principle

| Local Machine | Remote Server |
|---------------|---------------|
| Receives screenshots / visual frames only | Executes all web content (JavaScript, DOM, cookies) |
| Sends mouse and keyboard input | Renders pages using real Chrome |
| Never loads target site directly | Handles all network requests |

### Use Cases

- 🔒 **Privacy-focused browsing** — isolate your identity from websites
- 🧪 **Browser isolation experiments** — test sandboxing techniques
- 🎣 **Phishing & malvertising analysis** — safely inspect suspicious content
- 🛡️ **Safe interaction with untrusted web content** — analyze without exposure
- 🤖 **Automation & security research** — controlled browser environments

---

## ✨ Features

- ✅ **True Remote Browser Isolation** — full execution separation
- ✅ **Direct CDP Control** — native Chrome DevTools Protocol, no abstraction layers
- ✅ **Real Chrome Support** — uses official Chrome, not Chromium derivatives
- ✅ **No Playwright Dependency** — lightweight, no heavy automation frameworks
- ✅ **No Snap / Flatpak Required** — direct binary execution
- ✅ **Portable Chrome Management** — automatic download and setup
- ✅ **Stealth-Oriented Fingerprint Masking** — reduce detection footprint
- ✅ **Virtual Keyboard Support** — type safely without local keylogging exposure
- ✅ **Desktop / Mobile Viewport Switching** — responsive testing on demand
- ✅ **User-Agent Spoofing** — mask browser identity
- ✅ **Source Viewing & Page Download Tools** — inspect raw content
- ✅ **Container-Friendly** — runs in Docker, WSL, cloud shells, and VPS environments
- ✅ **Cloud-Ready** — tested on Google Cloud Shell and GitHub Codespaces

---

## 🖼️ Screenshots

### Main Interface
<img width="1919" height="912" alt="QuantumSurf Main Interface" src="https://github.com/user-attachments/assets/afd3daaa-267f-4b45-a3f8-fca82b472f9f" />

### Terminal Viewport Mode
<img width="924" height="821" alt="QuantumSurf Terminal Mode" src="https://github.com/user-attachments/assets/200e3e60-5d14-43f4-b39b-8e8ba368f9e3" />

> Replace or add more screenshots in the `screenshots/` directory as needed.

---

## 🧠 How It Works

QuantumSurf operates on a simple but powerful model:

```
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
                                                  │  Real Chrome  │
                                                  │  (Headless)   │
                                                  └───────────────┘
```

### Architecture Breakdown

| Component | Technology | Responsibility |
|-----------|-----------|----------------|
| Web UI | Flask (Python) | Serves frontend, handles HTTP requests, manages sessions |
| CDP Worker | WebSocket Client | Translates user actions to Chrome DevTools Protocol commands |
| Browser Engine | Google Chrome | Renders web pages, executes JavaScript, manages cookies |

### Data Flow

1. **User** opens the web interface at `http://localhost:8000`
2. **Flask Server** authenticates the user and serves the control UI
3. **CDP Worker** launches Chrome with remote debugging enabled on port `9222`
4. **Chrome** navigates to the target URL and renders the page
5. **Screenshot Stream** captures frames and sends them to the client
6. **User Input** (clicks, keystrokes) is forwarded to Chrome via CDP
7. **Target Site** never executes on the local machine — only pixels are transmitted

---

## 📋 Prerequisites

Before installing, ensure your environment meets the following requirements:

| Requirement | Details |
|-------------|---------|
| **Operating System** | Linux (Ubuntu/Debian), WSL2, macOS, or compatible container |
| **Python** | Version 3.8 or higher |
| **Internet Access** | Outbound connectivity for Chrome download and browsing |
| **Memory** | Minimum 2GB RAM recommended (4GB+ for smooth streaming) |
| **Chrome Compatibility** | GLIBC 2.31+ and required shared libraries (see Installation) |

> **Note:** This tool has been primarily tested on **GitHub Codespaces** with a **4-core instance**. Performance may vary on lower-spec environments.

---

## ⚙️ Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/giriaryan694-a11y/QuantumSurf.git
cd QuantumSurf
```

### Step 2: Install System Dependencies

Chrome requires specific shared libraries for rendering, audio, and GPU acceleration. Missing libraries will cause Chrome to crash or display a blank screen.

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

> **Tip:** On Ubuntu 20.04 and earlier, use `libasound2` instead of `libasound2t64`.

### Step 3: Set Up Python Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 4: Fix Chrome Permissions (If Needed)

If you encounter Chrome-related errors on first run, set the correct permissions:

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

Open your browser and navigate to:

```
http://localhost:8000
```

### Default Login Credentials

| Field | Value |
|-------|-------|
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

Replace with your desired `username:password` combination. Restart the server for changes to take effect.

---

## 🔧 Debugging

If Chrome fails to start, the screen remains blank, or the page freezes, enable debug mode:

```bash
# Kill any existing Chrome processes
pkill -9 -f "chrome" 2>/dev/null
sleep 1

# Start with debug logging
QS_DEBUG=1 python3 main.py
```

### Common Debug Signals

| Error Message | Meaning | Solution |
|---------------|---------|----------|
| `Chrome exited (code X)` | Chrome crashed unexpectedly | Check missing libraries with `ldd` |
| `error while loading shared libraries` | Missing system dependency | Reinstall system dependencies (Step 2) |
| `Handshake status 403` | CDP WebSocket origin rejected | Check firewall / proxy settings |
| Blank screen after launch | Chrome startup failure | Run `QS_DEBUG=1` and check logs |
| `Port 9222 already in use` | Zombie Chrome process | Run `pkill -9 -f "chrome"` |

---

## 🛠️ Troubleshooting

### Blank Screen After Loading

**Cause:** Chrome crashed or a required shared library is missing.

**Solution:**
1. Reinstall system dependencies (see Installation Step 2)
2. Run with `QS_DEBUG=1` to identify the exact error
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
- Upgrade to a machine with more resources (4+ cores recommended)
- Close unnecessary background applications
- Reduce browser viewport size in settings
- Lower screenshot capture frequency if configurable

### Chrome Permission Denied

**Cause:** Chrome binaries lack execute permissions.

**Solution:**
```bash
chmod +x /workspaces/QuantumSurf/.chrome/chrome-linux64/chrome
chmod +x /workspaces/QuantumSurf/.chrome/chrome-linux64/chrome_crashpad_handler
```

---

## 🌍 Remote Access & Tunneling

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

> **Security Warning:** When exposing via tunnel, ensure strong credentials are set in `auth.txt`. Do not use default credentials in production or public-facing deployments.

---

## ☁️ Cloud Deployment

QuantumSurf is optimized for free cloud development environments:

| Platform | Notes |
|----------|-------|
| **GitHub Codespaces** | Primary test environment; 4-core recommended |
| **Google Cloud Shell** | Pre-installed dependencies; may need library updates |
| **Gitpod** | Works with standard Ubuntu image |
| **Docker / VPS** | Use official Ubuntu/Debian base images |
| **WSL2** | Enable systemd if using WSL on Windows 11 |

### Codespaces Quick Start

1. Open the repository in GitHub Codespaces (4-core instance)
2. Run system dependency installation (Step 2)
3. Set up Python environment (Step 3)
4. Fix Chrome permissions (Step 4)
5. Start the server and open the forwarded port

---

## 🔐 Browser Control Features

QuantumSurf provides direct browser manipulation through the web UI:

| Feature | Description |
|---------|-------------|
| **User-Agent Spoofing** | Override browser identification string |
| **Cookie Management** | Load, edit, or clear cookies for sessions |
| **Viewport Profiles** | Switch between desktop, tablet, and mobile resolutions |
| **Mobile Emulation** | Enable touch events and device-specific behavior |
| **Virtual Keyboard** | Input text without local keyboard event exposure |
| **Direct CDP Interaction** | Send raw Chrome DevTools Protocol commands |
| **Page Source View** | Inspect raw HTML/JS before rendering |
| **Download Page Content** | Save full page source for offline analysis |

---

## 📚 Project Notes

### What QuantumSurf Is

- A **remote viewing and interaction layer** for web content
- A **browser isolation experiment** for privacy research
- A **security analysis tool** for controlled web interaction

### What QuantumSurf Is NOT

- ❌ A VPN — does not encrypt all traffic, only isolates browser execution
- ❌ A Proxy — does not route all system traffic, only browser content
- ❌ A Traditional Browser Automation Wrapper — no Playwright, Selenium, or Puppeteer abstraction

### Security Model

QuantumSurf assumes the **remote server is trusted** and the **client is potentially untrusted** (or vice versa for privacy). All web execution happens server-side; the client only receives visual frames.

---

## 📦 Requirements

Core dependencies (see `requirements.txt` for full list):

```text
flask>=2.0.0
pyfiglet>=0.8.0
termcolor>=2.0.0
colorama>=0.4.4
websocket-client>=1.0.0
psutil>=5.8.0
```

Install all dependencies:

```bash
pip install -r requirements.txt
```

---

## 🤝 Contributing

Contributions are welcome for educational and security research purposes.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

Please ensure all contributions comply with the project's intended use case and include appropriate documentation.

---

## 📜 License

This project is provided for **educational and authorized security research purposes only**.

By using this software, you agree to:
- Use it only on systems you own or have explicit permission to test
- Not use it for any illegal, unethical, or unauthorized activities
- Assume full responsibility for your actions

The author disclaims all liability for misuse or damage arising from the use of this software.

---

## 🧬 Final Note

QuantumSurf is a **secure viewing glass** between you and the modern web.

It keeps execution remote, the client lightweight, and the browser under your direct control.

**Built with Python. Powered by Chrome. Designed for Privacy.**

---

**Made by [Aryan Giri](https://github.com/giriaryan694-a11y)**

⭐ Star this repository if you find it useful for your research!
