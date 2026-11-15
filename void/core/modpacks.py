"""Modrinth / CurseForge / импорт. Безопасная распаковка."""
import json, os, re, shutil, tempfile, time, uuid, zipfile

from core import config, net, loaders, security

MODRINTH = "https://api.modrinth.com/v2"
CF = "https://api.curseforge.com/v1"


def _new_instance(name, mc, loader, loader_version, source):
    base = re.sub(r"[^a-z0-9]+", "-", (name or "pack").lower()).strip("-") or "pack"
    iid = f"{base}-{uuid.uuid4().hex[:6]}"
    inst = {"id": iid, "name": name or "Modpack", "mc": mc, "type": "modpack",
            "loader": loader, "loader_version": loader_version, "source": source,
            "created": time.time(), "last_played": 0, "playtime": 0, "icon": ""}
    security.secure_dir(config.instance_dir(iid))
    config.save_instance(inst)
    return inst


def _loader_from_deps(deps):
    for key, kind in (("fabric-loader", "fabric"), ("quilt-loader", "quilt"),
                      ("forge", "forge"), ("neoforge", "neoforge")):
        if key in deps:
            v = deps[key]
            if kind == "forge" and v.startswith("1.") and "-" in v:
                v = v.split("-", 1)[1]
            return kind, v
    return None, None


def modrinth_search(query="", sort="relevance", offset=0):
    data = net.get_json(MODRINTH + "/search", params={
        "query": query or "", "facets": json.dumps([["project_type:modpack"]]),
        "limit": 24, "offset": offset, "index": sort})
    return [{"source": "modrinth", "id": p["project_id"], "name": p["title"],
             "author": p["author"], "desc": p["description"],
             "icon": p.get("icon_url"), "downloads": p["downloads"],
             "tags": p.get("categories", [])[:4]} for p in data["hits"]]


def curseforge_search(query="", sort="relevance", key="", offset=0):
    headers = {"Accept": "application/json"}
    if key.strip():
        headers["x-api-key"] = key.strip()
        data = net.get_json(CF + "/mods/search", headers=headers, params={
            "gameId": 432, "classId": 4471, "searchFilter": query or "",
            "sortField": 2 if sort == "relevance" else 3, "sortOrder": "desc",
            "pageSize": 24, "index": offset})
    else:
        data = net.get_json("https://www.curseforge.com/api/v1/mods/search", params={
            "gameId": 432, "classId": 4471, "searchFilter": query or "",
            "pageSize": 20, "index": offset})
    return [{"source": "curseforge", "id": str(h["id"]), "name": h["name"],
             "author": (h.get("authors") or [{}])[0].get("name", ""),
             "desc": h["summary"], "icon": (h.get("logo") or {}).get("thumbnailUrl"),
             "downloads": h.get("downloadCount", 0),
             "tags": [c["name"] for c in h.get("categories", [])[:4]]}
            for h in data.get("data", [])]


def _cf_headers(settings):
    key = (settings.get("cf_key") or "").strip()
    if not key:
        raise RuntimeError("Для CurseForge нужен API-ключ (Настройки)")
    return {"x-api-key": key, "Accept": "application/json"}


def _cf_url(f):
    if f.get("downloadUrl"):
        return f["downloadUrl"]
    fid = int(f["id"])
    return f"https://edge.forgecdn.net/files/{fid // 1000}/{fid % 1000}/{f['fileName']}"


def modrinth_install(project_id, name, settings, progress, status):
    status("Modrinth…")
    proj = net.get_json(f"{MODRINTH}/project/{project_id}")
    vs = net.get_json(f"{MODRINTH}/project/{project_id}/version")
    v = f = None
    for x in vs:
        cand = next((y for y in x["files"] if y["filename"].endswith(".mrpack")), None)
        if cand:
            v, f = x, cand
            break
    if not f:
        raise RuntimeError("У сборки нет .mrpack")
    path = os.path.join(tempfile.mkdtemp(prefix="void-pack-"), f["filename"])
    net.download(f["url"], path, f.get("hashes", {}).get("sha1"), f.get("size"),
                 lambda d, t: progress("pack", d, t, "Сборка"), settings.get("verify_files", False))
    return _install_mrpack(path, name or proj["title"], "modrinth", settings, progress, status)


