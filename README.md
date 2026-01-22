# 🌐 QuantumSurf

**Remote Browser Isolation • Privacy • Stealth Automation**

> *“You are not browsing the web. You are watching it happen somewhere else.”*

**QuantumSurf** is an advanced **Remote Browser Isolation (RBI)** tool that allows users to browse the internet **without executing any website code on their local machine**. All web execution happens on a remote server, while the client only receives a live visual stream and sends interaction coordinates.

GitHub Repository: [https://github.com/giriaryan694-a11y/QuantumSurf/](https://github.com/giriaryan694-a11y/QuantumSurf/)

---

## 🧠 What Is QuantumSurf?

Modern websites aggressively track users, fingerprint browsers, and sometimes deliver malicious scripts. Traditionally, all of this code executes directly on your device.

**QuantumSurf changes the model.**

* Websites run inside an isolated Chromium browser on a server
* Your system never executes untrusted JavaScript
* You interact only with a streamed visual output
* Mouse clicks and interactions are translated and executed server-side

This creates a strong separation between **you** and **the web**.

---

## 🎥 Architecture Overview

```
[ User Browser ]
      |
      |  MJPEG Stream (Images Only)
      |
[ Flask Server ]  <-- Command Queue -->  [ Browser Worker Thread ]
                                              |
                                              |
                                     [ Playwright + Chromium ]
```

**Key Idea:** Isolation by design — pixels go to the user, execution stays on the server.

---

## ⚙️ How It Works (Technical Breakdown)

### 🔹 Thread-Safe Browser Control

Playwright is single-threaded and blocking. To avoid server lockups:

* Flask handles HTTP requests
* A dedicated **Browser Worker thread** controls Playwright
* Commands are passed through a **thread-safe queue**

Flask never talks to the browser directly.

---

### 🔹 MJPEG Live Streaming

Instead of heavy video codecs:

* The server captures screenshots ~20 times per second
* Each frame is sent as a JPEG
* Streaming uses `multipart/x-mixed-replace`

This provides:

* Low latency
* Simplicity
* Compatibility with most networks

---

### 🔹 Real-Time Interaction Mapping

* Client captures mouse clicks on the video feed
* JavaScript calculates scaled X/Y coordinates
* Coordinates are sent back to the server
* The worker simulates **real mouse events** in Chromium

This enables smooth, near-real-time interaction.

---

## 🕵️ Stealth & Evasion

QuantumSurf includes stealth techniques to avoid automated browser detection:

* Removes `AutomationControlled` flags
* Injects JavaScript overrides
* Spoofs a real Windows 10 User-Agent
* Mimics human-driven browser behavior

Useful for bot-detection research and defensive analysis.

---

## 📱 Identity & Device Control

Using the built-in configuration system, users can:

* Change User-Agent strings
* Inject cookies (JSON format)
* Instantly load authenticated sessions
* Toggle **Mobile View**, rebuilding the browser context to emulate a smartphone with touch events

---

## ⭐ Major Advantage: Run It on Google Cloud Shell

One of QuantumSurf’s strongest advantages is that it can run **entirely inside Google Cloud Shell**.

### Why This Is Powerful

* Free temporary Linux VM provided by Google
* No local installation required
* Fresh environment every session
* Runs in Google’s infrastructure
* Ideal for privacy and isolation research

### Recommended Setup

* **Terminal Tab 1:** Run QuantumSurf server
* **Terminal Tab 2:** Run a tunneling service (Cloudflared or Ngrok)

This allows you to securely access QuantumSurf from anywhere on the internet.

---

## 🚀 Installation & Usage

### 1️⃣ Clone the Repository

```bash
git clone https://github.com/giriaryan694-a11y/QuantumSurf.git
cd QuantumSurf
```

---

### 2️⃣ Install Python Dependencies

```bash
pip install -r requirements.txt
```

---

### 3️⃣ Install Browser Binaries (CRUCIAL)

Playwright does **not** download browsers automatically.
You must run:

```bash
python -m playwright install chromium
```

If this step is skipped, the tool **will crash**.

---

### 4️⃣ Run QuantumSurf

```bash
python main.py
```

The server will start listening on the configured port.

---

## 🌍 Exposing QuantumSurf to the Internet (Optional)

### 🔹 Cloudflared (Recommended)

Download and install:
[https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)

Run:

```bash
cloudflared tunnel --url http://localhost:8000
```

---

### 🔹 Ngrok (Alternative)

Download:
[https://ngrok.com/download/](https://ngrok.com/download/)

Run:

```bash
ngrok http 8000
```

---

## 📚 References & Further Reading

* QuantumSurf Repository:
  [https://github.com/giriaryan694-a11y/QuantumSurf/](https://github.com/giriaryan694-a11y/QuantumSurf/)

* Google Cloud Shell for Cybersecurity:
  [https://github.com/giriaryan694-a11y/gcloud-for-cybersec](https://github.com/giriaryan694-a11y/gcloud-for-cybersec)

* Google Cloud Shell Documentation:
  [https://cloud.google.com/shell](https://cloud.google.com/shell)

* Remote Browser Isolation (Concept):
  [https://en.wikipedia.org/wiki/Remote_browser_isolation](https://en.wikipedia.org/wiki/Remote_browser_isolation)

* Cloudflare Tunnel:
  [https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)

---

## 🧬 Final Note

QuantumSurf is not a proxy.
It is not a VPN.
It is not traditional automation.

It is a **secure viewing glass** — an invisible window between you and the modern web.

**Built with Python. Powered by Playwright.**

**Made by Aryan Giri**
