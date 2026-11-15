"""Fabric / Quilt / Forge / NeoForge с fallback зерками."""
import json, os, re, shutil, subprocess, tempfile, zipfile, logging

from core import config, net, mojang, security, sandbox

FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
MAVEN_RE = re.compile(r"^[A-Za-z0-9_.+\-]+(:[A-Za-z0-9_.+\-]+){2,3}$")

LOG = logging.getLogger("void.loaders")

# Fallback зеркала для России
FORGE_MIRRORS = [
    "https://maven.minecraftforge.net/",
    "https://maven.creeperhost.net/",  # альтернативное зеркало
]
NEOFORGE_MIRRORS = [
    "https://maven.neoforged.net/releases/",
]


def _maven_rel(name):
    name = str(name or "")
    if not MAVEN_RE.match(name) or ".." in name:
        raise ValueError("Недопустимые maven-координаты: " + name[:60])
    return mojang.maven_path(name)


def _save_profile(inst, profile):
    p = os.path.join(config.instance_dir(inst["id"]), "launch_profile.json")
    security.secure_write(p, json.dumps(profile, ensure_ascii=False, indent=2))


def _meta_install(kind, inst, settings, progress, status):
    base = FABRIC_META if kind == "fabric" else QUILT_META
    mc = security.safe_token(inst["mc"])
    version = security.safe_token(inst["loader_version"]) if inst.get("loader_version") else None
    if not version:
        rows = net.get_json(f"{base}/versions/loader/{mc}")
        if not rows:
            raise RuntimeError(f"{kind} не поддерживает {mc}")
        version = security.safe_token(next(
            (r["loader"]["version"] for r in rows if r["loader"].get("stable")),
            rows[0]["loader"]["version"]))
    prof = net.get_json(f"{base}/versions/loader/{mc}/{version}/profile/json")
    libs_dir = os.path.join(config.SHARED_DIR, "libraries")
    cp = []
    libs = prof.get("libraries", [])
    for i, lib in enumerate(libs):
        rel = _maven_rel(lib["name"])
        path = security.safe_join(libs_dir, rel)
        url = lib.get("url", "https://libraries.minecraft.net/").rstrip("/") + "/" + rel
        net.download(url, path, lib.get("sha1"), lib.get("size"),
                     verify=settings.get("verify_files", False))
        cp.append(path)
        progress("loader", i + 1, len(libs), f"{kind} {version}")
    _save_profile(inst, {
        "mainClass": prof["mainClass"], "classpath": cp,
        "jvm": [a for a in prof.get("arguments", {}).get("jvm", []) if isinstance(a, str)],
        "game": [a for a in prof.get("arguments", {}).get("game", []) if isinstance(a, str)]})
    inst["loader"], inst["loader_version"] = kind, version
    config.save_instance(inst)


def _forge_url(kind, mc, ver):
    mc = security.safe_token(mc)
    ver = security.safe_token(ver)
    if kind == "forge":
        full = ver if "-" in ver else f"{mc}-{ver}"
        rel = f"net/minecraftforge/forge/{full}/forge-{full}-installer.jar"
        # Пробуем зеркала
        for mirror in FORGE_MIRRORS:
            url = mirror + rel
            try:
                net.get_bytes(url, limit=1024)  # проверка доступности
                LOG.info(f"Forge: используем зеркало {mirror}")
                return url
            except Exception:
                continue
        # Если все зеркала недоступны, возвращаем первое (для ошибки)
        return FORGE_MIRRORS[0] + rel
    if mc == "1.20.1":
        full = f"{mc}-{ver}"
        return f"https://maven.neoforged.net/releases/net/neoforged/forge/{full}/forge-{full}-installer.jar"
    return f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{ver}/neoforge-{ver}-installer.jar"


