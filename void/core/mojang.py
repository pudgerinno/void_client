"""Версии, Java из 3 источников, библиотеки, ассеты, запуск."""
import hashlib, json, logging, lzma, os, platform, re, shutil, subprocess, sys, tarfile, time, uuid, zipfile

from core import config, net, security

LOG = logging.getLogger("void.mojang")
MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
JAVA_MANIFEST = ("https://launchermeta.mojang.com/v1/products/java-runtime/"
                 "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")
OS = {"Linux": "linux", "Windows": "windows", "Darwin": "osx"}[platform.system()]
ARCH_BITS = "64" if sys.maxsize > 2**32 else "32"

META_DIR = os.path.join(config.SHARED_DIR, "meta")
MANIFEST_CACHE = os.path.join(META_DIR, "manifest.json")
_mem_manifest = None
_vercache = {}
_java_cache = {}


def game_env(settings):
    """Минимальное окружение для игры. GPU-переменные пробрасываются из prime-run."""
    return security.build_env()


def cached_versions():
    global _mem_manifest
    if _mem_manifest is not None:
        return _mem_manifest
    try:
        with open(MANIFEST_CACHE, encoding="utf-8") as f:
            _mem_manifest = json.load(f).get("data", [])
    except Exception:
        _mem_manifest = []
    return _mem_manifest


def cache_age():
    try:
        with open(MANIFEST_CACHE, encoding="utf-8") as f:
            return time.time() - json.load(f).get("ts", 0)
    except Exception:
        return 1e9


def _write_manifest(data, etag):
    global _mem_manifest
    _mem_manifest = data
    security.secure_dir(META_DIR)
    security.secure_write(MANIFEST_CACHE, json.dumps({"etag": etag, "ts": time.time(), "data": data}))
    config.save_versions_cache(data)


def all_versions(force=False):
    if not force:
        return cached_versions()
    security.secure_dir(META_DIR)
    etag, old = None, None
    try:
        with open(MANIFEST_CACHE, encoding="utf-8") as f:
            j = json.load(f)
            etag, old = j.get("etag"), j.get("data")
    except Exception:
        pass
    changed, m, new_etag = net.get_conditional(MANIFEST, etag)
    if not changed:
        if old:
            _write_manifest(old, etag)
            return old
        changed, m, new_etag = net.get_conditional(MANIFEST, None)
    latest = m.get("latest", {})
    out = [{"id": v["id"], "type": v["type"], "url": v["url"],
            "releaseTime": v.get("releaseTime", "")[:10],
            "latest": v["id"] == latest.get("release"),
            "latest_snapshot": v["id"] == latest.get("snapshot")}
           for v in m["versions"]]
    _write_manifest(out, new_etag)
    return out


def version_json(mc):
    if mc in _vercache:
        return _vercache[mc]
    p = os.path.join(META_DIR, "versions", mc + ".json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                v = json.load(f)
            _vercache[mc] = v
            return v
        except Exception:
            pass
    entry = next((x for x in cached_versions() if x["id"] == mc), None)
    if not entry:
        entry = next((x for x in all_versions(force=True) if x["id"] == mc), None)
    if not entry:
        raise RuntimeError(f"Версия {mc} не найдена в манифесте Mojang")
    v = net.get_json(entry["url"])
    security.secure_write(p, json.dumps(v))
    _vercache[mc] = v
    return v


def rule_ok(rules, features=None):
    if not rules:
        return True
    features = features or {}
    allowed = False
    for r in rules:
        match = True
        o = r.get("os")
        if o:
            if "name" in o and o["name"] != OS:
                match = False
            if "arch" in o:
                a = {"x86_64": "x64", "amd64": "x64", "i386": "x86", "i686": "x86",
                     "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), "x64")
                if o["arch"] != a:
                    match = False
            if "version" in o:
                try:
                    if not re.search(o["version"], platform.version()):
                        match = False
                except re.error:
                    pass
        if "features" in r:
            for k, v in r["features"].items():
                if features.get(k, False) != v:
                    match = False
        if match:
            allowed = r.get("action") == "allow"
    return allowed


def maven_path(name):
    g, a, v, *rest = name.split(":")
    ext = rest[0] if rest else "jar"
    return f"{g.replace('.', '/')}/{a}/{v}/{a}-{v}.{ext}"


def offline_uuid(nick):
    d = bytearray(hashlib.md5(("OfflinePlayer:" + nick).encode()).digest())
    d[6] = (d[6] & 0x0F) | 0x30
    d[8] = (d[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(d)))


def _find_java(root):
    name = "java.exe" if OS == "windows" else "java"
    for dp, _, files in os.walk(root):
        if name in files and os.path.basename(dp) == "bin":
            return os.path.join(dp, name)
    return None


def _java_major_of(exe):
    try:
        r = subprocess.run([exe, "-version"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=15)
        m = re.search(r'version "([^"]+)"', r.stdout.decode("utf-8", "replace"))
        if not m:
            return None
        v = m.group(1)
        if v.startswith("1."):
            return int(v.split(".")[1])
        return int(re.split(r"[.\-+]", v)[0])
    except Exception:
        return None


def _system_java(major):
    cands = []
    w = shutil.which("java")
    if w:
        cands.append(os.path.realpath(w))
    for base in ("/usr/lib/jvm", "/usr/java", "/opt"):
        if os.path.isdir(base):
            for n in sorted(os.listdir(base)):
                p = os.path.join(base, n, "bin", "java")
                if os.path.exists(p):
                    cands.append(p)
    for exe in cands:
        if _java_major_of(exe) == major:
            return exe
    return None


def _java_platforms():
    m = platform.machine().lower()
    if OS == "linux":
        return ["linux"] if ARCH_BITS == "64" else ["linux-i386", "linux"]
    if OS == "osx":
        return ["mac-os-arm64", "mac-os"] if m in ("arm64", "aarch64") else ["mac-os"]
    if m in ("arm64", "aarch64"):
        return ["windows-arm64", "windows-x64"]
    return ["windows-x64", "windows-x86"] if ARCH_BITS == "64" else ["windows-x86", "windows-x64"]


def _pick_runtime(allj, component, major):
    for key in _java_platforms():
        node = allj.get(key) or {}
        if component and component in node and node[component]:
            return component, node[component][0]
        for name, arr in node.items():
            if not arr:
                continue
            vname = (arr[0].get("version") or {}).get("name", "")
            head = vname.split(".")[0]
            if head == str(major) or (major == 8 and vname.startswith("1.8")):
                return name, arr[0]
    return None


def _mojang_java(component, major, settings, progress, status):
    root = os.path.join(config.SHARED_DIR, "java", "mojang-" + (component or str(major)))
    exe = _find_java(root)
    if exe:
        if progress:
            progress("java", 1, 1, f"Java {major} · кэш")
        return exe
    if status:
        status(f"Java {major}: рантайм Mojang…")
    allj = net.get_json(JAVA_MANIFEST)
    picked = _pick_runtime(allj, component, major)
    if not picked:
        return None
    name, entry = picked
    man = net.get_json(entry["manifest"]["url"])
    files = man.get("files", {})
    tasks = []
    for path, info in files.items():
        try:
            dest = security.safe_join(root, path)
        except ValueError:
            continue
        t = info.get("type")
        if t == "directory":
            os.makedirs(dest, exist_ok=True)
        elif t == "link":
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tgt = info.get("target")
            if tgt and not os.path.lexists(dest):
                try:
                    os.symlink(tgt, dest)
                except OSError:
                    pass
        elif t == "file":
            tasks.append((info, dest))
    blobs = os.path.join(config.SHARED_DIR, "java", ".blobs")
    verify = settings.get("verify_files", False)

    def work(item):
        info, dest = item
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        d = info.get("downloads", {})
        if "lzma" in d:
            a = d["lzma"]
            blob = os.path.join(blobs, a["sha1"] + ".lzma")
            net.download(a["url"], blob, a["sha1"], a.get("size"), verify=verify)
            if not (os.path.exists(dest) and os.path.getsize(dest) > 0):
                with lzma.open(blob) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        elif "raw" in d:
            a = d["raw"]
            net.download(a["url"], dest, a.get("sha1"), a.get("size"), verify=verify)
        else:
            return
        if info.get("executable") and OS != "windows":
            try:
                os.chmod(dest, 0o755)
            except OSError:
                pass

    net.parallel(tasks, work, settings.get("threads", 16),
                 lambda d, t: progress and progress("java", d, t, f"Java {major}"))
    exe = _find_java(root)
    if exe and OS != "windows":
        try:
            os.chmod(exe, 0o755)
        except OSError:
            pass
    return exe


def _adoptium_java(major, settings, progress, status):
    root = os.path.join(config.SHARED_DIR, "java", "temurin-" + str(major))
    exe = _find_java(root)
    if exe:
        return exe
    aos = {"linux": "linux", "windows": "windows", "osx": "mac"}[OS]
    arch = "aarch64" if platform.machine().lower() in ("aarch64", "arm64") else "x64"
    url = (f"https://api.adoptium.net/v3/assets/latest/{major}/hotspot"
           f"?architecture={arch}&image_type=jre&os={aos}&vendor=eclipse")
    try:
        data = net.get_json(url)
    except Exception:
        data = []
    if not data:
        return None
    if status:
        status(f"Java {major}: Temurin…")
    pkg = data[0]["binary"]["package"]
    archive = os.path.join(config.SHARED_DIR, "java", pkg["name"])
    net.download(pkg["link"], archive, pkg.get("checksum"), pkg.get("size"),
                 lambda d, t: progress and progress("java", d, t, f"Java {major}"),
                 settings.get("verify_files", False))
    os.makedirs(root, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            security.safe_extract_zip(z, root)
    else:
        with tarfile.open(archive, "r:gz") as t:
            security.safe_extract_tar(t, root)
    os.remove(archive)
    exe = _find_java(root)
    if exe and OS != "windows":
        for f in os.listdir(os.path.dirname(exe)):
            try:
                os.chmod(os.path.join(os.path.dirname(exe), f), 0o755)
            except OSError:
                pass
    return exe


def ensure_java(major, settings, progress=None, status=None, component=None):
    if settings.get("java_path"):
        return settings["java_path"]
    key = (major, component)
    if key in _java_cache:
        return _java_cache[key]
    errors = []
    for label, fn in (("Mojang", lambda: _mojang_java(component, major, settings, progress, status)),
                      ("Adoptium", lambda: _adoptium_java(major, settings, progress, status)),
                      ("системная", lambda: _system_java(major))):
        try:
            exe = fn()
        except Exception as e:
            errors.append(f"{label}: {security.redact(e)}")
            continue
        if exe:
            _java_cache[key] = exe
            if progress:
                progress("java", 1, 1, f"Java {major}")
            return exe
        errors.append(f"{label}: не найдено")
    raise RuntimeError(f"Не удалось получить Java {major}. " + " | ".join(errors))


def java_for_mc(mc, settings, progress=None, status=None):
    jv = version_json(mc).get("javaVersion", {})
    return ensure_java(jv.get("majorVersion", 8), settings, progress, status, jv.get("component"))


def _lib_target(lib, libs_dir):
    d = lib.get("downloads", {})
    art = d.get("artifact")
    if art:
        return art["url"], security.safe_join(libs_dir, art["path"]), art.get("sha1"), art.get("size")
    if lib.get("name"):
        rel = maven_path(lib["name"])
        base = lib.get("url", "https://libraries.minecraft.net/")
        return base.rstrip("/") + "/" + rel, security.safe_join(libs_dir, rel), None, None
    return None


def install_libraries(libs, libs_dir, settings, progress=None, stage="libs"):
    tasks, cp, natives = {}, [], []
    for lib in libs:
        if not rule_ok(lib.get("rules")):
            continue
        t = _lib_target(lib, libs_dir)
        if not t:
            continue
        url, path, sha, size = t
        tasks[path] = (url, path, sha, size)
        cp.append(path)
        key = lib.get("natives", {}).get(OS)
        if key:
            key = key.replace("${arch}", ARCH_BITS)
            cls = lib.get("downloads", {}).get("classifiers", {}).get(key)
            if cls:
                p = os.path.join(libs_dir, cls["path"])
                tasks[p] = (cls["url"], p, cls.get("sha1"), cls.get("size"))
                natives.append((p, lib.get("extract", {}).get("exclude", [])))
    v = settings.get("verify_files", False)
    net.parallel(tasks.values(), lambda t: net.download(t[0], t[1], t[2], t[3], verify=v),
                 settings.get("threads", 16),
                 lambda d, t: progress and progress(stage, d, t, "Библиотеки"))
    return cp, natives


def extract_natives(natives, natives_dir):
    security.secure_dir(natives_dir)
    for path, excludes in natives:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                n = info.filename
                if n.endswith("/") or any(n.startswith(e) for e in excludes):
                    continue
                if n.startswith("META-INF") or n.endswith((".sha1", ".git")):
                    continue
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    continue
                try:
                    dest = security.safe_join(natives_dir, n)
                except ValueError:
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)


def install_base(mc, settings, progress=None, status=None):
    if status:
        status("Метаданные " + mc + "…")
    ver = version_json(mc)
    shared = config.SHARED_DIR
    libs_dir = os.path.join(shared, "libraries")
    natives_dir = os.path.join(config.VERSIONS_DIR, "_natives", mc)

    java = java_for_mc(mc, settings, progress, status)
    cp, natives = install_libraries(ver.get("libraries", []), libs_dir, settings, progress, "libs")
    extract_natives(natives, natives_dir)

    client = ver["downloads"]["client"]
    jar = os.path.join(shared, "clients", mc + ".jar")
    net.download(client["url"], jar, client.get("sha1"), client.get("size"),
                 lambda d, t: progress and progress("client", d, t, "Клиент"),
                 settings.get("verify_files", False))

    ai = ver.get("assetIndex")
    asset_id, assets_root = "legacy", os.path.join(shared, "assets")
    if ai:
        idx = os.path.join(shared, "assets", "indexes", ai["id"] + ".json")
        net.download(ai["url"], idx, ai.get("sha1"), ai.get("size"),
                     verify=settings.get("verify_files", False))
        with open(idx, encoding="utf-8") as f:
            index = json.load(f)
        objects = index.get("objects", {})
        virtual = index.get("virtual") or ai["id"] in ("legacy", "pre-1.6")
        vroot = os.path.join(shared, "assets", "virtual", ai["id"])

        def work(item):
            name, obj = item
            h = obj["hash"]
            p = os.path.join(shared, "assets", "objects", h[:2], h)
            net.download("https://resources.download.minecraft.net/" + h[:2] + "/" + h,
                         p, h, obj.get("size"), verify=settings.get("verify_files", False))
            if virtual:
                vp = security.safe_join(vroot, name)
                if not os.path.exists(vp):
                    os.makedirs(os.path.dirname(vp), exist_ok=True)
                    shutil.copyfile(p, vp)

        net.parallel(objects.items(), work, settings.get("threads", 16),
                     lambda d, t: progress and progress("assets", d, t, "Ассеты"))
        asset_id = ai["id"]
        assets_root = vroot if virtual else os.path.join(shared, "assets")

    log_arg = None
    log = ver.get("logging", {}).get("client")
    if log:
        lp = os.path.join(shared, "log_configs", log["file"]["id"])
        net.download(log["file"]["url"], lp, log["file"].get("sha1"), log["file"].get("size"),
                     verify=settings.get("verify_files", False))
        log_arg = log["argument"].replace("${path}", lp)

    return {"ver": ver, "java": java, "cp": cp, "jar": jar, "natives": natives_dir,
            "asset_id": asset_id, "assets_root": assets_root, "log_arg": log_arg}


def _profile_for(inst):
    p = os.path.join(config.instance_dir(inst["id"]), "launch_profile.json")
    if inst.get("loader") and os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def build_command(inst, settings, progress=None, status=None):
    base = install_base(inst["mc"], settings, progress, status)
    inst_path = config.instance_dir(inst["id"])
    security.secure_dir(inst_path)
    ver, profile = base["ver"], _profile_for(inst)
    extra_cp, extra_jvm, extra_game, main_class = [], [], [], ver["mainClass"]
    if profile:
        extra_cp = profile.get("classpath", [])
        extra_jvm = profile.get("jvm", [])
        extra_game = profile.get("game", [])
        main_class = profile.get("mainClass", main_class)
    values = {
        "natives_directory": base["natives"], "launcher_name": "Void", "launcher_version": "7.0",
        "classpath": os.pathsep.join(dict.fromkeys(extra_cp + base["cp"] + [base["jar"]])),
        "auth_player_name": settings["active_account"], "version_name": inst["mc"],
        "game_directory": inst_path, "assets_root": base["assets_root"],
        "assets_index_name": base["asset_id"], "auth_uuid": offline_uuid(settings["active_account"]),
        "auth_access_token": "0", "auth_xuid": "0", "clientid": "0", "user_type": "legacy",
        "version_type": ver.get("type", "release"), "user_properties": "{}",
        "resolution_width": "1280", "resolution_height": "720"}

    def sub(s):
        for k, v in values.items():
            s = s.replace("${" + k + "}", str(v))
        return s

    def expand(args):
        out = []
        for a in args:
            if isinstance(a, str):
                out.append(sub(a))
            elif isinstance(a, dict) and rule_ok(a.get("rules")):
                v = a.get("value", [])
                if isinstance(v, str):
                    out.append(sub(v))
                else:
                    out.extend(sub(x) for x in v)
        return out

    jvm = [f"-Xmx{int(settings['ram_mb'])}M", "-Xms512M"]
    if settings.get("java_args"):
        jvm += settings["java_args"].split()
    jvm.append("-Dorg.lwjgl.opengl.Display.allowSoftwareOpenGL=true")
    jvm.append("-Dfml.ignoreInvalidMinecraftCertificates=true")
    if "arguments" in ver:
        jvm += expand(ver["arguments"].get("jvm", []))
        game_args = expand(ver["arguments"].get("game", []))
    else:
        jvm += ["-Djava.library.path=" + base["natives"], "-cp", values["classpath"]]
        game_args = [sub(a) for a in ver["minecraftArguments"].split()]
    jvm += [sub(a) for a in extra_jvm]
    game_args += [sub(a) for a in extra_game]
    if base["log_arg"]:
        jvm.append(base["log_arg"])
    return [base["java"]] + jvm + [main_class] + game_args, inst_path, ver


def launch(inst, settings, progress, status, toast, net=True):
    from core import sandbox, gpu
    cmd, cwd, ver = build_command(inst, settings, progress, status)
    
    # Создаём окружение СРАЗУ
    env = game_env(settings)
    
    # GPU диагностика и настройка
    gpu.log_gpu_info()
    env = gpu.setup_gpu_env(env, force_nvidia=settings.get("force_nvidia", False))
    
    # Песочница
    if settings.get("sandbox", True) and sandbox.applies():
        cmd, env = sandbox.wrap(cmd, cwd, settings, env, net=net)
        status("Песочница активна")
    
    status("Запускаю Minecraft " + inst["mc"] +
           (" на NVIDIA" if settings.get("prime_run") else "") + "…")
    LOG.info("запуск: %s (sandbox=%s)", cmd[0], settings.get("sandbox", True))
    flags = 0x08000000 if OS == "windows" else 0
    return subprocess.Popen(cmd, cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace", creationflags=flags)


def doctor(mc, settings, progress=None, status=None):
    def p(stage, d, t, label=""):
        if t and (d == t or d % max(1, t // 20) == 0):
            print(f"  {stage:8} {d}/{t} {label}", flush=True)
    def s(t):
        print("· " + t, flush=True)
    cmd, cwd, ver = build_command({"id": "_doctor", "mc": mc, "loader": None}, settings, p, s)
    print("\nOK:", mc, "готова")
    print("Java:", cmd[0], "→", _java_major_of(cmd[0]))
    print("mainClass:", ver["mainClass"])
    return cmd