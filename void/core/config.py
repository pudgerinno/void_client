"""Настройки с правами 0700/0600. Шифрование секретов опционально."""
import getpass, json, os, re, sys

from core import security

APP = "void-launcher"
DATA_DIR = os.path.expanduser(f"~/.local/share/{APP}")
CONFIG_DIR = os.path.expanduser(f"~/.config/{APP}")
VERSIONS_DIR = os.path.join(DATA_DIR, "versions")
SHARED_DIR = os.path.join(DATA_DIR, "shared")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")
LOG_PATH = os.path.join(CONFIG_DIR, "launcher.log")
VERSIONS_CACHE = os.path.join(CONFIG_DIR, "versions.json")
_CRYPTO_WARNED = False


def _defaults():
    return {
        "last_page": "home", "ram_mb": 3072, "threads": 16,
        "java_path": "", "java_args": "", "verify_files": False,
        "cf_key": "", "prime_run": True,
        "online_mode": False, "whitelist": True,
        "friends": [], "accounts": [], "active_account": "",
        "active_instance": "",
        "allow_stun": True, "strict_urls": True, "link_confirm": True,
        "sandbox": True, "net_isolate": False, "allow_x11": True,
        "force_nvidia": False,
    }


def _clean(s):
    s = re.sub(r"[^A-Za-z0-9_]", "", s or "")[:16]
    return s if len(s) >= 3 else "Player"


def _secret_key():
    try:
        import base64
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        mid = "no-machine-id"
        try:
            with open("/etc/machine-id") as f:
                mid = f.read().strip()
        except Exception:
            pass
        mat = f"{mid}:{os.getuid()}".encode()
        return base64.urlsafe_b64encode(
            HKDF(algorithm=hashes.SHA256(), length=32, salt=b"void-launcher",
                 info=b"cfkey").derive(mat))
    except Exception:
        return None


def _crypto_warn():
    global _CRYPTO_WARNED
    if not _CRYPTO_WARNED:
        _CRYPTO_WARNED = True
        print("[config] cryptography недоступна — секреты хранятся без шифрования (0600).",
              file=sys.stderr)


def encrypt_secret(s):
    if not s:
        return ""
    key = _secret_key()
    if key is None:
        _crypto_warn(); return s
    try:
        from cryptography.fernet import Fernet
        return "enc:" + Fernet(key).encrypt(s.encode()).decode()
    except Exception:
        _crypto_warn(); return s


def decrypt_secret(tok):
    if not tok or not str(tok).startswith("enc:"):
        return tok or ""
    key = _secret_key()
    if key is None:
        _crypto_warn(); return ""
    try:
        from cryptography.fernet import Fernet
        return Fernet(key).decrypt(tok[4:].encode()).decode()
    except Exception:
        return ""


def load():
    security.secure_dir(CONFIG_DIR)
    security.secure_dir(DATA_DIR)
    security.secure_dir(VERSIONS_DIR)
    security.secure_dir(SHARED_DIR)
    d = _defaults()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            d.update(json.load(f))
    except Exception:
        pass
    if not d.get("accounts"):
        n = _clean(getpass.getuser())
        d["accounts"] = [{"nick": n}]; d["active_account"] = n
    if not d.get("active_account"):
        d["active_account"] = d["accounts"][0]["nick"]
    d["cf_key"] = decrypt_secret(d.get("cf_key", ""))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return d


def save(cfg):
    out = dict(cfg)
    k = out.get("cf_key", "")
    if k and not str(k).startswith("enc:"):
        out["cf_key"] = encrypt_secret(k)
    security.secure_write(CONFIG_PATH, json.dumps(out, ensure_ascii=False, indent=2))


def paths():
    return {"data": DATA_DIR, "versions": VERSIONS_DIR, "shared": SHARED_DIR, "config": CONFIG_DIR}


def instance_dir(iid):
    return security.safe_join(VERSIONS_DIR, iid)


def list_instances():
    out = []
    for n in os.listdir(VERSIONS_DIR):
        p = os.path.join(VERSIONS_DIR, n, "instance.json")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                pass
    # Сортируем ТОЛЬКО по last_played — сборки, в которые не играли, идут вниз
    out.sort(key=lambda x: x.get("last_played") or 0, reverse=True)
    return out


def get_instance(iid):
    if not iid:
        return None
    try:
        p = os.path.join(instance_dir(iid), "instance.json")
    except ValueError:
        return None
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_instance(inst):
    p = instance_dir(inst["id"])
    security.secure_dir(p)
    security.secure_write(os.path.join(p, "instance.json"),
                          json.dumps(inst, ensure_ascii=False, indent=2))


def save_versions_cache(vs):
    security.secure_write(VERSIONS_CACHE, json.dumps(vs))


def load_versions_cache():
    try:
        with open(VERSIONS_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []