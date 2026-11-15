"""Песочница: изоляция файлов, сети и графики."""
import os, shutil, logging

from core import config

LOG = logging.getLogger("void.sandbox")


def available():
    return shutil.which("bwrap") is not None


def applies():
    return available() and os.name == "posix"


def _bind(cmd, src, dst=None, ro=False):
    if src and os.path.exists(src):
        cmd += ["--ro-bind" if ro else "--bind", src, dst or src]
        LOG.debug(f"sandbox bind: {src} -> {dst or src} (ro={ro})")


def _base(env):
    return ["bwrap", "--die-with-parent", "--new-session",
            "--unshare-pid", "--unshare-uts",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/dev/shm",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
            "--ro-bind", "/sys", "/sys",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64"]


def _jvm_bind(b, java_exe, shared):
    jvm = os.path.dirname(os.path.dirname(os.path.realpath(java_exe)))
    if not jvm.startswith(("/usr", shared)):
        _bind(b, jvm, ro=True)


def _gpu_bind(b):
    """Пробрасывает все GPU-устройства и библиотеки."""
    # GPU устройства
    for d in ("/dev/dri", "/dev/snd"):
        _bind(b, d)
    
    # NVIDIA устройства
    for n in sorted(os.listdir("/dev")):
        if n.startswith("nvidia") or n == "nvidiactl" or n == "nvidia-uvm":
            _bind(b, "/dev/" + n)
    
    # NVIDIA библиотеки (если есть)
    for lib_path in ["/usr/lib/nvidia", "/usr/lib64/nvidia", 
                     "/usr/lib/x86_64-linux-gnu/nvidia",
                     "/opt/nvidia", "/usr/local/nvidia"]:
        if os.path.exists(lib_path):
            _bind(b, lib_path, ro=True)
    
    # Mesa драйверы
    for mesa_path in ["/usr/lib/dri", "/usr/lib64/dri", 
                      "/usr/lib/x86_64-linux-gnu/dri"]:
        if os.path.exists(mesa_path):
            _bind(b, mesa_path, ro=True)


def wrap(cmd, inst_path, settings, env, net=True):
    """Песочница для игры."""
    if not available():
        raise RuntimeError("Песочница включена, но нет bwrap: sudo pacman -S bubblewrap")
    
    LOG.info("Песочница: запускаю игру в изоляции")
    shared = config.SHARED_DIR
    natives = os.path.join(config.VERSIONS_DIR, "_natives")
    b = _base(env)

    if not net:
        b.insert(1, "--unshare-net")

    # GPU
    _gpu_bind(b)

    env = dict(env)
    rt = env.get("XDG_RUNTIME_DIR")
    wl = env.get("WAYLAND_DISPLAY")

    if rt and wl:
        _bind(b, os.path.join(rt, wl))
        env["_JAVA_AWT_WM_NONREPARENTING"] = "1"
    elif env.get("DISPLAY"):
        if settings.get("allow_x11", True):
            _bind(b, "/tmp/.X11-unix", ro=True)
            if env.get("XAUTHORITY"):
                _bind(b, env["XAUTHORITY"], ro=True)
            env["_JAVA_AWT_WM_NONREPARENTING"] = "1"
        else:
            raise RuntimeError("X11-сессия, а allow_x11 выключен.")

    # PulseAudio/PipeWire
    if rt:
        for sock in ("pulse/native", "pipewire-0", "pipewire-0-manager"):
            p = os.path.join(rt, sock)
            if os.path.exists(p):
                _bind(b, p)

    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    env["HOME"] = inst_path

    _bind(b, inst_path)
    _bind(b, shared, ro=True)
    _bind(b, natives)
    _jvm_bind(b, cmd[0], shared)

    b += ["--setenv", "HOME", inst_path,
          "--setenv", "_JAVA_AWT_WM_NONREPARENTING", "1"]
    if rt:
        b += ["--setenv", "XDG_RUNTIME_DIR", rt]
    
    LOG.info("Песочница: команда готова")
    return b + cmd, env


def wrap_build(cmd, cwd, env):
    """Песочница для Forge-процессоров — более мягкая."""
    if not available():
        LOG.info("Песочница: bwrap не найден, запускаю без изоляции")
        return cmd, env
    
    LOG.info("Песочница: запускаю Forge-процессор в изоляции")
    shared = config.SHARED_DIR
    b = _base(env)
    env = dict(env)
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    env["HOME"] = cwd
    
    # Для Forge нужно больше доступа
    _bind(b, shared)
    _bind(b, cwd)
    _jvm_bind(b, cmd[0], shared)
    
    # Дополнительные пути для Forge
    for extra in ["/usr/lib/jvm", "/usr/java", "/opt/java"]:
        if os.path.exists(extra):
            _bind(b, extra, ro=True)
    
    b += ["--setenv", "HOME", cwd]
    LOG.info("Песочница: Forge-процессор готов")
    return b + cmd, env