def _install_mrpack(path, name, source, settings, progress, status):
    with zipfile.ZipFile(path) as z:
        index = json.loads(z.read("modrinth.index.json"))
        deps = index.get("dependencies", {})
        mc = deps["minecraft"]
        loader, lv = _loader_from_deps(deps)
        inst = _new_instance(name or index.get("name"), mc, loader, lv, source)
        root = config.instance_dir(inst["id"])
        try:
            files = [f for f in index.get("files", [])
                     if f.get("env", {}).get("client", "required") != "unsupported"]

            def work(f):
                dest = security.safe_join(root, f["path"])
                h = f.get("hashes", {}).get("sha1") or f.get("hashes", {}).get("sha512")
                net.download(f["downloads"][0], dest, h, f.get("fileSize"),
                             verify=settings.get("verify_files", False))
            net.parallel(files, work, settings.get("threads", 16),
                         lambda d, t: progress("files", d, t, "Файлы сборки"))
            for prefix in ("overrides/", "client-overrides/"):
                security.safe_extract_zip(z, root, prefix, strip=1)
            if loader:
                status("Лоадер " + loader + "…")
                loaders.install_loader(inst, settings, progress, status)
            config.save_instance(inst)
            return inst
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise


def curseforge_install(project_id, file_id, name, settings, progress, status):
    h = _cf_headers(settings)
    status("CurseForge…")
    mod = net.get_json(f"{CF}/mods/{project_id}", headers=h)["data"]
    if file_id:
        f = net.get_json(f"{CF}/mods/{project_id}/files/{file_id}", headers=h)["data"]
    else:
        files = net.get_json(f"{CF}/mods/{project_id}/files", headers=h,
                             params={"pageSize": 50})["data"]
        f = next((x for x in files if x.get("isAvailable")
                  and x.get("fileName", "").endswith(".zip")), None)
        if not f:
            raise RuntimeError("Не найден файл сборки")
    path = os.path.join(tempfile.mkdtemp(prefix="void-pack-"), f["fileName"])
    net.download(_cf_url(f), path,
                 next((x["value"] for x in f.get("hashes", []) if x.get("algo") == 1), None),
                 f.get("fileLength"), lambda d, t: progress("pack", d, t, "Сборка"),
                 settings.get("verify_files", False))
    return _install_cfzip(path, name or mod["name"], "curseforge", settings, progress, status)


def _install_cfzip(path, name, source, settings, progress, status):
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("manifest.json"))
    mc = manifest["minecraft"]["version"]
    loader = lv = None
    for ml in manifest["minecraft"].get("modLoaders", []):
        mid = ml["id"]
        for pre, kind in (("forge-", "forge"), ("neoforge-", "neoforge"),
                          ("fabric-loader-", "fabric"), ("fabric-", "fabric"), ("quilt-", "quilt")):
            if mid.startswith(pre):
                loader, lv = kind, mid[len(pre):]
                break
        if ml.get("primary"):
            break
    inst = _new_instance(name, mc, loader, lv, source)
    root = config.instance_dir(inst["id"])
    try:
        h = _cf_headers(settings)

        def fetch(f):
            d = net.get_json(f"{CF}/mods/{f['projectID']}/files/{f['fileID']}", headers=h)["data"]
            return {"url": _cf_url(d), "name": d["fileName"],
                    "hash": next((x["value"] for x in d.get("hashes", []) if x.get("algo") == 1), None),
                    "size": d.get("fileLength")}
        meta = net.parallel(manifest.get("files", []), fetch, settings.get("threads", 8),
                            lambda d, t: progress("meta", d, t, "Метаданные"))

        def work(m):
            dest = security.safe_join(root, "mods/" + m["name"])
            net.download(m["url"], dest, m["hash"], m["size"],
                         verify=settings.get("verify_files", False))
        net.parallel(meta, work, settings.get("threads", 16),
                     lambda d, t: progress("files", d, t, "Файлы сборки"))
        with zipfile.ZipFile(path) as z:
            security.safe_extract_zip(z, root, manifest.get("overrides", "overrides") + "/", strip=1)
        if loader:
            status("Лоадер " + loader + "…")
            loaders.install_loader(inst, settings, progress, status)
        config.save_instance(inst)
        return inst
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def import_pack(path, settings, progress, status):
    if not os.path.exists(path):
        raise RuntimeError("Файл не найден")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
    base = os.path.splitext(os.path.basename(path))[0]
    if "modrinth.index.json" in names:
        return _install_mrpack(path, base, "import", settings, progress, status)
    if "manifest.json" in names:
        return _install_cfzip(path, base, "import", settings, progress, status)
    raise RuntimeError("Это не .mrpack и не CurseForge zip")