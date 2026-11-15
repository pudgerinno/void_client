"""Сканер модов на опасные паттерны."""
import os, json, zipfile, hashlib

from core import config, security

CRIT = [
    (b"java/lang/Runtime", "вызов системных команд (Runtime)"),
    (b"java/lang/ProcessBuilder", "запуск внешних процессов"),
    (b"java/net/URLClassLoader", "загрузка кода из сети"),
    (b"java/awt/Robot", "эмуляция ввода (потенциальный кейлоггер)"),
    (b"sun/misc/Unsafe", "прямой доступ к памяти"),
    (b"javax/script/ScriptEngine", "выполнение скриптов"),
    (b"java/lang/ClassLoader.defineClass", "инжекция байткода"),
]
WARN = [
    (b"setAccessible", "рефлексия с отключением проверок"),
    (b"java/awt/datatransfer/Clipboard", "чтение буфера обмена"),
    (b"java/lang/System.getenv", "чтение переменных окружения"),
    (b"java/security/KeyStore", "доступ к хранилищу ключей"),
    (b"java/awt/Desktop", "открытие браузера/файлов"),
    (b"java/io/FileOutputStream", "запись файлов"),
]
INFO = [
    (b"java/net/Socket", "сетевые сокеты"),
    (b"java/net/HttpURLConnection", "HTTP-запросы"),
    (b"java/net/http/HttpClient", "HTTP-клиент"),
]
EXFIL = [b"discord.com/api/webhooks", b"pastebin", b"ngrok", b".onion", b"requestbin"]
CACHE = os.path.join(config.CONFIG_DIR, "modscan.json")


def _sig(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return f"{os.path.getsize(path)}:{h.hexdigest()[:16]}"


def scan_jar(path):
    crit, warn, info = [], [], []
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith(".class")][:20000]
            for n in names:
                try:
                    data = z.read(n)
                except Exception:
                    continue
                if len(data) > 2_000_000:
                    continue
                for pat, d in CRIT:
                    if pat in data:
                        crit.append((d, n))
                for pat, d in WARN:
                    if pat in data:
                        warn.append((d, n))
                for pat, d in INFO:
                    if pat in data:
                        info.append((d, n))
                for pat in EXFIL:
                    if pat in data:
                        crit.append(("эксфильтрация: " + pat.decode(), n))
    except Exception as e:
        warn.append((f"не удалось просканировать: {e}", os.path.basename(path)))

    def dedup(lst):
        seen, out = set(), []
        for d, n in lst:
            if d not in seen:
                seen.add(d)
                out.append((d, n))
        return out
    return dedup(crit), dedup(warn), dedup(info)


def scan_instance(inst_path, force=False):
    mods = os.path.join(inst_path, "mods")
    empty = {"crit": [], "warn": [], "info": [], "files": 0}
    if not os.path.isdir(mods):
        return empty
    cache = {}
    try:
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        pass
    crit, warn, info, files = [], [], [], 0
    for fn in sorted(os.listdir(mods)):
        if not fn.endswith(".jar"):
            continue
        p = os.path.join(mods, fn)
        sig = _sig(p)
        files += 1
        if not force and fn in cache and cache[fn].get("sig") == sig:
            r = cache[fn]["r"]
        else:
            c, w, i = scan_jar(p)
            r = {"crit": c, "warn": w, "info": i}
            cache[fn] = {"sig": sig, "r": r}
        crit += [(fn, d, n) for d, n in r["crit"]]
        warn += [(fn, d, n) for d, n in r["warn"]]
        info += [(fn, d, n) for d, n in r["info"]]
    try:
        security.secure_write(CACHE, json.dumps(cache))
    except Exception:
        pass
    return {"crit": crit, "warn": warn, "info": info, "files": files}