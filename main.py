#!/usr/bin/env python3
"""
QuantumSurf — Remote Browser Isolation Privacy Toolkit
Made by Aryan Giri
Direct CDP · Real Chromium · No Playwright · No Snap · Single File
Multi-arch: amd64 (x86_64) + arm64 (aarch64)

FIXED: symbol lookup error jpeg_crop_scanline LIBJPEG_6.2
FIXED: Scroll up/down buttons + hardware keyboard keys not working
FIXED: CSS/JS not rendering properly (rasterizer flag conflict)
FIXED: GitHub, DuckDuckGo, and CSP-heavy sites not loading
  - Added Page.setBypassCSP before every navigation (bypasses strict CSP)
  - Added Network.setBypassServiceWorker (prevents SW from blocking requests)
  - Added --disable-site-isolation-trials + IsolateOrigins,SitePerProcess
  - Added AcceptCHFrame,MediaRouter to disabled features
  - Made stealth script CSP-resilient (try/catch wrapper)
  - Re-assert CSP bypass + SW bypass before each navigation
"""
import os,re,json,time,html,hmac,hashlib,secrets,base64,tempfile,sys
import threading,queue,copy,shutil,subprocess,multiprocessing,signal
import urllib.request,zipfile,platform,ctypes.util,tarfile,io
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
CHROME_DIR=BASE/".chromium"
LIBS_DIR=CHROME_DIR/"libs"
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

def _detect_arch():
    m=platform.machine().lower()
    if m in ("x86_64","amd64"): return "amd64"
    if m in ("aarch64","arm64","armv8l","aarch64_be"): return "arm64"
    return None
ARCH=_detect_arch()
ARCH_LABEL=platform.machine()

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

def _validate_chromium(path):
    if not path or not os.path.isfile(path) or not os.access(path,os.X_OK): return False
    try:
        env=_build_lib_env()
        r=subprocess.run([path,"--version"],capture_output=True,timeout=10,text=True,env=env)
        o=(r.stdout+r.stderr).lower()
        if "snap" in o or "requires" in o: return False
        if r.returncode!=0: return False
        return any(k in o for k in ["chromium","chrome"])
    except: return False

def _binary_exists(path):
    if not path or not os.path.isfile(path) or not os.access(path,os.X_OK): return False
    try:
        with open(path,'rb') as f: magic=f.read(4)
        if magic[:4]==b'\x7fELF' or magic[:2]==b'#!': return True
    except: pass
    return False

def _build_lib_env():
    env=os.environ.copy()
    if LIBS_DIR.is_dir():
        existing=env.get("LD_LIBRARY_PATH","")
        extra_dirs=[str(LIBS_DIR)]
        for d in CHROME_DIR.glob("extracted/usr/lib/*/"):
            if d.is_dir(): extra_dirs.append(str(d))
        for d in CHROME_DIR.glob("extracted/usr/lib/"):
            if d.is_dir(): extra_dirs.append(str(d))
        new_path=":".join(extra_dirs)
        if existing: new_path+=":"+existing
        env["LD_LIBRARY_PATH"]=new_path
    return env

