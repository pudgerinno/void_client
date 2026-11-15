"""HTTPS-only сессия, анти-SSRF, параллельные загрузки, хэши."""
import hashlib, os, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

from core import security

UA = {"User-Agent": "VoidLauncher/7.0"}
_session = requests.Session()
_session.headers.update(UA)
_session.trust_env = False
_ad = requests.adapters.HTTPAdapter(pool_connections=48, pool_maxsize=96, max_retries=2)
_session.mount("https://", _ad)
_lock = threading.Lock()
STRICT = True


def set_strict(v):
    global STRICT
    STRICT = bool(v)
    security.set_strict_dns(v)


def _guard(url):
    if not security.url_ok(url, STRICT):
        raise IOError("Заблокирован небезопасный адрес: " + url.split("?")[0][:80])


def get_json(url, **kw):
    _guard(url)
    r = _session.get(url, timeout=45, verify=True, **kw)
    r.raise_for_status()
    return r.json()


def get_conditional(url, etag=None):
    _guard(url)
    headers = {"If-None-Match": etag} if etag else {}
    r = _session.get(url, timeout=45, headers=headers, verify=True)
    if r.status_code == 304:
        return False, None, etag
    r.raise_for_status()
    return True, r.json(), r.headers.get("ETag")


def get_bytes(url, limit=4 << 20):
    _guard(url)
    r = _session.get(url, timeout=25, verify=True, stream=True)
    r.raise_for_status()
    buf = b""
    for c in r.iter_content(65536):
        buf += c
        if len(buf) > limit:
            raise IOError("Слишком большой ответ")
    return buf


def hash_file(path, algo):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def algo_for(c):
    return {40: "sha1", 64: "sha256", 128: "sha512"}.get(len(c or ""))


def file_ok(path, checksum=None, size=None, verify=False):
    if not os.path.exists(path):
        return False
    st = os.path.getsize(path)
    if st == 0:
        return False
    if size is not None and st != size:
        return False
    if checksum and verify:
        a = algo_for(checksum)
        return bool(a) and hash_file(path, a) == checksum
    return True


def download(url, path, checksum=None, size=None, progress=None, verify=False):
    _guard(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if file_ok(path, checksum, size, verify):
        if progress:
            progress(size or 1, size or 1)
        return path
    tmp = path + ".part"
    last = None
    for _ in range(3):
        try:
            with _session.get(url, stream=True, timeout=60, verify=True) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or size or 0)
                done = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total or done)
            if checksum:
                a = algo_for(checksum)
                if a and hash_file(tmp, a) != checksum:
                    raise IOError("hash mismatch " + os.path.basename(path))
            os.replace(tmp, path)
            return path
        except Exception as e:
            last = security.redact(e)
            time.sleep(0.5)
    raise IOError(f"Не скачался {url.split('?')[0][:90]}: {last}")


def parallel(items, worker, threads=16, on_progress=None):
    items = list(items)
    total = len(items)
    if not items:
        if on_progress:
            on_progress(1, 1)
        return []
    done = 0
    res = []
    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        for fut in as_completed([ex.submit(worker, it) for it in items]):
            res.append(fut.result())
            with _lock:
                done += 1
                if on_progress:
                    on_progress(done, total)
    return res