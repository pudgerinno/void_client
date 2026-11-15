"""Права, редакция секретов, безопасные пути, DNS-пиннинг, контроль UID."""
import ipaddress, os, re, socket, tempfile, threading, time, urllib.parse, zipfile, shutil

SECRET_RES = [
    re.compile(r"VOID1:[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\$2a\$10\$[A-Za-z0-9./]{16,}"),
    re.compile(r"(?i)(x-api-key['\"\s:=]+)[^\s'\",}]+"),
    re.compile(r"(?i)(access_?token|session_?id|password|secret)(['\"\s:=]+)[^\s'\",}]+"),
]
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")


def redact(text):
    s = str(text)
    for r in SECRET_RES:
        s = r.sub(lambda m: (m.group(1) + "***") if m.lastindex else "***", s)
    return s


def safe_token(s):
    s = str(s or "")
    if not TOKEN_RE.match(s) or ".." in s:
        raise ValueError("Недопустимый идентификатор: " + s[:40])
    return s


def secure_dir(path):
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def secure_write(path, data, binary=False):
    secure_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_join(root, rel):
    rel = str(rel).replace("\\", "/").lstrip("/")
    if not rel or rel.startswith(("/", "~")) or ":" in rel.split("/")[0]:
        raise ValueError("Небезопасный путь: " + str(rel))
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("Обход каталога: " + str(rel))
    root_r = os.path.realpath(root)
    dest = os.path.realpath(os.path.join(root_r, *parts))
    if dest != root_r and not dest.startswith(root_r + os.sep):
        raise ValueError("Выход за пределы папки: " + str(rel))
    return dest


def safe_extract_zip(z, dest, prefix="", strip=0):
    n = 0
    for info in z.infolist():
        name = info.filename
        if prefix and not name.startswith(prefix):
            continue
        rel = name[len(prefix):] if strip else name
        if not rel or rel.endswith("/"):
            continue
        if ((info.external_attr >> 16) & 0o170000) == 0o120000:
            continue
        target = safe_join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with z.open(info) as src, open(target, "wb") as out:
            while True:
                chunk = src.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
        n += 1
    return n


def safe_extract_tar(t, dest):
    try:
        t.extractall(dest, filter="data")
        return
    except TypeError:
        pass
    for m in t.getmembers():
        if m.issym() or m.islnk() or not (m.isfile() or m.isdir()):
            continue
        target = safe_join(dest, m.name)
        if m.isdir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        src = t.extractfile(m)
        if src is None:
            continue
        with src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


_orig_getaddrinfo = socket.getaddrinfo
_dns_cache = {}
_dns_lock = threading.Lock()
STRICT_DNS = True
DNS_TTL = 60


def set_strict_dns(v):
    global STRICT_DNS
    STRICT_DNS = bool(v)
    if v:
        install_dns_guard()


def _numeric(host):
    try:
        ipaddress.ip_address(host)
        return True
    except (ValueError, TypeError):
        return False


def _guarded_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host is None or not STRICT_DNS:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    if isinstance(host, bytes):
        host = host.decode("idna", "replace")
    if _numeric(host):
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    key = (host, port, family, type, proto, flags)
    now = time.time()
    with _dns_lock:
        ent = _dns_cache.get(key)
        if ent and now - ent[0] < DNS_TTL:
            return ent[1]
    res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    ips = {r[4][0].split("%")[0] for r in res}
    if not ips or not all(ipaddress.ip_address(i).is_global for i in ips):
        raise socket.gaierror(-2, "заблокирован непубличный адрес: " + str(host))
    with _dns_lock:
        _dns_cache[key] = (now, res)
    return res


def install_dns_guard():
    if socket.getaddrinfo is not _guarded_getaddrinfo:
        socket.getaddrinfo = _guarded_getaddrinfo


def url_ok(url, strict=True):
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False
    if u.scheme != "https" or not u.hostname:
        return False
    if not strict:
        return True
    if _numeric(u.hostname):
        try:
            return ipaddress.ip_address(u.hostname).is_global
        except ValueError:
            return False
    try:
        socket.getaddrinfo(u.hostname, 443)
        return True
    except Exception:
        return False


def _addr_hex(ip, port):
    return socket.inet_aton(ip)[::-1].hex().upper() + f":{port:04X}"


def peer_uid(peer, our_port):
    try:
        cip, cport = peer
        loc = _addr_hex(cip, cport)
        rem = _addr_hex("127.0.0.1", our_port)
        with open("/proc/net/tcp", encoding="ascii", errors="replace") as f:
            next(f)
            for line in f:
                p = line.split()
                if len(p) > 7 and p[1] == loc and p[2] == rem:
                    return int(p[7])
    except Exception:
        pass
    return None


def wipe_file(path):
    try:
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.write(b"\x00" * min(size, 1 << 20))
            f.flush()
            os.fsync(f.fileno())
        os.unlink(path)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass


# ==================== ОКРУЖЕНИЕ ДЛЯ ИГРЫ ====================
ENV_ALLOW = {
    # Базовые
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TZ",
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "XDG_DATA_DIRS", "XDG_CONFIG_DIRS",
    "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER", "PIPEWIRE_RUNTIME_DIR",
    "ALSA_CONFIG_PATH", "TMPDIR",
    # NVIDIA / GPU
    "__NV_PRIME_RENDER_OFFLOAD", "__GLX_VENDOR_LIBRARY_NAME",
    "__VK_LAYER_NV_optimus", "__GL_THREADED_OPTIMIZATIONS",
    "__GL_SHADER_DISK_CACHE_PATH", "__GL_YIELD",
    "LD_LIBRARY_PATH", "LD_PRELOAD",
    # Mesa
    "MESA_LOADER_DRIVER_OVERRIDE", "MESA_GL_VERSION_OVERRIDE",
    "MESA_GLSL_VERSION_OVERRIDE", "vblank_mode", "LIBGL_ALWAYS_SOFTWARE",
    "LIBGL_DEBUG", "LIBGL_DRIVERS_PATH",
    # Vulkan
    "VK_ICD_FILENAMES", "VK_LAYER_PATH",
}
ENV_PREFIX = ("LC_", "XDG_", "QT_", "GTK_", "SDL_", "WINE_", "DXVK_")


def build_env():
    """Минимальное окружение для игры. NVIDIA-переменные пробрасываются для prime-run."""
    env = {k: v for k, v in os.environ.items()
           if k in ENV_ALLOW or k.startswith(ENV_PREFIX)}
    env.setdefault("PATH", "/usr/bin:/bin")
    return env