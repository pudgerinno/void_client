"""Скачивание и установка скинов для офлайн-аккаунтов."""
import logging
import os
import shutil

from core import config, net, security

LOG = logging.getLogger("void.skins")


def download_skin(nick, dest_dir):
    """Скачивает скин с Minotar.net по нику."""
    url = f"https://minotar.net/download/{nick}"
    dest = os.path.join(dest_dir, f"{nick}.png")
    try:
        net.download(url, dest, verify=False)
        LOG.info(f"Скин скачан: {dest}")
        return dest
    except Exception as e:
        LOG.error(f"Не удалось скачать скин: {e}")
        raise


def install_skin_mod(inst, settings, progress, status):
    """Устанавливает мод CustomSkinLoader для загрузки кастомных скинов."""
    mc = inst["mc"]
    loader = inst.get("loader")
    
    if not loader:
        raise RuntimeError("Для скинов нужен лоадер (Fabric/Forge). Сначала создай сборку с лоадером.")
    
    status("Ищу CustomSkinLoader для " + mc + " + " + loader + "…")
    
    # Ищем мод на Modrinth
    from core import mods
    hits = mods.modrinth_search("CustomSkinLoader", mc, "relevance", 0)
    if not hits:
        raise RuntimeError("CustomSkinLoader не найден для этой версии")
    
    mod_id = hits[0]["id"]
    fname = mods.modrinth_install(mod_id, inst, settings, progress, status)
    status(f"Мод {fname} установлен")
    return fname


def set_active_skin(inst, skin_path):
    """Устанавливает активный скин для инстанса."""
    dest = os.path.join(config.instance_dir(inst["id"]), "skin.png")
    shutil.copy(skin_path, dest)
    os.chmod(dest, 0o600)
    LOG.info(f"Активный скин установлен: {dest}")
    return dest


def get_active_skin(inst):
    """Возвращает путь к активному скину, если есть."""
    dest = os.path.join(config.instance_dir(inst["id"]), "skin.png")
    if os.path.exists(dest):
        return dest
    return None