def _find_chromium():
    candidates=[
        CHROME_DIR/"chrome-linux64"/"chrome",
        CHROME_DIR/"extracted"/"usr"/"lib"/"chromium"/"chromium",
        CHROME_DIR/"extracted"/"usr"/"lib"/"chromium-browser"/"chromium-browser",
        CHROME_DIR/"extracted"/"usr"/"bin"/"chromium",
        CHROME_DIR/"extracted"/"usr"/"bin"/"chromium-browser",
        Path("/usr/lib/chromium/chromium"),
        Path("/usr/lib/chromium-browser/chromium-browser"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/opt/chromium/chrome"),
    ]
    for p in candidates:
        if _validate_chromium(str(p)): return str(p)
    for n in ["chromium","chromium-browser","google-chrome","google-chrome-stable"]:
        p=shutil.which(n)
        if p and _validate_chromium(p): return p
    for p in candidates:
        if _binary_exists(str(p)): return str(p)
    return None

def _extract_deb_python(deb_path, extract_dir):
    extract_dir.mkdir(parents=True, exist_ok=True)
    if shutil.which("dpkg-deb"):
        try:
            subprocess.run(["dpkg-deb","-x",str(deb_path),str(extract_dir)],
                         check=True,timeout=120,capture_output=True)
            return True
        except Exception as e:
            if DEBUG_CDP: print(f"[DBG] dpkg-deb failed: {e}")
    try:
        data_tar_bytes=_extract_data_tar_from_deb(deb_path)
        if data_tar_bytes is None:
            print(colored("[!] Could not find data.tar.* inside .deb","red"))
            return False
        _safe_tar_extract(data_tar_bytes, extract_dir)
        return True
    except Exception as e:
        print(colored(f"[!] Python extraction failed: {e}","red"))
        if DEBUG_CDP: import traceback; traceback.print_exc()
        return False

def _extract_data_tar_from_deb(deb_path):
    with open(deb_path,'rb') as f:
        magic=f.read(8)
        if magic!=b'!<arch>\n':
            raise ValueError("Not a valid .deb file")
        while True:
            header=f.read(60)
            if len(header)<60: break
            name=header[0:16].decode('ascii',errors='replace').strip().rstrip('/')
            size_str=header[48:58].decode('ascii',errors='replace').strip()
            try: size=int(size_str)
            except ValueError: break
            data=f.read(size)
            if size%2!=0: f.read(1)
            if name.startswith('data.tar'): return data
    return None

def _safe_tar_extract(tar_bytes, extract_dir):
    fileobj=io.BytesIO(tar_bytes)
    tf=None
    for mode in ['r:xz','r:gz','r:bz2','r:']:
        try:
            fileobj.seek(0)
            tf=tarfile.open(fileobj=fileobj,mode=mode)
            break
        except: continue
    if tf is None:
        try:
            import zstandard
            fileobj.seek(0)
            dctx=zstandard.ZstdDecompressor()
            decompressed=dctx.decompress(fileobj.read(),max_output_size=2*1024*1024*1024)
            tf=tarfile.open(fileobj=io.BytesIO(decompressed),mode='r:')
        except ImportError:
            raise RuntimeError("Cannot determine tar compression (no zstd module)")
        except Exception as e:
            raise RuntimeError(f"Tar extraction failed: {e}")
    try:
        members=tf.getmembers()
        dirs_to_create=set()
        for member in members:
            mp=Path(member.name.lstrip('./'))
            if '..' in mp.parts: continue
            if member.isdir(): dirs_to_create.add(extract_dir/mp)
            else: dirs_to_create.add((extract_dir/mp).parent)
        for d in sorted(dirs_to_create,key=lambda x:len(x.parts)):
            try: d.mkdir(parents=True,exist_ok=True)
            except OSError: pass
        for member in members:
            try:
                mp=Path(member.name.lstrip('./'))
                if '..' in mp.parts: continue
                target=extract_dir/mp
                if member.isdir():
                    target.mkdir(parents=True,exist_ok=True)
                elif member.issym():
                    if target.exists() or target.is_symlink(): target.unlink()
                    target.parent.mkdir(parents=True,exist_ok=True)
                    os.symlink(member.linkname,str(target))
                elif member.isfile():
                    target.parent.mkdir(parents=True,exist_ok=True)
                    src=tf.extractfile(member)
                    if src:
                        with open(target,'wb') as dst: shutil.copyfileobj(src,dst)
                        os.chmod(target,member.mode&0o7777 if member.mode&0o111 else 0o644)
            except (OSError,PermissionError) as e:
                if DEBUG_CDP: print(f"[DBG] Skip {member.name}: {e}")
                continue
    finally:
        tf.close()

def _download_chrome_for_testing():
    if ARCH!="amd64": return None
    print(colored("[*] Fetching Chrome for Testing (linux64) via official API...","yellow"))
    try:
        api_url="https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
        req=urllib.request.Request(api_url,headers={"User-Agent":"Mozilla/5.0"})
        resp=urllib.request.urlopen(req,timeout=20)
        data=json.loads(resp.read().decode())
        stable=data.get("channels",{}).get("Stable",{})
        version=stable.get("version","")
        if not version:
            ver_url="https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_STABLE"
            version=urllib.request.urlopen(ver_url,timeout=10).read().decode().strip()
        if not version:
            print(colored("[!] Could not determine CfT version","red")); return None
        print(colored(f"[*] Chrome for Testing version: {version}","cyan"))
        downloads=stable.get("downloads",{}).get("chrome",[])
        zip_url=None
        for d in downloads:
            if d.get("platform")=="linux64":
                zip_url=d.get("url"); break
        if not zip_url:
            zip_url=f"https://storage.googleapis.com/chrome-for-testing-public/{version}/linux64/chrome-linux64.zip"
        print(colored(f"[*] Downloading: {zip_url.split('/')[-1]}","cyan"))
        CHROME_DIR.mkdir(parents=True,exist_ok=True)
        zp=CHROME_DIR/"chrome-linux64.zip"
        req2=urllib.request.Request(zip_url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req2,timeout=300) as r, open(zp,"wb") as f:
            shutil.copyfileobj(r,f)
        if zp.stat().st_size<1024*1024:
            print(colored("[!] Download too small, likely failed","red"))
            zp.unlink(missing_ok=True); return None
        print(colored(f"[*] Downloaded ({zp.stat().st_size//(1024*1024)} MB), extracting...","cyan"))
        with zipfile.ZipFile(str(zp)) as z: z.extractall(str(CHROME_DIR))
        zp.unlink(missing_ok=True)
        cb=CHROME_DIR/"chrome-linux64"/"chrome"
        if _binary_exists(str(cb)):
            cb.chmod(0o755)
            print(colored(f"[✓] Chrome for Testing {version} ready (amd64)","green"))
            return str(cb)
        print(colored("[!] Extracted but binary not found","red"))
    except Exception as e:
        print(colored(f"[!] Chrome for Testing failed: {e}","red"))
    return None

DEBIAN_MIRRORS=[
    "http://http.us.debian.org/debian",
    "http://ftp.debian.org/debian",
    "http://ftp.uk.debian.org/debian",
    "http://ftp.de.debian.org/debian",
]

def _scrape_debian_pool(mirror_base, arch):
    pool_url=f"{mirror_base}/pool/main/c/chromium/"
    try:
        req=urllib.request.Request(pool_url,headers={"User-Agent":"Mozilla/5.0"})
        html_content=urllib.request.urlopen(req,timeout=20).read().decode(errors='replace')
        all_debs=re.findall(r'href="([^"]+\.deb)"',html_content)
        main_pattern=re.compile(rf'^chromium_([\d\.]+(?:-\d+)?(?:~\w+\d+)?)_{arch}\.deb$')
        common_pattern=re.compile(rf'^chromium-common_([\d\.]+(?:-\d+)?(?:~\w+\d+)?)_{arch}\.deb$')
        main_debs=[]; common_debs=[]
        for d in all_debs:
            m=main_pattern.match(d)
            if m: main_debs.append((d,m.group(1))); continue
            c=common_pattern.match(d)
            if c: common_debs.append((d,c.group(1)))
        def ver_key(item):
            v=item[1].split('-')[0].split('~')[0]
            return [int(x) for x in v.split('.') if x.isdigit()]
        main_debs.sort(key=ver_key)
        common_debs.sort(key=ver_key)
        latest_main=main_debs[-1][0] if main_debs else None
        latest_common=None
        if latest_main and common_debs:
            main_ver=main_debs[-1][1].split('-')[0].split('~')[0]
            for cd,cv in reversed(common_debs):
                if cv.split('-')[0].split('~')[0]==main_ver:
                    latest_common=cd; break
            if not latest_common: latest_common=common_debs[-1][0]
        elif common_debs:
            latest_common=common_debs[-1][0]
        return latest_main, latest_common, pool_url
    except Exception as e:
        if DEBUG_CDP: print(f"[DBG] Debian scrape failed for {mirror_base}: {e}")
        return None, None, pool_url

COMPANION_LIBS={
    "libjpeg62-turbo": ("pool/main/libj/libjpeg-turbo/", rf"libjpeg62-turbo_([\d\.]+(?:-\d+)?)_ARCH\.deb"),
}

def _download_companion_libs(arch):
    if not arch: return
    LIBS_DIR.mkdir(parents=True,exist_ok=True)
    existing_jpeg=list(LIBS_DIR.glob("libjpeg.so.62*"))
    if existing_jpeg:
        if DEBUG_CDP: print(f"[DBG] libjpeg.so.62 already in {LIBS_DIR}")
        return
    print(colored("[*] Downloading companion libs (libjpeg62-turbo) from Debian pool...","yellow"))
    for mirror in DEBIAN_MIRRORS:
        try:
            pool_url=f"{mirror}/pool/main/libj/libjpeg-turbo/"
            req=urllib.request.Request(pool_url,headers={"User-Agent":"Mozilla/5.0"})
            html_content=urllib.request.urlopen(req,timeout=20).read().decode(errors='replace')
            all_debs=re.findall(r'href="([^"]+\.deb)"',html_content)
            pattern=re.compile(rf'^libjpeg62-turbo_([\d\.]+(?:-\d+)?)_{arch}\.deb$')
            matches=[]
            for d in all_debs:
                m=pattern.match(d)
                if m: matches.append((d,m.group(1)))
            if not matches:
                if DEBUG_CDP: print(f"[DBG] No libjpeg62-turbo for {arch} on {mirror}")
                continue
            def ver_key(item):
                v=item[1].split('-')[0]
                return [int(x) for x in v.split('.') if x.isdigit()]
            matches.sort(key=ver_key)
            deb_name=matches[-1][0]
            deb_url=f"{pool_url}{deb_name}"
            deb_path=CHROME_DIR/"libjpeg62-turbo.deb"
            print(colored(f"[*] Downloading {deb_name}...","cyan"))
            req2=urllib.request.Request(deb_url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req2,timeout=60) as r, open(deb_path,"wb") as f:
                shutil.copyfileobj(r,f)
            if deb_path.stat().st_size<10*1024:
                print(colored("[!] libjpeg62-turbo download too small","red"))
                deb_path.unlink(missing_ok=True)
                continue
            tmp_extract=CHROME_DIR/"_libjpeg_tmp"
            if tmp_extract.exists(): shutil.rmtree(tmp_extract,ignore_errors=True)
            _extract_deb_python(str(deb_path), tmp_extract)
            deb_path.unlink(missing_ok=True)
            copied=0
            for so_file in tmp_extract.rglob("*.so*"):
                if so_file.is_file() or so_file.is_symlink():
                    dest=LIBS_DIR/so_file.name
                    try:
                        if dest.exists() or dest.is_symlink(): dest.unlink()
                        if so_file.is_symlink():
                            os.symlink(os.readlink(str(so_file)),str(dest))
                        else:
                            shutil.copy2(str(so_file),str(dest))
                            os.chmod(str(dest),0o755)
                        copied+=1
                    except Exception as e:
                        if DEBUG_CDP: print(f"[DBG] Copy {so_file.name}: {e}")
            shutil.rmtree(tmp_extract,ignore_errors=True)
            if copied>0:
                print(colored(f"[✓] Extracted {copied} lib files to {LIBS_DIR}","green"))
                return
            else:
                print(colored("[!] No .so files found in libjpeg62-turbo deb","red"))
        except Exception as e:
            if DEBUG_CDP: print(f"[DBG] Companion lib download failed ({mirror}): {e}")
            continue
    print(colored("[!] Could not download libjpeg62-turbo from any mirror","red"))

def _download_specific_debian_lib(pkg_name, arch):
    pool_map={
        "libjpeg62-turbo": "pool/main/libj/libjpeg-turbo/",
        "libopenjp2-7": "pool/main/o/openjpeg2/",
        "libwebp7": "pool/main/libw/libwebp/",
        "libwebpdemux2": "pool/main/libw/libwebp/",
        "libpng16-16": "pool/main/libp/libpng1.6/",
        "libharfbuzz0b": "pool/main/h/harfbuzz/",
        "libharfbuzz-subset0": "pool/main/h/harfbuzz/",
        "libfreetype6": "pool/main/f/freetype/",
        "libfontconfig1": "pool/main/f/fontconfig/",
        "libnss3": "pool/main/n/nss/",
        "libnspr4": "pool/main/n/nspr/",
        "libasound2": "pool/main/a/alsa-lib/",
        "libasound2t64": "pool/main/a/alsa-lib/",
        "libopus0": "pool/main/o/opus/",
        "libflac12": "pool/main/f/flac/",
        "libvpx9": "pool/main/libv/libvpx/",
        "libdav1d7": "pool/main/libd/dav1d/",
        "libdav1d6": "pool/main/libd/dav1d/",
        "libopenh264-7": "pool/main/o/openh264/",
        "libopenh264-8": "pool/main/o/openh264/",
        "libdouble-conversion3": "pool/main/d/double-conversion/",
        "libminizip1": "pool/main/z/zlib/",
        "libsnappy1v5": "pool/main/s/snappy/",
        "libevent-2.1-7": "pool/main/libe/libevent/",
        "libhyphen0": "pool/main/h/hyphen/",
        "libxslt1.1": "pool/main/libx/libxslt/",
        "libenchant-2-2": "pool/main/e/enchant/",
        "libsecret-1-0": "pool/main/libs/libsecret/",
        "libxml2": "pool/main/libx/libxml2/",
        "libvulkan1": "pool/main/v/vulkan-loader/",
        "libegl1": "pool/main/libg/libglvnd/",
        "libgles2": "pool/main/libg/libglvnd/",
        "libglapi-mesa": "pool/main/m/mesa/",
    }
    subdir=pool_map.get(pkg_name)
    if not subdir:
        if DEBUG_CDP: print(f"[DBG] No pool mapping for {pkg_name}")
        return False
    LIBS_DIR.mkdir(parents=True,exist_ok=True)
    for mirror in DEBIAN_MIRRORS:
        try:
            pool_url=f"{mirror}/{subdir}"
            req=urllib.request.Request(pool_url,headers={"User-Agent":"Mozilla/5.0"})
            html_content=urllib.request.urlopen(req,timeout=15).read().decode(errors='replace')
            all_debs=re.findall(r'href="([^"]+\.deb)"',html_content)
            pattern=re.compile(rf'^{re.escape(pkg_name)}_([\d\.]+(?:-\d+)?(?:~\w+\d+)?)_{arch}\.deb$')
            matches=[]
            for d in all_debs:
                m=pattern.match(d)
                if m: matches.append((d,m.group(1)))
            if not matches: continue
            def ver_key(item):
                v=item[1].split('-')[0].split('~')[0]
                return [int(x) for x in v.split('.') if x.isdigit()]
            matches.sort(key=ver_key)
            deb_name=matches[-1][0]
            deb_url=f"{pool_url}{deb_name}"
            deb_path=CHROME_DIR/f"{pkg_name}.deb"
            print(colored(f"[*] Downloading {deb_name}...","cyan"))
            req2=urllib.request.Request(deb_url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req2,timeout=60) as r, open(deb_path,"wb") as f:
                shutil.copyfileobj(r,f)
            if deb_path.stat().st_size<5*1024:
                deb_path.unlink(missing_ok=True); continue
            tmp_extract=CHROME_DIR/f"_{pkg_name}_tmp"
            if tmp_extract.exists(): shutil.rmtree(tmp_extract,ignore_errors=True)
            _extract_deb_python(str(deb_path), tmp_extract)
            deb_path.unlink(missing_ok=True)
            copied=0
            for so_file in tmp_extract.rglob("*.so*"):
                if so_file.is_file() or so_file.is_symlink():
                    dest=LIBS_DIR/so_file.name
                    try:
                        if dest.exists() or dest.is_symlink(): dest.unlink()
                        if so_file.is_symlink():
                            os.symlink(os.readlink(str(so_file)),str(dest))
                        else:
                            shutil.copy2(str(so_file),str(dest))
                            os.chmod(str(dest),0o755)
                        copied+=1
                    except: pass
            shutil.rmtree(tmp_extract,ignore_errors=True)
            if copied>0:
                print(colored(f"[✓] {pkg_name}: {copied} libs → {LIBS_DIR}","green"))
                return True
        except Exception as e:
            if DEBUG_CDP: print(f"[DBG] {pkg_name} from {mirror}: {e}")
            continue
    return False

def _download_chromium_debian_pool():
    if not ARCH:
        print(colored(f"[!] Unsupported architecture '{ARCH_LABEL}'","red")); return None
    deb_arch=ARCH
    print(colored(f"[*] Fetching Chromium from Debian pool ({deb_arch})...","yellow"))
    CHROME_DIR.mkdir(parents=True,exist_ok=True)
    for mirror in DEBIAN_MIRRORS:
        try:
            main_deb, common_deb, pool_url = _scrape_debian_pool(mirror, deb_arch)
            if not main_deb:
                if DEBUG_CDP: print(f"[DBG] No chromium .deb found on {mirror}")
                continue
            print(colored(f"[*] Found: {main_deb}","cyan"))
            if common_deb: print(colored(f"[*] Found: {common_deb}","cyan"))
            deb_files=[]
            if common_deb:
                common_url=f"{pool_url}{common_deb}"
                common_path=CHROME_DIR/"common.deb"
                print(colored(f"[*] Downloading {common_deb}...","cyan"))
                req=urllib.request.Request(common_url,headers={"User-Agent":"Mozilla/5.0"})
                with urllib.request.urlopen(req,timeout=180) as r, open(common_path,"wb") as f:
                    shutil.copyfileobj(r,f)
                if common_path.stat().st_size>1024*1024:
                    deb_files.append(str(common_path))
            main_url=f"{pool_url}{main_deb}"
            main_path=CHROME_DIR/"main.deb"
            print(colored(f"[*] Downloading {main_deb}...","cyan"))
            req=urllib.request.Request(main_url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=180) as r, open(main_path,"wb") as f:
                shutil.copyfileobj(r,f)
            if main_path.stat().st_size<1024*1024:
                print(colored("[!] Download too small","red"))
                main_path.unlink(missing_ok=True)
                if common_path.exists(): common_path.unlink(missing_ok=True)
                continue
            deb_files.append(str(main_path))
            print(colored("[*] Installing via dpkg + apt (resolves all dependencies)...","cyan"))
            try:
                subprocess.run(["sudo","apt-get","update","-qq"],timeout=90,capture_output=True)
                subprocess.run(["sudo","dpkg","-i"]+deb_files,timeout=120,capture_output=True,text=True)
                r=subprocess.run(["sudo","apt-get","install","-f","-y","-qq"],
                               timeout=300,capture_output=True,text=True)
                if r.returncode==0:
                    print(colored("[✓] Dependencies resolved via apt","green"))
                for p in ["/usr/lib/chromium/chromium","/usr/bin/chromium",
                          "/usr/lib/chromium-browser/chromium-browser","/usr/bin/chromium-browser"]:
                    if _binary_exists(p):
                        print(colored(f"[✓] Chromium installed system-wide: {p}","green"))
                        for df in deb_files:
                            try: Path(df).unlink(missing_ok=True)
                            except: pass
                        return p
            except Exception as e:
                if DEBUG_CDP: print(f"[DBG] dpkg install failed: {e}")
            print(colored("[*] dpkg method failed, falling back to manual extraction...","yellow"))
            extract_dir=CHROME_DIR/"extracted"
            if extract_dir.exists(): shutil.rmtree(extract_dir,ignore_errors=True)
            for df in deb_files:
                _extract_deb_python(df, extract_dir)
                try: Path(df).unlink(missing_ok=True)
                except: pass
            _download_companion_libs(deb_arch)
            binary_candidates=[
                extract_dir/"usr"/"lib"/"chromium"/"chromium",
                extract_dir/"usr"/"lib"/"chromium-browser"/"chromium-browser",
                extract_dir/"usr"/"bin"/"chromium",
                extract_dir/"usr"/"bin"/"chromium-browser",
            ]
            for binary in binary_candidates:
                if _binary_exists(str(binary)):
                    binary.chmod(0o755)
                    print(colored(f"[✓] Chromium binary found: {binary} ({deb_arch})","green"))
                    return str(binary)
            for f in extract_dir.rglob("*"):
                if f.is_file() and f.name in ("chromium","chromium-browser","chrome"):
                    if _binary_exists(str(f)):
                        f.chmod(0o755)
                        print(colored(f"[✓] Chromium binary found: {f}","green"))
                        return str(f)
            print(colored("[!] Extracted but no binary found.","red"))
            return None
        except Exception as e:
            print(colored(f"[!] Debian pool ({mirror}) failed: {e}","red"))
            continue
    return None

def _apt_install_chromium():
    if not ARCH: return None
    print(colored("[*] Attempting apt install of chromium...","yellow"))
    try:
        subprocess.run(["sudo","apt-get","update","-qq"],timeout=90,capture_output=True)
        for pkg in ["chromium","chromium-browser"]:
            r=subprocess.run(["sudo","apt-get","install","-y","-qq",pkg],
                           timeout=180,capture_output=True,text=True)
            if r.returncode==0:
                for p in [f"/usr/bin/{pkg}",f"/usr/lib/{pkg}/{pkg}",
                          "/usr/lib/chromium-browser/chromium-browser",
                          "/usr/lib/chromium/chromium"]:
                    if _binary_exists(p):
                        print(colored(f"[✓] Installed {pkg} via apt","green"))
                        return p
    except Exception as e:
        print(colored(f"[!] apt install failed: {e}","red"))
    return None

def _check_chromium_libs(chromium_path):
    missing=[]
    try:
        env=_build_lib_env()
        r=subprocess.run(["ldd",chromium_path],capture_output=True,timeout=10,text=True,env=env)
        for line in r.stdout.splitlines():
            if "not found" in line:
                missing.append(line.strip().split("=>")[0].strip())
    except: pass
    return missing

def _try_install_libs(missing):
    if not missing: return
    print(colored(f"[!] Missing libraries: {', '.join(missing[:15])}","red"))
    print(colored("[*] Attempting auto-install...","yellow"))
    pkg_map={
        "libX11.so.6":["libx11-6"],"libXext.so.6":["libxext6"],"libxcb.so.1":["libxcb1"],
        "libXcomposite.so.1":["libxcomposite1"],"libXdamage.so.1":["libxdamage1"],
        "libXfixes.so.3":["libxfixes3"],"libXrandr.so.2":["libxrandr2"],
        "libX11-xcb.so.1":["libx11-xcb1"],"libxcb-dri3.so.0":["libxcb-dri3-0"],
        "libxshmfence.so.1":["libxshmfence1"],"libxkbcommon.so.0":["libxkbcommon0"],
        "libXNVCtrl.so.0":["libxnvctrl0"],
        "libgtk-3.so.0":["libgtk-3-0"],"libgdk_pixbuf-2.0.so.0":["libgdk-pixbuf-2.0-0"],
        "libatk-1.0.so.0":["libatk1.0-0"],"libatk-bridge-2.0.so.0":["libatk-bridge2.0-0"],
        "libpango-1.0.so.0":["libpango-1.0-0"],"libcairo.so.2":["libcairo2"],
        "libnss3.so":["libnss3"],"libnspr4.so":["libnspr4"],
        "libasound.so.2":["libasound2t64","libasound2"],"libasound.so":["libasound2t64","libasound2"],
        "libopus.so.0":["libopus0"],"libflac.so.12":["libflac12"],
        "libvpx.so.9":["libvpx9"],"libopenh264.so.8":["libopenh264-8","libopenh264-7"],
        "libopenh264.so.7":["libopenh264-7"],"libdav1d.so.7":["libdav1d7"],
        "libdav1d.so.6":["libdav1d6"],
        "libwebp.so.7":["libwebp7"],"libwebpdemux.so.2":["libwebpdemux2"],
        "libjpeg.so.62":["libjpeg62-turbo","libjpeg62","libjpeg-turbo8"],
        "libopenjp2.so.7":["libopenjp2-7"],
        "libpng16.so.16":["libpng16-16"],
        "libharfbuzz-subset.so.0":["libharfbuzz-subset0"],
        "libharfbuzz.so.0":["libharfbuzz0b","libharfbuzz0"],
        "libfreetype.so.6":["libfreetype6"],"libfontconfig.so.1":["libfontconfig1"],
        "libcups.so.2":["libcups2"],"libdrm.so.2":["libdrm2"],
        "libgbm.so.1":["libgbm1"],"libdbus-1.so.3":["libdbus-1-3"],
        "libexpat.so.1":["libexpat1"],"libz.so.1":["zlib1g"],
        "libdouble-conversion.so.3":["libdouble-conversion3"],
        "libminizip.so.1":["libminizip1"],
        "libsnappy.so.1":["libsnappy1v5"],
        "libevent-2.1.so.7":["libevent-2.1-7"],
        "libhyphen.so.0":["libhyphen0"],"libxslt.so.1":["libxslt1.1"],
        "libenchant-2.so.2":["libenchant-2-2"],"libsecret-1.so.0":["libsecret-1-0"],
        "libxml2.so.2":["libxml2"],
        "libvulkan.so.1":["libvulkan1"],"libEGL.so.1":["libegl1"],
        "libGLESv2.so.2":["libgles2"],"libglapi.so.0":["libglapi-mesa"],
    }
    candidates=[]
    for lib in missing:
        matched=False
        for k,v in pkg_map.items():
            if k in lib or lib.startswith(k.split(".so")[0]):
                candidates.append(v); matched=True; break
        if not matched:
            base=re.sub(r'\.so[\d.]*$','',lib)
            ver_match=re.search(r'\.so\.(\d+)',lib)
            if ver_match:
                candidates.append([f"{base}{ver_match.group(1)}",f"{base}-0"])
            else:
                candidates.append([f"{base}0"])
    subprocess.run(["sudo","apt-get","update","-qq"],timeout=60,capture_output=True)
    installed_count=0
    for cand_list in candidates:
        for pkg in cand_list:
            r=subprocess.run(["sudo","apt-get","install","-y","-qq",pkg],
                           timeout=60,capture_output=True,text=True)
            if r.returncode==0:
                installed_count+=1
                break
            elif DEBUG_CDP: print(f"[DBG] Failed: {pkg}")
    print(colored(f"[✓] Installed libs for {installed_count}/{len(candidates)} missing libraries",
                 "green" if installed_count>0 else "red"))

def _install_missing_from_stderr(stderr_text):
    m=re.search(r'error while loading shared libraries:\s+(\S+?):',stderr_text)
    if m:
        lib_name=m.group(1)
        print(colored(f"[*] Runtime missing lib: {lib_name} — attempting install...","yellow"))
        _try_install_libs([lib_name])
        return True
    m2=re.search(r'symbol lookup error:.*?undefined symbol:\s*(\S+?)(?:,\s*version\s+(\S+))?\s*$',stderr_text,re.MULTILINE)
    if m2:
        sym_name=m2.group(1)
        sym_ver=m2.group(2) or ""
        print(colored(f"[*] Symbol lookup error: {sym_name} (version {sym_ver})","yellow"))
        print(colored("[*] This is a library VERSION MISMATCH — downloading correct Debian libs...","cyan"))
        return _fix_symbol_error(sym_name, sym_ver, stderr_text)
    return False

def _fix_symbol_error(sym_name, sym_ver, stderr_text):
    symbol_to_pkg={
        "jpeg_crop_scanline": "libjpeg62-turbo",
        "jpeg_read_header": "libjpeg62-turbo",
        "jpeg_create_decompress": "libjpeg62-turbo",
        "jpeg_start_decompress": "libjpeg62-turbo",
        "jpeg_finish_decompress": "libjpeg62-turbo",
        "jpeg_destroy_decompress": "libjpeg62-turbo",
        "jpeg_std_error": "libjpeg62-turbo",
        "jpeg_CreateCompress": "libjpeg62-turbo",
        "jpeg_CreateDecompress": "libjpeg62-turbo",
        "opj_create_decompress": "libopenjp2-7",
        "opj_read_header": "libopenjp2-7",
        "WebPDecode": "libwebp7",
        "WebPGetInfo": "libwebp7",
        "hb_buffer_create": "libharfbuzz0b",
        "hb_font_create": "libharfbuzz0b",
        "FT_Init_FreeType": "libfreetype6",
        "FT_New_Face": "libfreetype6",
        "FcInit": "libfontconfig1",
        "FcConfigCreate": "libfontconfig1",
        "NSS_Init": "libnss3",
        "PK11_CreateGenericObject": "libnss3",
        "png_create_read_struct": "libpng16-16",
        "xmlParseDoc": "libxml2",
        "xsltParseStylesheetDoc": "libxslt1.1",
        "secret_password_lookup_sync": "libsecret-1-0",
        "enchant_broker_init": "libenchant-2-2",
        "_ZN17double_conversion12DoubleToString": "libdouble-conversion3",
        "unzOpen64": "libminizip1",
        "_ZN6snappy11RawCompress": "libsnappy1v5",
        "event_base_new": "libevent-2.1-7",
        "hnj_hyphen_load": "libhyphen0",
        "opus_decoder_create": "libopus0",
        "FLAC__stream_decoder_new": "libflac12",
        "vpx_codec_dec_init_ver": "libvpx9",
        "dav1d_open": "libdav1d7",
        "WelsCreateDecoder": "libopenh264-7",
        "vkCreateInstance": "libvulkan1",
        "eglGetDisplay": "libegl1",
        "glGetString": "libgles2",
    }
    ver_to_pkg={
        "LIBJPEG_6.2": "libjpeg62-turbo",
        "LIBJPEG_62": "libjpeg62-turbo",
        "OPENJPEG_2.0": "libopenjp2-7",
        "HARFBUZZ_0.9.18": "libharfbuzz0b",
        "FREETYPE_2.0": "libfreetype6",
        "FONTCONFIG_2.0": "libfontconfig1",
        "NSS_3.2": "libnss3",
        "PNG16_0": "libpng16-16",
        "LIBXML2_2.0": "libxml2",
        "LIBXSLT_1.0": "libxslt1.1",
    }
    pkg_to_try=None
    for known_sym, pkg in symbol_to_pkg.items():
        if known_sym in sym_name:
            pkg_to_try=pkg; break
    if not pkg_to_try and sym_ver:
        for known_ver, pkg in ver_to_pkg.items():
            if known_ver in sym_ver:
                pkg_to_try=pkg; break
    if not pkg_to_try and sym_ver:
        base=sym_ver.split("_")[0].lower()
        if "jpeg" in base: pkg_to_try="libjpeg62-turbo"
        elif "png" in base: pkg_to_try="libpng16-16"
        elif "webp" in base: pkg_to_try="libwebp7"
    if not pkg_to_try:
        print(colored(f"[!] Unknown symbol {sym_name} — cannot auto-fix","red"))
        print(colored("[*] Try manually: sudo apt-get install -y libjpeg62-turbo","yellow"))
        return False
    print(colored(f"[*] Symbol {sym_name} → need Debian package: {pkg_to_try}","cyan"))
    if ARCH:
        r=subprocess.run(["sudo","apt-get","install","-y","-qq",pkg_to_try],
                        timeout=60,capture_output=True,text=True)
        if r.returncode==0:
            print(colored(f"[✓] Installed {pkg_to_try} via apt","green"))
            return True
        if _download_specific_debian_lib(pkg_to_try, ARCH):
            return True
    return False

def _install_fonts():
    font_dirs=[Path("/usr/share/fonts"),Path("/usr/local/share/fonts")]
    has_fonts=False
    for d in font_dirs:
        if d.is_dir():
            try:
                if any(d.rglob("*.ttf")) or any(d.rglob("*.otf")):
                    has_fonts=True; break
            except: pass
    if has_fonts:
        if DEBUG_CDP: print("[DBG] System fonts found, skipping font install")
        return
    print(colored("[*] No system fonts detected — installing for CSS rendering...","yellow"))
    try:
        subprocess.run(["sudo","apt-get","update","-qq"],timeout=60,capture_output=True)
        r=subprocess.run(["sudo","apt-get","install","-y","-qq",
                         "fonts-liberation","fonts-liberation2","fontconfig",
                         "fonts-dejavu-core","fonts-noto-color-emoji"],
                        timeout=120,capture_output=True,text=True)
        if r.returncode==0:
            print(colored("[✓] Fonts installed (Liberation + DejaVu + Noto Emoji)","green"))
            try: subprocess.run(["fc-cache","-f","-s"],timeout=30,capture_output=True)
            except: pass
        else:
            if DEBUG_CDP: print(colored(f"[!] Font install stderr: {r.stderr[:300]}","yellow"))
    except Exception as e:
        print(colored(f"[!] Font install error: {e}","yellow"))

# ============================================================
# BOOT SEQUENCE
# ============================================================
CHROME_BIN=_find_chromium()
if not CHROME_BIN and ARCH=="amd64":
    CHROME_BIN=_download_chrome_for_testing()
if not CHROME_BIN:
    CHROME_BIN=_download_chromium_debian_pool()
if not CHROME_BIN:
    CHROME_BIN=_apt_install_chromium()

if CHROME_BIN:
    _install_fonts()
    if "extracted" in str(CHROME_BIN) or ".chromium" in str(CHROME_BIN):
        _download_companion_libs(ARCH)
    _missing=_check_chromium_libs(CHROME_BIN)
    if _missing:
        _try_install_libs(_missing)
    if not _validate_chromium(CHROME_BIN):
        print(colored(f"[!] Binary exists but --version failed: {CHROME_BIN}","yellow"))
        _missing2=_check_chromium_libs(CHROME_BIN)
        if _missing2:
            print(colored(f"[!] Still missing: {', '.join(_missing2)}","red"))
            _try_install_libs(_missing2)
        print(colored("[*] Keeping binary — Chromium may work after installing libs","yellow"))

def _kill_existing_chromium():
    try:
        subprocess.run(["pkill","-9","-f",f"chrom.*remote-debugging-port={CDP_PORT}"],capture_output=True,timeout=5)
        time.sleep(0.5)
    except: pass

def _chromium_args():
    ud=tempfile.mkdtemp(prefix="qs_")
    return [f"--remote-debugging-port={CDP_PORT}",f"--user-data-dir={ud}",
       "--headless=new","--no-sandbox","--disable-setuid-sandbox",
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
       # ── SITE FIX: Expanded disabled features ──
       # IsolateOrigins,SitePerProcess: disables site isolation that blocks
       #   cross-origin resources on GitHub (githubassets.com, avatars, etc.) [[148]][[149]]
       # AcceptCHFrame: prevents Client Hints negotiation stalls [[189]][[190]]
       # MediaRouter: prevents background networking that wastes resources [[192]]
       # AutoupgradeMixedContent: prevents http→https auto-upgrade that breaks some sites [[171]]
       # Note: SitePerProcess (capital S) is the correct name since Chrome 96 [[149]][[90]]
       "--disable-features=TranslateUI,IsolateOrigins,SitePerProcess,AcceptCHFrame,MediaRouter,AutoupgradeMixedContent",
       "--disable-component-update","--disable-domain-reliability","--no-pings",
       "--disable-client-side-phishing-detection","--disable-hang-monitor",
       "--disable-popup-blocking","--disable-prompt-on-repost",
       "--metrics-recording-only",
       "--enable-features=NetworkService,NetworkServiceInProcess,UseSkiaRenderer",
       f"--num-raster-threads={max(1,min(CPU_CORES//2,4))}",
       "--window-size=1280,720","--force-color-profile=srgb",
       "--disable-gpu",
       "--disable-gpu-compositing",
       "--use-gl=angle","--use-angle=swiftshader","--no-zygote",
       "--disable-accelerated-video-decode",
       "--disable-accelerated-video-encode",
       "--disable-background-crash-reporting",
       "--run-all-compositor-stages-before-draw",
       "--disable-new-content-rendering-timeout",
       "--disable-background-throttling",
       "--force-device-scale-factor=1",
       # ── SITE FIX: Disable site isolation trials for cross-origin resource loading ──
       # Required for GitHub which loads from github.githubassets.com,
       # avatars.githubusercontent.com, etc. [[148]][[151]]
       "--disable-site-isolation-trials",
    ]

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

VK_CODES={"ArrowLeft":37,"ArrowUp":38,"ArrowRight":39,"ArrowDown":40,"Home":36,"End":35,"PageUp":33,"PageDown":34,"Insert":45,"Delete":46,"F1":112,"F2":113,"F3":114,"F4":115,"F5":116,"F6":117,"F7":118,"F8":119,"F9":120,"F10":121,"F11":122,"F12":123}

class CDPWorker(threading.Thread):
    ACTIVE_FPS=min(30,max(20,CPU_CORES*5)); IDLE_FPS=12; ACTIVE_SECS=2.0
    QUALITY=72 if HAS_GPU else 65
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
        self._page_loaded=threading.Event(); self._navigating=False; self._chrome_stderr=""
        self._pending_requests=0; self._total_requests=0; self._finished_requests=0
        self._net_lock=threading.Lock(); self._load_phase="idle"
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
            if method in ("Page.loadEventFired","Page.frameStoppedLoading"): self._page_loaded.set()
            elif method=="Page.lifecycleEvent":
                if data.get("params",{}).get("name") in ("load","networkAlmostIdle","networkIdle"): self._page_loaded.set()
            elif method=="Network.requestWillBeSent":
                with self._net_lock: self._pending_requests+=1; self._total_requests+=1
            elif method in ("Network.loadingFinished","Network.loadingFailed"):
                with self._net_lock: self._pending_requests=max(0,self._pending_requests-1); self._finished_requests+=1
    def _on_error(self,ws,error):
        if DEBUG_CDP: print(f"[CDP] WS error: {error}")
    def _on_close(self,ws,code,msg): self._ws_connected=False
    def _on_open(self,ws): self._ws_connected=True
    def _start_chrome(self):
        if not CHROME_BIN: return False
        _kill_existing_chromium()
        args=[CHROME_BIN]+_chromium_args()
        if DEBUG_CDP: print(f"[DBG] Launch: {' '.join(args[:8])}...")
        max_attempts=3
        for attempt in range(max_attempts):
            try:
                env=_build_lib_env()
                if DEBUG_CDP and "LD_LIBRARY_PATH" in env:
                    print(f"[DBG] LD_LIBRARY_PATH={env['LD_LIBRARY_PATH'][:200]}")
                self._chrome_proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    preexec_fn=os.setsid if os.name!='nt' else None,
                    env=env)
                launched_ok=False
                for _ in range(60):
                    time.sleep(0.25)
                    if self._chrome_proc.poll() is not None:
                        err_out=""
                        try: _,err_bytes=self._chrome_proc.communicate(timeout=3); err_out=err_bytes.decode(errors='replace')[:2000]
                        except: pass
                        self._chrome_stderr=err_out
                        rc=self._chrome_proc.returncode
                        if rc==127 and attempt<max_attempts-1:
                            print(colored(f"[!] Exit 127 — runtime library error, attempting fix (attempt {attempt+1})...","yellow"))
                            if err_out.strip():
                                print(colored(f"    STDERR: {err_out.strip()[:500]}","red"))
                            if _install_missing_from_stderr(err_out):
                                print(colored("[*] Retrying Chromium launch...","cyan"))
                                launched_ok=True
                                break
                            print(colored("[!] Could not auto-fix. Giving up.","red"))
                            return False
                        if "symbol lookup error" in err_out and attempt<max_attempts-1:
                            print(colored(f"[!] Symbol lookup error detected, attempting fix (attempt {attempt+1})...","yellow"))
                            print(colored(f"    STDERR: {err_out.strip()[:500]}","red"))
                            if _install_missing_from_stderr(err_out):
                                print(colored("[*] Retrying Chromium launch...","cyan"))
                                launched_ok=True
                                break
                            return False
                        print(colored(f"[!] Chromium exited (code {rc})","red"))
                        if err_out.strip(): print(colored(f"    STDERR: {err_out.strip()[:500]}","red"))
                        return False
                    try:
                        resp=urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version",timeout=2)
                        info=json.loads(resp.read().decode())
                        self._chrome_ver=info.get("Browser","Chromium")
                        self._ws_url=info.get("webSocketDebuggerUrl","")
                        if self._ws_url: return True
                    except: continue
                else:
                    return False
                if launched_ok:
                    continue
                return False
            except Exception as e:
                print(colored(f"[!] Launch failed: {e}","red")); return False
        return False
    def _connect_cdp(self):
        if not WS_AVAILABLE: return False
        try:
            self._ws=websocket.WebSocketApp(self._ws_url,on_open=self._on_open,on_message=self._on_message,
                on_error=self._on_error,on_close=self._on_close)
            threading.Thread(target=self._ws.run_forever,daemon=True,
                kwargs={"ping_interval":20,"ping_timeout":10,"skip_utf8_validation":True,"suppress_origin":True}).start()
            for _ in range(50):
                if self._ws_connected: return True
                time.sleep(0.1)
            return False
        except: return False
    def _create_target(self):
        resp=self._send_cdp("Target.createTarget",{"url":"about:blank"},timeout=10)
        if not resp or "result" not in resp: return False
        self._target_id=resp["result"].get("targetId")
        if not self._target_id: return False
        resp2=self._send_cdp("Target.attachToTarget",{"targetId":self._target_id,"flatten":True},timeout=10)
        if not resp2 or "result" not in resp2: return False
        self._session_id=resp2["result"].get("sessionId")
        return bool(self._session_id)
    # ── SITE FIX: Added CSP bypass + service worker bypass + cache control ──
    def _enable_domains(self):
        for m in ["Page.enable","Network.enable","Runtime.enable"]: self._send_session(m,{},timeout=5)
        self._send_session("Page.setLifecycleEventsEnabled",{"enabled":True},timeout=5)
        self._send_session("Network.setCacheDisabled",{"cacheDisabled":True},timeout=5)
        self._send_session("Emulation.setDefaultBackgroundColorOverride",{
            "color":{"r":255,"g":255,"b":255,"a":1}
        },timeout=5)
        # ── SITE FIX: Bypass page Content Security Policy ──
        # GitHub enforces strict CSP blocking unsafe-inline scripts/styles [[135]]
        # DuckDuckGo also has strict CSP [[112]]
        # Without this, our stealth script injection gets blocked by the page's CSP,
        # which cascades into breaking the page's own JS execution [[126]][[128]]
        # Must be called BEFORE navigating [[125]][[126]]
        self._send_session("Page.setBypassCSP",{"enabled":True},timeout=5)
        # ── SITE FIX: Bypass service workers ──
        # GitHub and DuckDuckGo use service workers that intercept network requests
        # in headless mode, serving stale/cached content or blocking requests [[139]][[141]]
        # This forces all requests to go directly to the network [[139]][[140]]
        self._send_session("Network.setBypassServiceWorker",{"bypass":True},timeout=5)
    # ── SITE FIX: Made stealth script CSP-resilient with try/catch ──
    def _inject_stealth(self,vw,vh):
        # Wrapped entire script in try/catch so CSP blocks on individual
        # Object.defineProperty calls don't crash the whole evaluation
        s=f"""(()=>{{try{{Object.defineProperty(navigator,'webdriver',{{get:()=>undefined,configurable:true}});}}catch(e){{}}
        try{{if(!window.chrome)window.chrome={{runtime:{{connect:()=>{{}},sendMessage:()=>{{}},onMessage:{{addListener:()=>{{}}}},id:undefined}},loadTimes:()=>({{}}),csi:()=>({{}}),app:{{isInstalled:false}}}};}}catch(e){{}}
        try{{const fp=[{{name:'Chromium PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format',length:1}},{{name:'Chromium PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:'',length:1}},{{name:'Native Client',filename:'internal-nacl-plugin',description:'',length:2}}];
        fp.__proto__=PluginArray.prototype;Object.defineProperty(navigator,'plugins',{{get:()=>fp,configurable:true}});}}catch(e){{}}
        try{{if(window.outerWidth===0)window.outerWidth={vw};if(window.outerHeight===0)window.outerHeight={vh+85};}}catch(e){{}}
        try{{Object.defineProperty(navigator,'hardwareConcurrency',{{get:()=>{CPU_CORES},configurable:true}});}}catch(e){{}}
        try{{Object.defineProperty(navigator,'deviceMemory',{{get:()=>8,configurable:true}});}}catch(e){{}}
        try{{Object.defineProperty(navigator,'languages',{{get:()=>['en-US','en'],configurable:true}});}}catch(e){{}}
        try{{Object.defineProperty(navigator,'userAgent',{{get:()=>navigator.userAgent.replace(/Headless/g,''),configurable:true}});}}catch(e){{}}
        try{{Object.defineProperty(navigator,'appVersion',{{get:()=>navigator.appVersion.replace(/Headless/g,''),configurable:true}});}}catch(e){{}}
        try{{if(navigator.permissions){{const o=navigator.permissions.query.bind(navigator.permissions);navigator.permissions.query=(p)=>p.name==='notifications'?Promise.resolve({{state:Notification.permission}}):o(p);}}}}catch(e){{}}
        try{{const gp=WebGLRenderingContext.prototype.getParameter;WebGLRenderingContext.prototype.getParameter=function(p){{if(p===37445)return'Intel Inc.';if(p===37446)return'Intel Iris OpenGL Engine';return gp.call(this,p);}};}}catch(e){{}}}})();"""
        self._send_session("Page.addScriptToEvaluateOnNewDocument",{"source":s},timeout=5)
    def _wait_for_load(self,timeout=20):
        self._load_phase="loading"; self._page_loaded.clear()
        with self._net_lock: self._pending_requests=0; self._total_requests=0; self._finished_requests=0
        self._page_loaded.wait(timeout=min(timeout,10))
        deadline=time.monotonic()+min(timeout,10); stable_count=0
        while time.monotonic()<deadline:
            with self._net_lock: pending=self._pending_requests
            if pending==0:
                stable_count+=1
                if stable_count>=3: break
            else: stable_count=0
            time.sleep(0.4)
        self._load_phase="rendering"
        try:
            self._send_session("Runtime.evaluate",{
                "expression":"document.fonts.ready.then(()=>'fonts_ok').catch(()=>'fonts_err')",
                "awaitPromise":True,"returnByValue":True,"timeout":5000
            },timeout=8)
        except: pass
        try:
            self._send_session("Runtime.evaluate",{
                "expression":"new Promise(r=>{requestAnimationFrame(()=>{requestAnimationFrame(()=>{r('painted');});});})",
                "awaitPromise":True,"returnByValue":True,"timeout":3000
            },timeout=5)
        except: pass
        try:
            self._send_session("Runtime.evaluate",{
                "expression":"""(function(){
                    var links=document.querySelectorAll('link[rel="stylesheet"]');
                    var allLoaded=true;
                    for(var i=0;i<links.length;i++){
                        try{if(links[i].sheet===null){allLoaded=false;break;}}
                        catch(e){allLoaded=false;break;}
                    }
                    return JSON.stringify({css:allLoaded,readyState:document.readyState});
                })()""",
                "returnByValue":True
            },timeout=5)
        except: pass
        time.sleep(2.5)
        try:
            resp=self._send_session("Runtime.evaluate",{"expression":"document.readyState","returnByValue":True},timeout=3)
            if resp and "result" in resp:
                if resp["result"].get("result",{}).get("value","")!="complete": time.sleep(1.5)
        except: pass
        self._load_phase="complete"; return True
    def get_load_status(self):
        with self._net_lock: total=self._total_requests; finished=self._finished_requests; pending=self._pending_requests
        pct=0
        if total>0: pct=min(100,int((finished/total)*100))
        if self._load_phase=="complete": pct=100
        elif self._load_phase=="idle": pct=0
        return {"phase":self._load_phase,"pending":pending,"total":total,"finished":finished,"percent":pct,"navigating":self._navigating}
    def _scroll_page(self, dy):
        try:
            js=f"window.scrollBy({{top:{dy},left:0,behavior:'instant'}});document.documentElement.scrollTop+=0;window.pageYOffset"
            resp=self._send_session("Runtime.evaluate",{"expression":js,"returnByValue":True},timeout=5)
            if resp and "result" in resp:
                if DEBUG_CDP: print(f"[DBG] JS scrollBy({dy}) → offset={resp['result'].get('result',{}).get('value','?')}")
                self._send_session("Runtime.evaluate",{
                    "expression":f"""(function(){{
                        var el=document.activeElement;
                        if(el&&el!==document.body&&el!==document.documentElement&&el.scrollHeight>el.clientHeight){{
                            el.scrollBy(0,{dy});
                        }}
                        var els=document.querySelectorAll('*');
                        for(var i=0;i<els.length;i++){{
                            var e=els[i];
                            if(e.scrollHeight>e.clientHeight+10&&e.scrollTop>=0){{
                                var st=getComputedStyle(e).overflowY;
                                if(st==='auto'||st==='scroll'){{e.scrollBy(0,{dy});break;}}
                            }}
                        }}
                    }})()""",
                    "returnByValue":True},timeout=5)
                return
        except Exception as e:
            if DEBUG_CDP: print(f"[DBG] JS scroll failed: {e}")
        try:
            cx,cy=self.vp_width//2,self.vp_height//2
            self._send_session("Input.synthesizeScrollGesture",{
                "x":cx,"y":cy,"yDistance":-dy,"speed":800,"preventFling":True
            },timeout=5)
            return
        except Exception as e:
            if DEBUG_CDP: print(f"[DBG] synthesizeScrollGesture failed: {e}")
        try:
            cx,cy=self.vp_width//2,self.vp_height//2
            self._send_session("Input.dispatchMouseEvent",{"type":"mouseMoved","x":cx,"y":cy},timeout=3)
            time.sleep(0.05)
            self._send_session("Input.dispatchMouseEvent",{"type":"mouseWheel","x":cx,"y":cy,"deltaX":0,"deltaY":dy},timeout=3)
        except Exception as e:
            if DEBUG_CDP: print(f"[DBG] mouseWheel fallback failed: {e}")

    def run(self):
        if not CHROME_BIN: print(colored("[!] No Chromium binary.","red")); return
        if not WS_AVAILABLE: print(colored("[!] pip install websocket-client","red")); return
        if not self._start_chrome(): print(colored("[!] FATAL: Chromium failed to start.","red")); return
        print(colored(f"[✓] {self._chrome_ver} (PID {self._chrome_proc.pid})"+(" [GPU]" if HAS_GPU else " [CPU]"),"green"))
        if not self._connect_cdp(): print(colored("[!] FATAL: CDP WebSocket failed.","red")); return
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
    def _take_screenshot(self):
        if not self._session_id: return
        for attempt in range(3):
            try:
                resp=self._send_session("Page.captureScreenshot",{
                    "format":"jpeg","quality":self.QUALITY,
                    "captureBeyondViewport":False,"fromSurface":False,"optimizeForSpeed":False
                },timeout=8)
                if resp and "result" in resp:
                    b64=resp["result"].get("data","")
                    if b64 and len(b64)>100:
                        frame=base64.b64decode(b64)
                        if len(frame)>100:
                            global LATEST_FRAME
                            with FRAME_LOCK: LATEST_FRAME=frame
                            FRAME_EVENT.set(); self._frame_n+=1; return
            except: pass
            if attempt<2: time.sleep(0.15)
    def _refresh_state(self):
        try:
            resp=self._send_session("Runtime.evaluate",{"expression":"JSON.stringify({u:location.href,t:document.title})","returnByValue":True},timeout=5)
            if resp and "result" in resp:
                val=resp["result"].get("result",{}).get("value","")
                if val:
                    info=json.loads(val)
                    with self._state_lock: self._state.update({"url":info.get("u",""),"title":info.get("t",""),"ua":self.current_ua,"mobile":self.is_mobile,"vp_width":self.vp_width,"vp_height":self.vp_height})
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
            self._send_session("Network.setExtraHTTPHeaders",{"headers":{"Accept-Language":"en-US,en;q=0.9","Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8","Upgrade-Insecure-Requests":"1"}},timeout=5)
            # ── SITE FIX: Re-assert CSP bypass + SW bypass before EVERY navigation ──
            # CSP bypassing happens at CSP initialization time, so it must be
            # set before each navigation to ensure it takes effect [[126]][[128]]
            # Service worker bypass also needs re-assertion per navigation [[141]]
            self._send_session("Page.setBypassCSP",{"enabled":True},timeout=5)
            self._send_session("Network.setBypassServiceWorker",{"bypass":True},timeout=5)
            self._inject_stealth(vp_w,vp_h)
            if data.get("cookies"):
                try:
                    for c in json.loads(data["cookies"]): self._send_session("Network.setCookie",c,timeout=3)
                except: pass
            self._page_loaded.clear(); self._navigating=True; self._load_phase="navigating"
            nav_resp=self._send_session("Page.navigate",{"url":url},timeout=20)
            if nav_resp and "error" in nav_resp:
                self._navigating=False; self._load_phase="idle"; return
            self._wait_for_load(timeout=20); self._navigating=False
            self._take_screenshot()
            threading.Timer(2.0,self._take_screenshot).start()
            threading.Timer(5.0,self._take_screenshot).start()
        elif action=="reload":
            self._page_loaded.clear(); self._load_phase="navigating"
            # ── SITE FIX: Re-assert bypasses before reload too ──
            self._send_session("Page.setBypassCSP",{"enabled":True},timeout=5)
            self._send_session("Network.setBypassServiceWorker",{"bypass":True},timeout=5)
            self._send_session("Page.reload",{},timeout=10)
            self._wait_for_load(timeout=15); self._take_screenshot()
            threading.Timer(2.0,self._take_screenshot).start()
            threading.Timer(5.0,self._take_screenshot).start()
        elif action in ("go_back","go_forward"):
            self._page_loaded.clear(); self._load_phase="navigating"
            # ── SITE FIX: Re-assert bypasses before history nav ──
            self._send_session("Page.setBypassCSP",{"enabled":True},timeout=5)
            self._send_session("Network.setBypassServiceWorker",{"bypass":True},timeout=5)
            resp=self._send_session("Page.getNavigationHistory",{},timeout=5)
            if resp and "result" in resp:
                idx=resp["result"].get("currentIndex",0); entries=resp["result"].get("entries",[])
                if action=="go_back" and idx>0: self._send_session("Page.navigateToHistoryEntry",{"entryId":entries[idx-1]["id"]},timeout=10)
                elif action=="go_forward" and idx<len(entries)-1: self._send_session("Page.navigateToHistoryEntry",{"entryId":entries[idx+1]["id"]},timeout=10)
                self._wait_for_load(timeout=15); self._take_screenshot()
                threading.Timer(2.0,self._take_screenshot).start()
                threading.Timer(5.0,self._take_screenshot).start()
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
                if kv in VK_CODES:
                    vk=VK_CODES[kv]
                    for tp in ("rawKeyDown","keyUp"): self._send_session("Input.dispatchKeyEvent",{"type":tp,"key":kv,"code":kv,"windowsVirtualKeyCode":vk,"nativeVirtualKeyCode":vk,"modifiers":mods},timeout=3)
                elif kv=="CapsLock":
                    for tp in ("rawKeyDown","keyUp"): self._send_session("Input.dispatchKeyEvent",{"type":tp,"key":"CapsLock","code":"CapsLock","windowsVirtualKeyCode":20,"nativeVirtualKeyCode":20,"modifiers":mods},timeout=3)
                else:
                    txt=sp.get(kv,kv); vk=ord(txt[0]) if txt else 0
                    for tp in ("keyDown","keyUp"): self._send_session("Input.dispatchKeyEvent",{"type":tp,"key":kv,"text":txt,"unmodifiedText":txt,"windowsVirtualKeyCode":vk,"nativeVirtualKeyCode":vk,"modifiers":mods},timeout=3)
            elif t in ("scroll_up","scroll_down"):
                px=int(data.get("px",400)); dy=px if t=="scroll_down" else -px
                self._scroll_page(dy)
            elif t=="touch_scroll":
                dy=float(data.get("dy",0)); cx,cy=self.vp_width//2,self.vp_height//2
                if self.is_mobile:
                    steps=max(4,int(abs(dy))//25); sdy=dy/steps
                    self._send_session("Input.dispatchTouchEvent",{"type":"touchStart","touchPoints":[{"x":cx,"y":cy}]},timeout=3)
                    for i in range(1,steps+1): self._send_session("Input.dispatchTouchEvent",{"type":"touchMove","touchPoints":[{"x":cx,"y":int(cy+sdy*i)}]},timeout=2)
                    self._send_session("Input.dispatchTouchEvent",{"type":"touchEnd","touchPoints":[]},timeout=3)
                else:
                    self._scroll_page(int(dy))
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

APP_HTML=r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1"><title>QuantumSurf</title><link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@500;700&display=swap" rel="stylesheet"><style>:root{--bg:#080c10;--surf:#0d1117;--surf2:#111820;--bdr:#1e2d20;--acc:#00ff90;--acc2:#00cfff;--warn:#ffcc00;--danger:#ff4444;--text:#c8ffd4;--muted:#4a7a58;--glow:0 0 16px #00ff9030}*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--text);font-family:'Share Tech Mono',monospace;height:100dvh;display:flex;flex-direction:column;overflow:hidden}#tb{background:var(--surf);border-bottom:1px solid var(--bdr);height:48px;display:flex;align-items:center;gap:6px;padding:0 10px;flex-shrink:0;z-index:10}.brand{font-family:'Orbitron',sans-serif;font-size:13px;font-weight:700;color:var(--acc);letter-spacing:2px;white-space:nowrap;padding-right:6px;text-shadow:0 0 12px var(--acc);display:none}@media(min-width:640px){.brand{display:block}}.nb{background:none;border:1px solid var(--bdr);color:var(--muted);width:32px;height:32px;border-radius:4px;cursor:pointer;font-size:14px;display:flex;align-items:center;justify-content:center;transition:border-color .15s,color .15s;flex-shrink:0}.nb:hover{border-color:var(--acc);color:var(--acc)}#addr{flex:1;background:#050809;border:1px solid var(--bdr);color:var(--text);font-family:'Share Tech Mono',monospace;font-size:13px;padding:0 12px;height:32px;outline:none;transition:border-color .2s,box-shadow .2s;min-width:0}#addr:focus{border-color:var(--acc);box-shadow:var(--glow)}#btn-go{background:var(--acc);color:#000;border:none;font-family:'Orbitron',sans-serif;font-size:11px;font-weight:700;letter-spacing:1px;padding:0 14px;height:32px;cursor:pointer;flex-shrink:0;transition:opacity .15s}#btn-go:hover{opacity:.85}#btn-tb{background:none;border:1px solid var(--acc2);color:var(--acc2);font-family:'Orbitron',sans-serif;font-size:10px;font-weight:700;letter-spacing:1px;padding:0 10px;height:32px;cursor:pointer;flex-shrink:0;white-space:nowrap;transition:background .15s,color .15s}#btn-tb:hover{background:var(--acc2);color:#000}#btn-out{background:none;border:1px solid var(--bdr);color:var(--danger);font-size:12px;width:32px;height:32px;border-radius:4px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}#vp{flex:1;background:#000;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}#load-bar{position:absolute;top:0;left:0;height:3px;background:linear-gradient(90deg,var(--acc),var(--acc2));transition:width .3s ease;z-index:100;box-shadow:0 0 10px var(--acc);border-radius:0 2px 2px 0}#feed{max-width:100%;max-height:100%;display:block;cursor:crosshair;object-fit:contain;outline:none}#ov{position:absolute;inset:0;background:rgba(0,0,0,.85);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;color:var(--muted);font-family:'Orbitron',sans-serif;font-size:13px;letter-spacing:2px}.spin{width:40px;height:40px;border:3px solid var(--bdr);border-top-color:var(--acc);border-radius:50%;animation:spin .8s linear infinite;display:none}@keyframes spin{to{transform:rotate(360deg)}}#ov-msg{text-align:center;padding:0 20px;line-height:1.6}#ov-pct{font-family:'Orbitron',sans-serif;font-size:22px;font-weight:700;color:var(--acc);text-shadow:0 0 20px var(--acc)}#sb{background:var(--surf);border-top:1px solid var(--bdr);height:22px;display:flex;align-items:center;padding:0 10px;gap:14px;font-size:10px;color:var(--muted);flex-shrink:0;letter-spacing:.5px;overflow:hidden}.si{display:flex;align-items:center;gap:5px;white-space:nowrap}.dot{width:5px;height:5px;border-radius:50%;background:var(--acc);animation:blink 2s ease-in-out infinite}@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}#tbx{position:fixed;top:0;right:-340px;width:320px;height:100%;background:var(--surf);border-left:1px solid var(--acc2);box-shadow:-4px 0 30px rgba(0,207,255,.1);z-index:200;transition:right .25s cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;overflow:hidden}#tbx.open{right:0}.th{background:var(--surf2);border-bottom:1px solid var(--bdr);padding:14px 16px;display:flex;align-items:center;justify-content:space-between}.tt{font-family:'Orbitron',sans-serif;font-size:13px;font-weight:700;color:var(--acc2);letter-spacing:2px}.tc{background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer;line-height:1;transition:color .15s}.tc:hover{color:var(--danger)}.tabs{display:flex;border-bottom:1px solid var(--bdr)}.tab{flex:1;background:none;border:none;color:var(--muted);font-family:'Share Tech Mono',monospace;font-size:11px;padding:10px 4px;cursor:pointer;border-bottom:2px solid transparent;transition:color .15s,border-color .15s;letter-spacing:.5px;text-transform:uppercase}.tab.on{color:var(--acc2);border-color:var(--acc2)}.tbody{flex:1;overflow-y:auto;padding:16px}.tbody::-webkit-scrollbar{width:4px}.tbody::-webkit-scrollbar-thumb{background:var(--bdr)}.tsec{display:none}.tsec.on{display:block}.lbl{color:var(--acc2);font-size:10px;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;display:block}.fi,.fs,.fta{width:100%;background:#050809;border:1px solid var(--bdr);color:var(--text);font-family:'Share Tech Mono',monospace;font-size:12px;padding:8px 10px;outline:none;transition:border-color .2s;margin-bottom:12px}.fi:focus,.fs:focus,.fta:focus{border-color:var(--acc2)}.fta{height:80px;resize:vertical}.fs option{background:#0d1117}.xbtn{width:100%;background:transparent;border:1px solid var(--acc2);color:var(--acc2);font-family:'Share Tech Mono',monospace;font-size:12px;padding:9px;cursor:pointer;letter-spacing:1px;text-transform:uppercase;transition:background .15s,color .15s;margin-bottom:8px}.xbtn:hover{background:var(--acc2);color:#000}.xbtn.g{border-color:var(--acc);color:var(--acc)}.xbtn.g:hover{background:var(--acc);color:#000}.ua-pre{background:var(--surf2);border:1px solid var(--bdr);color:var(--text);font-size:11px;padding:7px 10px;margin-bottom:6px;cursor:pointer;width:100%;text-align:left;border-left:3px solid transparent;transition:border-left-color .15s}.ua-pre:hover{border-left-color:var(--acc2)}.pills{display:flex;gap:8px;margin-bottom:12px}.pill{flex:1;background:var(--surf2);border:1px solid var(--bdr);color:var(--muted);font-family:'Orbitron',sans-serif;font-size:11px;padding:9px;cursor:pointer;text-align:center;letter-spacing:1px;transition:all .15s}.pill.on{border-color:var(--acc);color:var(--acc);background:#001a0d}.srcbox{background:#050809;border:1px solid var(--bdr);color:var(--acc);font-size:10px;height:280px;overflow:auto;padding:8px;white-space:pre-wrap;word-break:break-all;font-family:'Share Tech Mono',monospace;line-height:1.5;margin-bottom:8px}.kb-row{display:flex;gap:4px;margin-bottom:4px;flex-wrap:wrap}.key{background:var(--surf2);border:1px solid var(--bdr);color:var(--text);font-family:'Share Tech Mono',monospace;font-size:13px;min-width:30px;height:36px;padding:0 6px;cursor:pointer;display:flex;align-items:center;justify-content:center;border-radius:3px;transition:background .1s,transform .1s;user-select:none;-webkit-user-select:none;flex:1}.key:active,.key.hit{background:var(--acc2);color:#000;border-color:var(--acc2);transform:scale(.92)}.key.w{flex:2}.key.xw{flex:3}.key.sp{flex:5;min-width:80px}.key.sk{color:var(--acc2);border-color:var(--acc2);background:rgba(0,207,255,.06);font-size:11px}.key.dk{color:var(--danger);border-color:var(--danger);background:rgba(255,68,68,.06)}.srow{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}#op-wrap{margin-top:16px;padding:12px 10px 10px;background:var(--surf2);border:1px solid var(--bdr);border-radius:3px}.op-lbl{display:flex;justify-content:space-between;align-items:center;font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:var(--acc2);margin-bottom:10px}#op-val{font-family:'Orbitron',sans-serif;font-size:11px;font-weight:700;color:var(--acc);min-width:38px;text-align:right}.op-tw{display:flex;align-items:center;gap:8px;margin-bottom:10px}.oi{font-size:14px;flex-shrink:0}.oi.dim{color:var(--muted)}.oi.br{color:var(--warn);text-shadow:0 0 8px var(--warn)}.op-track{flex:1;position:relative;height:28px;display:flex;align-items:center}#op-bar{position:absolute;left:0;top:50%;transform:translateY(-50%);height:4px;border-radius:2px;background:linear-gradient(90deg,rgba(0,255,144,.15),var(--acc));pointer-events:none}.op-rng{-webkit-appearance:none;appearance:none;width:100%;height:4px;background:var(--bdr);border-radius:2px;outline:none;cursor:pointer;position:relative;z-index:1}.op-rng::-webkit-slider-thumb{-webkit-appearance:none;width:22px;height:22px;border-radius:50%;background:var(--acc);border:3px solid #000;box-shadow:0 0 8px rgba(0,255,144,.5);cursor:pointer}.op-rng:active::-webkit-slider-thumb{box-shadow:0 0 16px rgba(0,255,144,.8)}.op-rng::-moz-range-thumb{width:20px;height:20px;border-radius:50%;background:var(--acc);border:3px solid #000}.op-pre{display:flex;gap:6px}.op-btn{flex:1;background:var(--surf);border:1px solid var(--bdr);color:var(--muted);font-family:'Share Tech Mono',monospace;font-size:10px;padding:5px 0;cursor:pointer;letter-spacing:.5px;text-transform:uppercase;border-radius:2px}.op-btn:hover,.op-btn.on{border-color:var(--acc2);color:var(--acc2);background:rgba(0,207,255,.08)}#bk{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:199}@media(max-width:540px){#tbx{width:100%;right:-100%}#tbx.open{right:0}#bk.show{display:block}}</style></head><body><div id="tb"><span class="brand">QS</span><button class="nb" onclick="nav_back()">&#9668;</button><button class="nb" onclick="nav_fwd()">&#9658;</button><button class="nb" onclick="nav_reload()">&#8635;</button><input id="addr" type="text" placeholder="https://example.com" spellcheck="false"><button id="btn-go" onclick="go()">GO</button><button id="btn-tb" onclick="openTB()">⚙ TOOLBOX</button><button id="btn-out" onclick="logout()" title="Logout">⏻</button></div><div id="vp"><div id="load-bar" style="width:0%"></div><img id="feed" alt="" tabindex="0"><div id="ov"><div class="spin" id="spin"></div><span id="ov-pct"></span><span id="ov-msg">⬆ ENTER A URL AND PRESS GO</span></div></div><div id="sb"><div class="si"><span class="dot"></span><span id="s-url">IDLE</span></div><div class="si">│ <span id="s-mode">DESKTOP</span></div><div class="si" style="margin-left:auto"><span id="s-hw" style="color:var(--muted)">⚙ detecting...</span></div></div><div id="bk" onclick="closeTB()"></div><div id="tbx"><div class="th"><span class="tt">⚙ TOOLBOX</span><button class="tc" onclick="closeTB()">✕</button></div><div class="tabs"><button class="tab on" onclick="tab('ua')">UA</button><button class="tab" onclick="tab('view')">View</button><button class="tab" onclick="tab('kb')">⌨ KB</button><button class="tab" onclick="tab('src')">Src</button></div><div class="tbody"><div id="t-ua" class="tsec on"><span class="lbl">Quick Presets</span><button class="ua-pre" onclick="setUA('cw')">🖥 Chrome — Windows</button><button class="ua-pre" onclick="setUA('cm')">🍎 Chrome — macOS</button><button class="ua-pre" onclick="setUA('fw')">🦊 Firefox — Windows</button><button class="ua-pre" onclick="setUA('sm')">🧭 Safari — macOS</button><button class="ua-pre" onclick="setUA('ew')">🌐 Edge — Windows</button><button class="ua-pre" onclick="setUA('ip')">📱 iPhone 16</button><button class="ua-pre" onclick="setUA('an')">🤖 Android Chrome</button><button class="ua-pre" onclick="setUA('gb')">🤖 Googlebot</button><span class="lbl" style="margin-top:12px">Custom UA</span><textarea class="fta" id="ua-custom" placeholder="Paste custom user agent..."></textarea><button class="xbtn g" onclick="applyUA()">▶ Apply & Reload</button><div id="ua-cur" style="font-size:10px;color:var(--muted);margin-top:4px;word-break:break-all;line-height:1.4"></div></div><div id="t-view" class="tsec"><span class="lbl">Viewport Mode</span><div class="pills"><div class="pill on" id="p-desk" onclick="setMode('desktop')">🖥 DESKTOP</div><div class="pill" id="p-mob" onclick="setMode('mobile')">📱 MOBILE</div></div><span class="lbl">Mobile Presets</span><select class="fs" id="mob-pre" onchange="applyMobPre()"><option value="">-- Select preset --</option><option value="ip16">iPhone 16 Pro (393×852)</option><option value="ipse">iPhone SE (375×667)</option><option value="px8">Pixel 8 (412×915)</option><option value="ss24">Samsung S24 (360×780)</option><option value="ipad">iPad Pro (1024×1366)</option></select><span class="lbl">Scroll Speed</span><input class="fi" type="range" id="spd" min="100" max="1200" value="400" style="padding:0;background:none;border:none;cursor:pointer"><div style="font-size:10px;color:var(--muted);margin-top:-8px;margin-bottom:12px">Pixels/scroll: <span id="spd-val">400</span></div><button class="xbtn" onclick="scrollUp()">▲ Scroll Up</button><button class="xbtn" onclick="scrollDown()">▼ Scroll Down</button></div><div id="t-kb" class="tsec"><div style="color:var(--acc2);font-size:10px;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:10px">⌨ On-Screen Keyboard</div><div style="display:flex;gap:6px;margin-bottom:10px"><input class="fi" id="kb-in" type="text" placeholder="Type text to send..." style="flex:1;margin-bottom:0" autocorrect="off" autocapitalize="off" spellcheck="false"><button class="xbtn" onclick="kbSend()" style="width:auto;padding:0 12px;margin-bottom:0;flex-shrink:0">SEND</button></div><div id="kb-rows"></div><div style="margin-top:8px"><div style="color:var(--muted);font-size:10px;letter-spacing:1px;margin-bottom:6px">SPECIAL KEYS</div><div class="srow" id="kb-spec"></div></div><div style="color:var(--muted);font-size:10px;margin-top:10px;line-height:1.5">Tap keys → sent to remote browser.<br>Use text field for longer input.</div><div id="op-wrap"><div class="op-lbl"><span>☀ Panel Transparency</span><span id="op-val">100%</span></div><div class="op-tw"><span class="oi dim">◐</span><div class="op-track"><div id="op-bar" style="width:100%"></div><input type="range" id="op-rng" min="15" max="100" value="100" class="op-rng" oninput="setOpacity(this.value)"></div><span class="oi br">☀</span></div><div class="op-pre"><button class="op-btn" onclick="setOpacity(30)">Ghost</button><button class="op-btn" onclick="setOpacity(55)">Half</button><button class="op-btn" onclick="setOpacity(80)">Dim</button><button class="op-btn on" onclick="setOpacity(100)">Full</button></div></div></div><div id="t-src" class="tsec"><button class="xbtn g" onclick="fetchSrc()">⟳ Fetch Source</button><div id="src-box" class="srcbox">Click "Fetch Source" to load...</div><button class="xbtn" onclick="copySrc()">⎘ Copy</button><button class="xbtn" onclick="dlSrc()">⬇ Download .html</button></div></div></div><script>const S={mobile:false,vpW:1280,vpH:720,ua:'',scrollPx:400,nav:false,srcCache:'',lastTouch:false,feedActive:false};const UAS={cw:'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',cm:'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',fw:'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0',sm:'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',ew:'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',ip:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1',an:'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36',gb:'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'};async function api(ep,data={}){try{const r=await fetch(ep,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify(data),credentials:'same-origin'});if(r.status===401){location.href='/login';return null}return await r.json()}catch{return null}}const feed=document.getElementById('feed'),addr=document.getElementById('addr'),ov=document.getElementById('ov'),spin=document.getElementById('spin'),omsg=document.getElementById('ov-msg'),opct=document.getElementById('ov-pct'),loadBar=document.getElementById('load-bar');function showLoad(on,msg){ov.style.display=on?'flex':'none';spin.style.display=on?'block':'none';if(msg)omsg.textContent=msg;if(!on){opct.textContent='';loadBar.style.width='0%'}}function startFeed(){if(S.feedActive)return;S.feedActive=true;feed.src='/video_feed?t='+Date.now()}let loadIv=null;function pollLoadStatus(){if(loadIv)clearInterval(loadIv);loadIv=setInterval(async()=>{try{const r=await fetch('/load_status',{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});if(!r.ok)return;const d=await r.json();const pct=d.percent||0;loadBar.style.width=pct+'%';if(d.phase==='navigating'){omsg.textContent='CONNECTING...';opct.textContent=''}else if(d.phase==='loading'){omsg.textContent=`LOADING RESOURCES (${d.finished}/${d.total})`;opct.textContent=pct+'%'}else if(d.phase==='rendering'){omsg.textContent='RENDERING PAGE...';opct.textContent=pct+'%'}else if(d.phase==='complete'){omsg.textContent='✓ PAGE LOADED';opct.textContent='100%';loadBar.style.width='100%';setTimeout(()=>{showLoad(false);clearInterval(loadIv);loadIv=null},800)}else if(d.phase==='idle'&&!S.nav){clearInterval(loadIv);loadIv=null}}catch{}},400)}function go(){const url=addr.value.trim();if(!url)return;S.nav=true;showLoad(true,'CONNECTING...');loadBar.style.width='5%';api('/navigate',{url,ua:S.ua,mobile:S.mobile});startFeed();pollLoadStatus();setTimeout(()=>{if(S.nav){showLoad(false);if(loadIv){clearInterval(loadIv);loadIv=null}}},25000)}addr.addEventListener('keydown',e=>{if(e.key==='Enter')go()});function nav_back(){api('/action',{action:'go_back'})}function nav_fwd(){api('/action',{action:'go_forward'})}function nav_reload(){S.nav=true;showLoad(true,'RELOADING...');api('/action',{action:'reload'});pollLoadStatus();setTimeout(()=>{showLoad(false);if(loadIv){clearInterval(loadIv);loadIv=null}},20000)}function logout(){if(confirm('Logout?'))location.href='/logout'}feed.onload=()=>{};feed.onerror=()=>{if(S.nav){S.feedActive=false;setTimeout(startFeed,1000)}};setInterval(async()=>{const res=await api('/get_state',{lite:true});if(!res)return;if(res.url&&res.url!=='about:blank'&&document.activeElement!==addr)addr.value=res.url;if(S.nav&&res.url&&res.url!=='about:blank'){S.nav=false}document.getElementById('s-url').textContent=(res.title||res.url||'IDLE').substring(0,70);if(res.vp_width)S.vpW=res.vp_width;if(res.vp_height)S.vpH=res.vp_height;if(typeof res.mobile==='boolean'&&res.mobile!==S.mobile){S.mobile=res.mobile;document.getElementById('p-desk').classList.toggle('on',!S.mobile);document.getElementById('p-mob').classList.toggle('on',S.mobile);document.getElementById('s-mode').textContent=S.mobile?'MOBILE':'DESKTOP'}S.ua=res.ua||'';document.getElementById('ua-cur').textContent=S.ua?'Current: '+S.ua.substring(0,80)+'...':''},2000);(async()=>{try{const r=await fetch('/hw_info',{credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest'}});if(!r.ok)return;const h=await r.json();const el=document.getElementById('s-hw');el.textContent=`${h.gpu_accel?'⚡':'🖥'} ${h.gpu_accel?h.gpu:'CPU'} │ ${h.cpu_cores}c │ ${h.ram_gb}GB │ ${h.active_fps}fps │ ${h.arch} │ CDP`;el.style.color=h.gpu_accel?'var(--acc)':'var(--warn)'}catch{}})();let tx0=0,ty0=0,ty_l=0,t0=0,sw=false,lsc=0;const SP=8,TH=60;function coords(cx,cy){const r=feed.getBoundingClientRect();return{x:(cx-r.left)*(S.vpW/r.width),y:(cy-r.top)*(S.vpH/r.height)}}feed.addEventListener('click',e=>{if(S.lastTouch){S.lastTouch=false;return}feed.focus();const{x,y}=coords(e.clientX,e.clientY);api('/interact',{type:'click',x,y})});feed.addEventListener('touchstart',e=>{e.preventDefault();S.lastTouch=true;const t=e.touches[0];tx0=t.clientX;ty0=ty_l=t.clientY;t0=Date.now();sw=false;lsc=0},{passive:false});feed.addEventListener('touchmove',e=>{e.preventDefault();const t=e.touches[0];const dy=ty0-t.clientY,dx=tx0-t.clientX;if(!sw&&(Math.abs(dy)>SP||Math.abs(dx)>SP))sw=true;if(!sw||Math.abs(dy)<Math.abs(dx))return;const now=Date.now();if(now-lsc<TH)return;lsc=now;const delta=ty_l-t.clientY;ty_l=t.clientY;if(Math.abs(delta)<2)return;const r=feed.getBoundingClientRect();api('/interact',{type:'touch_scroll',dy:delta*(S.vpH/r.height)})},{passive:false});feed.addEventListener('touchend',e=>{e.preventDefault();if(!sw&&Date.now()-t0<400){const{x,y}=coords(tx0,ty0);api('/interact',{type:'click',x,y})}sw=false},{passive:false});document.addEventListener('keydown',function(e){if(document.activeElement===addr)return;if(document.activeElement&&document.activeElement.id==='kb-in')return;if(document.activeElement&&document.activeElement.tagName==='TEXTAREA')return;if(document.activeElement&&document.activeElement.tagName==='SELECT')return;var mods='';if(e.ctrlKey)mods+='Control+';if(e.altKey)mods+='Alt+';if(e.metaKey)mods+='Meta+';if(e.shiftKey&&e.key.length>1)mods+='Shift+';var specialKeys=['Enter','Backspace','Tab','Escape','Delete','Insert','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown','F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12'];var k=null;if(specialKeys.includes(e.key)){k=e.key;}else if(e.key===' '){k=' ';}else if(e.key.length===1){if(e.shiftKey)mods+='Shift+';k=e.key;}if(k){e.preventDefault();e.stopPropagation();api('/interact',{type:'key',key:mods+k});}},true);function openTB(){document.getElementById('tbx').classList.add('open');document.getElementById('bk').classList.add('show')}function closeTB(){document.getElementById('tbx').classList.remove('open');document.getElementById('bk').classList.remove('show')}function tab(t){['ua','view','kb','src'].forEach((n,i)=>{document.querySelectorAll('.tab')[i].classList.toggle('on',n===t);document.getElementById('t-'+n).classList.toggle('on',n===t)})}function setUA(k){const ua=UAS[k]||'';document.getElementById('ua-custom').value=ua;S.ua=ua;if(['ip','an'].includes(k))setMode('mobile');api('/action',{action:'change_ua',data:{ua}})}function applyUA(){const ua=document.getElementById('ua-custom').value.trim();if(!ua)return;S.ua=ua;api('/action',{action:'change_ua',data:{ua}})}function setMode(m){S.mobile=m==='mobile';document.getElementById('p-desk').classList.toggle('on',!S.mobile);document.getElementById('p-mob').classList.toggle('on',S.mobile);document.getElementById('s-mode').textContent=S.mobile?'MOBILE':'DESKTOP';if(addr.value)go()}function applyMobPre(){if(document.getElementById('mob-pre').value)setMode('mobile')}document.getElementById('spd').addEventListener('input',function(){S.scrollPx=parseInt(this.value);document.getElementById('spd-val').textContent=this.value});function scrollUp(){api('/interact',{type:'scroll_up',px:S.scrollPx})}function scrollDown(){api('/interact',{type:'scroll_down',px:S.scrollPx})}async function fetchSrc(){document.getElementById('src-box').textContent='Fetching...';await api('/action',{action:'get_source'});await new Promise(r=>setTimeout(r,700));const res=await api('/get_state',{});if(res&&res.source){S.srcCache=res.source;document.getElementById('src-box').textContent=res.source}else document.getElementById('src-box').textContent='No source available.'}function copySrc(){if(!S.srcCache){alert('Fetch first.');return}navigator.clipboard.writeText(S.srcCache).then(()=>alert('Copied!'))}function dlSrc(){if(!S.srcCache){alert('Fetch first.');return}const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([S.srcCache],{type:'text/html'}));a.download='source_'+Date.now()+'.html';a.click()}function setOpacity(v){v=Math.max(15,Math.min(100,parseInt(v)));document.getElementById('op-rng').value=v;document.getElementById('op-bar').style.width=v+'%';document.getElementById('op-val').textContent=v+'%';const p=document.getElementById('tbx');p.style.opacity=(v/100).toFixed(2);p.style.pointerEvents=v<30?'none':'auto';document.querySelectorAll('.op-btn').forEach((b,i)=>b.classList.toggle('on',[30,55,80,100][i]===v))}(function(){const ROWS=[['`','1','2','3','4','5','6','7','8','9','0','-','='],['q','w','e','r','t','y','u','i','o','p','[',']','\\'],['a','s','d','f','g','h','j','k','l',';',"'"],['z','x','c','v','b','n','m',',','.','/']];const SHF={'`':'~','1':'!','2':'@','3':'#','4':'$','5':'%','6':'^','7':'&','8':'*','9':'(','0':')','-':'_','=':'+','[':'{',']':'}','\\':'|',';':':',"'":'"',',':'<','.':'>','/':'?'};const SPEC=[{l:'TAB',k:'Tab',c:'w sk'},{l:'CAPS',k:'CapsLock',c:'w sk'},{l:'SHIFT',k:'Shift',c:'w sk'},{l:'CTRL',k:'Control',c:'w sk'},{l:'ALT',k:'Alt',c:'w sk'},{l:'ESC',k:'Escape',c:'w sk dk'},{l:'⌫',k:'Backspace',c:'w dk'},{l:'↵',k:'Enter',c:'w sk'},{l:'␣ SPACE',k:' ',c:'sp'},{l:'DEL',k:'Delete',c:'w dk'},{l:'↑',k:'ArrowUp',c:'sk'},{l:'↓',k:'ArrowDown',c:'sk'},{l:'←',k:'ArrowLeft',c:'sk'},{l:'→',k:'ArrowRight',c:'sk'}];let shft=false,caps=false,mods={Shift:false,Control:false,Alt:false};const rows=document.getElementById('kb-rows'),spec=document.getElementById('kb-spec');function send(k){let ch='';if(mods.Control)ch+='Control+';if(mods.Alt)ch+='Alt+';if(mods.Shift||shft)ch+='Shift+';api('/interact',{type:'key',key:ch?ch+k:k});document.querySelectorAll('.key').forEach(b=>{if(b.dataset.k===k){b.classList.add('hit');setTimeout(()=>b.classList.remove('hit'),150)}});if(mods.Control){mods.Control=false;updM('Control')}if(mods.Alt){mods.Alt=false;updM('Alt')}if(shft&&!caps){shft=false;build();updM('Shift')}}function updM(n){document.querySelectorAll('.key').forEach(b=>{if(b.dataset.k===n){const on=mods[n]||(n==='Shift'&&shft)||(n==='CapsLock'&&caps);b.style.background=on?'var(--acc2)':'';b.style.color=on?'#000':'';b.style.borderColor=on?'var(--acc2)':''}})}function build(){rows.innerHTML='';ROWS.forEach(row=>{const d=document.createElement('div');d.className='kb-row';row.forEach(k=>{const b=document.createElement('button');b.className='key';b.dataset.k=k;b.textContent=(caps||shft)?(SHF[k]||k.toUpperCase()):k;b.addEventListener('pointerdown',ev=>{ev.preventDefault();send((caps||shft)?(SHF[k]||k.toUpperCase()):k)});d.appendChild(b)});rows.appendChild(d)})}SPEC.forEach(s=>{const b=document.createElement('button');b.className='key '+(s.c||'');b.dataset.k=s.k;b.textContent=s.l;b.addEventListener('pointerdown',ev=>{ev.preventDefault();if(s.k==='CapsLock'){caps=!caps;updM('CapsLock');build()}else if(s.k==='Shift'){shft=!shft;updM('Shift');build()}else if(['Control','Alt'].includes(s.k)){mods[s.k]=!mods[s.k];updM(s.k)}else send(s.k)});spec.appendChild(b)});build();document.getElementById('kb-in').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();kbSend()}e.stopPropagation()})})();function kbSend(){const el=document.getElementById('kb-in'),txt=el.value;if(!txt)return;[...txt].forEach((c,i)=>setTimeout(()=>api('/interact',{type:'key',key:c}),i*30));setTimeout(()=>{el.value='';el.focus()},txt.length*30+50)}</script></body></html>"""

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
def load_status(): return jsonify(worker.get_load_status())
@app.route("/video_feed")
@login_required
def video_feed():
    def gen():
        last=None
        while True:
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
        "active_fps":CDPWorker.ACTIVE_FPS,"idle_fps":CDPWorker.IDLE_FPS,"engine":"CDP","chromium":worker._chrome_ver,
        "container":IN_CONTAINER,"arch":ARCH_LABEL})
