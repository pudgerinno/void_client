"""Скачивание модов с Modrinth и CurseForge."""
import json, os, re, shutil, tempfile, time, uuid

from core import config, net, security

MODRINTH = "https://api.modrinth.com/v2"
CF = "https://api.curseforge.com/v1"


def modrinth_search(query="", game_version="", sort="relevance", offset=0):
    """Поиск модов на Modrinth."""
    facets = [["project_type:mod"]]
    if game_version:
        facets.append([f"versions:{game_version}"])
    data = net.get_json(MODRINTH + "/search", params={
        "query": query or "", "facets": json.dumps(facets),
        "limit": 24, "offset": offset, "index": sort})
    return [{"source": "modrinth", "id": p["project_id"], "slug": p["slug"],
             "name": p["title"], "author": p["author"], "desc": p["description"],
             "icon": p.get("icon_url"), "downloads": p["downloads"],
             "tags": p.get("categories", [])[:4],
             "versions": p.get("versions", [])[:5]} for p in data["hits"]]


def curseforge_search(query="", game_version="", sort="relevance", key="", offset=0):
    """Поиск модов на CurseForge."""
    headers = {"Accept": "application/json"}
    if key.strip():
        headers["x-api-key"] = key.strip()
    params = {"gameId": 432, "classId": 6, "searchFilter": query or "",
              "sortField": 2 if sort == "relevance" else 3, "sortOrder": "desc",
              "pageSize": 24, "index": offset}
    if game_version:
        params["gameVersion"] = game_version
    data = net.get_json(CF + "/mods/search", headers=headers, params=params)
    return [{"source": "curseforge", "id": str(h["id"]), "slug": h.get("slug"),
             "name": h["name"], "author": (h.get("authors") or [{}])[0].get("name", ""),
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


def modrinth_install(project_id, inst, settings, progress, status):
    """Установить мод с Modrinth в инстанс."""
    mc = inst["mc"]
    loader = inst.get("loader") or "fabric"
    status(f"Modrinth: ищу версию для {mc} + {loader}…")
    vs = net.get_json(f"{MODRINTH}/project/{project_id}/version")
    f = None
    for v in vs:
        if mc in v.get("game_versions", []) and loader in v.get("loaders", []):
            files = v.get("files", [])
            if files:
                f = files[0]
                break
    if not f:
        raise RuntimeError(f"Не найдена версия мода для {mc} + {loader}")
    mods = os.path.join(config.instance_dir(inst["id"]), "mods")
    os.makedirs(mods, exist_ok=True)
    dest = os.path.join(mods, f["filename"])
    net.download(f["url"], dest, f.get("hashes", {}).get("sha1"), f.get("size"),
                 lambda d, t: progress("mod", d, t, f["filename"]),
                 settings.get("verify_files", False))
    return f["filename"]


def curseforge_install(project_id, inst, settings, progress, status):
    """Установить мод с CurseForge в инстанс."""
    h = _cf_headers(settings)
    mc = inst["mc"]
    status(f"CurseForge: ищу версию для {mc}…")
    files = net.get_json(f"{CF}/mods/{project_id}/files", headers=h,
                         params={"gameVersion": mc, "pageSize": 50})["data"]
    f = next((x for x in files if x.get("isAvailable")
              and x.get("fileName", "").endswith(".jar")), None)
    if not f:
        raise RuntimeError(f"Не найдена версия мода для {mc}")
    mods = os.path.join(config.instance_dir(inst["id"]), "mods")
    os.makedirs(mods, exist_ok=True)
    dest = os.path.join(mods, f["fileName"])
    net.download(_cf_url(f), dest,
                 next((x["value"] for x in f.get("hashes", []) if x.get("algo") == 1), None),
                 f.get("fileLength"), lambda d, t: progress("mod", d, t, f["fileName"]),
                 settings.get("verify_files", False))
    return f["fileName"]