def _manifest_main(jar):
    with zipfile.ZipFile(jar) as z:
        mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    for line in mf.splitlines():
        if line.lower().startswith("main-class:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("Main-Class не найден в " + os.path.basename(jar))


def _forge_install(kind, inst, settings, progress, status):
    mc = security.safe_token(inst["mc"])
    ver = inst.get("loader_version")
    if not ver:
        if kind != "forge":
            raise RuntimeError("Укажи версию " + kind)
        promos = net.get_json(
            "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json")["promos"]
        ver = promos.get(f"{mc}-recommended") or promos.get(f"{mc}-latest")
        if not ver:
            raise RuntimeError("Forge не найден для " + mc)
    ver = security.safe_token(ver)
    inst["loader_version"] = ver

    url = _forge_url(kind, mc, ver)
    installer = os.path.join(config.SHARED_DIR, "installers", os.path.basename(url))
    status(f"{kind} {ver}…")
    LOG.info(f"Скачиваю {kind} с {url}")
    net.download(url, installer, verify=settings.get("verify_files", False))
    libs_dir = os.path.join(config.SHARED_DIR, "libraries")
    java = mojang.java_for_mc(mc, settings, progress, status)
    use_sandbox = settings.get("sandbox", True) and sandbox.available()

    with zipfile.ZipFile(installer) as z:
        ip = json.loads(z.read("install_profile.json"))
        vjson = ip.get("version")
        if not isinstance(vjson, dict):
            vjson = json.loads(z.read(security.safe_join("/", ip["json"]).lstrip("/")))
        mojang.install_libraries(ip.get("libraries", []) + vjson.get("libraries", []),
                                 libs_dir, settings, progress, "loader")

        def maven_art(name):
            rel = _maven_rel(name)
            path = security.safe_join(libs_dir, rel)
            if not os.path.exists(path):
                # Пробуем зеркала
                for mirror in (FORGE_MIRRORS if kind == "forge" else NEOFORGE_MIRRORS):
                    try:
                        net.download(mirror + rel, path, verify=settings.get("verify_files", False))
                        return path
                    except Exception:
                        continue
                raise RuntimeError(f"Не удалось скачать {name} ни с одного зеркала")
            return path

        if not ip.get("processors") and ip.get("install", {}).get("filePath"):
            info = ip["install"]
            dest = security.safe_join(libs_dir, _maven_rel(info["path"]))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(info["filePath"]) as s, open(dest, "wb") as o:
                shutil.copyfileobj(s, o)
        else:
            tmp = tempfile.mkdtemp(prefix="void-forge-", dir=config.SHARED_DIR)
            try:
                client_jar = os.path.join(config.SHARED_DIR, "clients", mc + ".jar")
                if not os.path.exists(client_jar):
                    cj = mojang.version_json(mc)["downloads"]["client"]
                    net.download(cj["url"], client_jar, cj.get("sha1"), cj.get("size"))
                data = {"SIDE": "client", "ROOT": config.SHARED_DIR, "LIBRARY_DIR": libs_dir,
                        "INSTALLER": installer, "MINECRAFT_JAR": client_jar}
                for k, v in ip.get("data", {}).items():
                    val = v["client"] if isinstance(v, dict) else v
                    if val.startswith("[") and val.endswith("]"):
                        data[k] = maven_art(val[1:-1])
                    elif val.startswith("/"):
                        out = security.safe_join(tmp, val)
                        os.makedirs(os.path.dirname(out), exist_ok=True)
                        with open(out, "wb") as f:
                            f.write(z.read(val.lstrip("/")))
                        data[k] = out
                    else:
                        data[k] = val
                procs = [p for p in ip.get("processors", [])
                         if "client" in p.get("sides", ["client", "server"])]
                for i, p in enumerate(procs):
                    status(f"{kind}: обработка {i + 1}/{len(procs)}")
                    cp = [maven_art(p["jar"])] + [maven_art(c) for c in p.get("classpath", [])]
                    main = _manifest_main(cp[0])
                    args = []
                    for a in p.get("args", []):
                        if a.startswith("[") and a.endswith("]"):
                            args.append(maven_art(a[1:-1]))
                        else:
                            for k, val in data.items():
                                a = a.replace("{" + k + "}", str(val))
                            args.append(a)
                    cmd = [java, "-cp", os.pathsep.join(cp), main] + args
                    env = mojang.game_env(settings)
                    if use_sandbox:
                        cmd, env = sandbox.wrap_build(cmd, tmp, env)
                    r = subprocess.run(cmd, cwd=tmp, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
                    if r.returncode != 0:
                        raise RuntimeError("Forge-процессор упал:\n" +
                                           security.redact(r.stdout.decode("utf-8", "replace"))[-1500:])
                    progress("loader", i + 1, len(procs), kind)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    cp, natives = mojang.install_libraries(vjson.get("libraries", []), libs_dir,
                                           settings, progress, "loader")
    mojang.extract_natives(natives, os.path.join(config.VERSIONS_DIR, "_natives", f"{mc}-{kind}"))
    _save_profile(inst, {
        "mainClass": vjson["mainClass"], "classpath": cp,
        "jvm": [a for a in vjson.get("arguments", {}).get("jvm", []) if isinstance(a, str)],
        "game": [a for a in vjson.get("arguments", {}).get("game", []) if isinstance(a, str)]})
    inst["loader"], inst["loader_version"] = kind, ver
    config.save_instance(inst)


def install_loader(inst, settings, progress, status):
    kind = inst.get("loader")
    if not kind:
        return
    if kind in ("fabric", "quilt"):
        _meta_install(kind, inst, settings, progress, status)
    elif kind in ("forge", "neoforge"):
        _forge_install(kind, inst, settings, progress, status)
    else:
        raise RuntimeError("Неизвестный лоадер: " + str(kind))