@app.route("/debug_cdp")
@login_required
def debug_cdp():
    return jsonify({"ws_connected":worker._ws_connected,"session_id":worker._session_id,"target_id":worker._target_id,
        "chromium_ver":worker._chrome_ver,"chromium_bin":CHROME_BIN,"frame_count":worker._frame_n,
        "has_frame":LATEST_FRAME is not None,"frame_size":len(LATEST_FRAME) if LATEST_FRAME else 0,
        "navigating":worker._navigating,"pending_requests":worker._pending_requests,
        "load_status":worker.get_load_status(),
        "chromium_stderr":worker._chrome_stderr[:500] if worker._chrome_stderr else "",
        "container":IN_CONTAINER,"arch":ARCH_LABEL,
        "libs_dir":str(LIBS_DIR),"libs_dir_exists":LIBS_DIR.is_dir(),
        "ld_library_path":_build_lib_env().get("LD_LIBRARY_PATH","")})

if __name__=="__main__":
    try: import psutil
    except: pass
    os.system('cls' if os.name=='nt' else 'clear')
    banner=pyfiglet.figlet_format("QuantumSurf",font="slant")
    print(colored(banner,"cyan"))
    print(colored("="*60,"cyan"))
    print(colored("  QuantumSurf — Made by Aryan Giri","yellow",attrs=["bold"]))
    print(colored("  Privacy Toolkit — Remote Browser Isolation","white"))
    print(colored("  Engine: Direct CDP · Real Chromium (amd64 + arm64) · No Snap","green"))
    print(colored("="*60,"cyan"))
    print(colored(f"  Architecture : {ARCH_LABEL} ({ARCH or 'UNSUPPORTED'})","white"))
    print(colored(f"  Chromium     : {CHROME_BIN or 'NOT FOUND'}","green" if CHROME_BIN else "red"))
    print(colored(f"  GPU          : {'YES ('+GPU_VENDOR+')' if HAS_GPU else 'No — CPU mode'}","green" if HAS_GPU else "yellow"))
    print(colored(f"  CPU          : {CPU_CORES} cores","white"))
    print(colored(f"  Container    : {'YES' if IN_CONTAINER else 'No'}","yellow" if IN_CONTAINER else "white"))
    print(colored(f"  FPS          : {CDPWorker.ACTIVE_FPS} active / {CDPWorker.IDLE_FPS} idle","white"))
    print(colored(f"  JPEG Quality : {CDPWorker.QUALITY}","white"))
    print(colored(f"  Local libs   : {LIBS_DIR} ({'EXISTS' if LIBS_DIR.is_dir() else 'not yet'})","white"))
    print(colored(f"  Debug        : {'ON' if DEBUG_CDP else 'OFF (QS_DEBUG=1 to enable)'}","white"))
    print(colored("="*60+"\n","cyan"))
    print(colored("[*] Default credentials in auth.txt","magenta"))
    print(colored("[*] http://0.0.0.0:8000\n","green"))
    if not CHROME_BIN:
        print(colored("[!] FATAL: No working Chromium found!","red"))
        if not ARCH:
            print(colored(f"    Unsupported CPU: {ARCH_LABEL}. Only amd64/x86_64 and arm64/aarch64 supported.","yellow"))
        else:
            print(colored(f"    Manual install for {ARCH}:","yellow"))
            print(colored(f"    sudo apt-get update && sudo apt-get install -y chromium","yellow"))
        exit(1)
    app.run(host="0.0.0.0",port=8000,threaded=True)
