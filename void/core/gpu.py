"""Детект GPU, диагностика, принудительный выбор видеокарты."""
import logging
import os
import subprocess

LOG = logging.getLogger("void.gpu")


def detect_gpu():
    """Возвращает информацию о GPU через glxinfo."""
    try:
        r = subprocess.run(["glxinfo"], capture_output=True, text=True, timeout=5)
        if "OpenGL renderer" in r.stdout:
            renderer = [l for l in r.stdout.split("\n") if "OpenGL renderer" in l][0]
            vendor = [l for l in r.stdout.split("\n") if "OpenGL vendor" in l]
            vendor = vendor[0] if vendor else "unknown"
            LOG.info("GPU: " + renderer)
            LOG.info("Vendor: " + vendor)
            return {"renderer": renderer, "vendor": vendor, "software": "llvmpipe" in renderer.lower()}
    except Exception as e:
        LOG.debug("glxinfo недоступен: " + str(e))
    return {"renderer": "unknown", "vendor": "unknown", "software": False}


def nvidia_available():
    """Проверяет, есть ли NVIDIA GPU."""
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def setup_gpu_env(env, force_nvidia=False):
    """Настраивает окружение для GPU. Если force_nvidia=True — принудительно NVIDIA."""
    env = dict(env)
    
    # Если уже есть NVIDIA-переменные (от prime-run) — не трогаем
    if "__NV_PRIME_RENDER_OFFLOAD" in env:
        LOG.info("GPU: уже настроен через prime-run")
        return env
    
    # Если принудительно NVIDIA
    if force_nvidia and nvidia_available():
        LOG.info("GPU: принудительно включаем NVIDIA")
        env["__NV_PRIME_RENDER_OFFLOAD"] = "1"
        env["__GLX_VENDOR_LIBRARY_NAME"] = "nvidia"
        env["__VK_LAYER_NV_optimus"] = "NVIDIA_only"
        return env
    
    # Автодетект
    info = detect_gpu()
    if info["software"]:
        LOG.warning("GPU: обнаружен программный рендер (llvmpipe)")
        if nvidia_available():
            LOG.info("GPU: переключаемся на NVIDIA")
            env["__NV_PRIME_RENDER_OFFLOAD"] = "1"
            env["__GLX_VENDOR_LIBRARY_NAME"] = "nvidia"
            env["__VK_LAYER_NV_optimus"] = "NVIDIA_only"
    
    return env


def log_gpu_info():
    """Логирует информацию о GPU для диагностики."""
    info = detect_gpu()
    LOG.info("=" * 60)
    LOG.info("GPU DIAGNOSTICS")
    LOG.info("Renderer: " + info["renderer"])
    LOG.info("Vendor: " + info["vendor"])
    LOG.info("Software render: " + str(info["software"]))
    LOG.info("NVIDIA available: " + str(nvidia_available()))
    LOG.info("GPU env vars:")
    for k in ["__NV_PRIME_RENDER_OFFLOAD", "__GLX_VENDOR_LIBRARY_NAME", 
              "__VK_LAYER_NV_optimus", "MESA_LOADER_DRIVER_OVERRIDE"]:
        if k in os.environ:
            LOG.info(f"  {k}={os.environ[k]}")
    LOG.info("=" * 60)