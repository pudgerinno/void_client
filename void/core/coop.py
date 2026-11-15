"""Опциональный туннель-мод (для 1.18+)."""
import json, os, re, urllib.parse

from core import config, net, mojang, loaders

MODRINTH = "https://api.modrinth.com/v2"
MANAGED = ("e4all-", "fabric-api-", "e4mc-")


def e4all_supported(mc):
    try:
        return tuple(int(x) for x in mc.split(".")[:2]) >= (1, 18)
    except Exception:
        return False


def _pick(v):
    fs = [f for f in v.get("files", []) if "android" not in f["filename"].lower()]
    return next((f for f in fs if f.get("primary")), fs[0] if fs else None)


def _mod(slug, mc, loader):
    q = urllib.parse.urlencode({"loaders": json.dumps([loader]),
                                "game_versions": json.dumps([mc])})
    try:
        vs = net.get_json(f"{MODRINTH}/project/{slug}/version?{q}")
    except Exception:
        return None, None
    for v in vs:
        f = _pick(v)
        if f:
            return v, f
    return None, None


def _dl(f, mods, settings, progress, label):
    net.download(f["url"], os.path.join(mods, f["filename"]),
                 f.get("hashes", {}).get("sha1"), f.get("size"),
                 lambda d, t: progress("coop", d, t, label), settings.get("verify_files", False))


def install_coop(inst, settings, progress, status, toast):
    mc = inst["mc"]
    mods = os.path.join(config.instance_dir(inst["id"]), "mods")
    os.makedirs(mods, exist_ok=True)
    for n in os.listdir(mods):
        if n.startswith(MANAGED) and n.endswith(".jar"):
            os.remove(os.path.join(mods, n))
    inst["loader"] = "fabric"
    loaders.install_loader(inst, settings, progress, status)
    v, f = _mod("e4all", mc, "fabric")
    if not f:
        raise RuntimeError("e4all не найден для " + mc)
    _dl(f, mods, settings, progress, "e4all")
    for dep in v.get("dependencies", []):
        if dep.get("dependency_type") == "required" and dep.get("project_id"):
            dv, df = _mod(dep["project_id"], mc, "fabric")
            if df:
                _dl(df, mods, settings, progress, "зависимость")
    sync_instance(inst, settings)
    return "cold"


def sync_instance(inst, settings):
    if inst.get("type") != "coop":
        return
    path = config.instance_dir(inst["id"])
    if inst.get("loader") == "fabric":
        wanted = {"offlineMode": "false" if settings.get("online_mode") else "true",
                  "useWhiteList": "true" if settings.get("whitelist") else "false",
                  "preventPauseWhileHosting": "true"}
        for p in (os.path.join(path, "config", "e4all", "e4all.toml"),
                  os.path.join(path, "config", "e4all.toml")):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            text = ""
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    text = f.read()
            for k, val in wanted.items():
                if re.search(rf"(?m)^\s*{k}\s*=", text):
                    text = re.sub(rf"(?m)^(\s*{k}\s*=\s*).*$", rf"\g<1>{val}", text)
                else:
                    text += f"\n{k} = {val}\n"
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
    entries = [{"uuid": mojang.offline_uuid(n), "name": n} for n in settings.get("friends", [])]
    with open(os.path.join(path, "whitelist.json"), "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)