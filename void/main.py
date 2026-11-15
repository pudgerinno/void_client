"""Void Launcher — финальная версия."""
import argparse, os, re, sys, time, uuid, math, random, shutil, logging, subprocess, threading, traceback
import urllib.request, json as jsonlib

try:
    from PySide6.QtCore import (Qt, QObject, Signal, QTimer, QPropertyAnimation, Property,
                                QEasingCurve, QSize, QPointF, QParallelAnimationGroup)
    from PySide6.QtGui import (QPainter, QColor, QLinearGradient, QRadialGradient, QPen,
                               QPixmap, QIcon, QPolygonF, QAction)
    from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGridLayout, QFormLayout, QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem,
        QSlider, QCheckBox, QComboBox, QFrame, QStackedWidget, QFileDialog, QInputDialog,
        QProgressBar, QGraphicsOpacityEffect, QScrollArea, QPlainTextEdit, QMessageBox, QDialog,
        QMenu)
except ImportError:
    print("Нужен PySide6:  sudo pacman -S python-pyside6")
    sys.exit(1)

try:
    from core import config, net, mojang, modpacks, mods, security, sandbox, voidlink, modscan, loaders, gpu, skins
except ImportError as e:
    print("Не хватает модуля в папке core/:", e)
    sys.exit(1)

voidlink.LOG = logging.getLogger("void.voidlink")
LOG = logging.getLogger("void")


def setup_logging():
    security.secure_dir(config.CONFIG_DIR)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.FileHandler(config.LOG_PATH, encoding="utf-8")
    sh = logging.StreamHandler()
    for h in (fh, sh):
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[fh, sh], force=True)
    try:
        os.chmod(config.LOG_PATH, 0o600)
    except OSError:
        pass


def fetch_loader_versions(mc, loader):
    try:
        if loader == "fabric":
            url = f"https://meta.fabricmc.net/v2/versions/loader/{mc}"
            with urllib.request.urlopen(url, timeout=15) as r:
                return [x["loader"]["version"] for x in jsonlib.load(r)]
        elif loader == "forge":
            url = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
            with urllib.request.urlopen(url, timeout=15) as r:
                data = jsonlib.load(r)
            return sorted({v for k, v in data.get("promos", {}).items()
                           if k.startswith(mc + "-")}, reverse=True)
        elif loader == "quilt":
            url = f"https://meta.quiltmc.org/v3/versions/loader/{mc}"
            with urllib.request.urlopen(url, timeout=15) as r:
                return [x["loader"]["version"] for x in jsonlib.load(r)]
    except Exception:
        pass
    return []


# ====================== виджеты ======================
class MCButton(QPushButton):
    def __init__(self, text, kind="normal"):
        super().__init__(text)
        self._kind = kind; self._glow = 0.0; self._pulse = 0.0; self._breath = False
        self.setCursor(Qt.PointingHandCursor); self.setMinimumHeight(38)
        self._anim = QPropertyAnimation(self, b"glow"); self._anim.setDuration(150)
        self._bt = QTimer(self); self._bt.timeout.connect(self._tick); self._bt.start(40)

    def setBreathing(self, on): self._breath = on; self.update()

    def _tick(self):
        if self._breath and self.isEnabled():
            w = self.window()
            if not (w and w.isMinimized()):
                self._pulse += 0.05; self.update()

    def _g(self): return self._glow
    def _s(self, v): self._glow = v; self.update()
    glow = Property(float, _g, _s)

    def enterEvent(self, e):
        if self.isEnabled():
            self._anim.stop(); self._anim.setEndValue(1.0); self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.stop(); self._anim.setEndValue(0.0); self._anim.start()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self); w, h = self.width(), self.height()
        on = self.isEnabled(); g = self._glow if on else 0.0
        if self._kind == "primary":
            br = 0.5 + 0.5 * math.sin(self._pulse) if (self._breath and on) else 0
            base = QColor(88 + int(30*g + 18*br), 52 + int(20*g), 150 + int(40*g + 30*br))
            light, dark = QColor("#a78bfa"), QColor("#2a1b52")
        elif self._kind == "danger":
            base = QColor(70 + int(30*g), 26, 40); light, dark = QColor("#f87171"), QColor("#3a1220")
        else:
            base = QColor(34 + int(22*g), 27 + int(18*g), 56 + int(30*g))
            light, dark = QColor("#5b4a8f"), QColor("#140f26")
        if not on:
            base, light, dark = QColor("#221c33"), QColor("#3a3350"), QColor("#151022")
        p.fillRect(self.rect(), base)
        p.setPen(QPen(dark, 2)); p.drawLine(0, h-1, w, h-1); p.drawLine(w-1, 0, w-1, h)
        p.setPen(QPen(light, 2)); p.drawLine(0, 0, w-2, 0); p.drawLine(0, 0, 0, h-2)
        if self.isDown():
            p.fillRect(self.rect(), QColor(0, 0, 0, 55))
        if g > 0:
            p.setPen(QPen(QColor(167, 139, 250, int(130*g)), 1)); p.drawRect(1, 1, w-3, h-3)
        p.setPen(QColor("#efeaff") if on else QColor("#6f6a8a"))
        f = p.font(); f.setBold(True); f.setPointSize(9); p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self.text())
        p.end()


class AnimPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("panel"); self.setAttribute(Qt.WA_StyledBackground, True)
        self._glow = 0.0
        self._anim = QPropertyAnimation(self, b"glow"); self._anim.setDuration(200)

    def _g(self): return self._glow
    def _s(self, v): self._glow = v; self.update()
    glow = Property(float, _g, _s)

    def enterEvent(self, e):
        self._anim.stop(); self._anim.setEndValue(1.0); self._anim.start(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.stop(); self._anim.setEndValue(0.0); self._anim.start(); super().leaveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._glow > 0.01:
            p = QPainter(self)
            p.setPen(QPen(QColor(139, 92, 246, int(150*self._glow)), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(1, 1, self.width()-3, self.height()-3)
            p.end()


class VoidLogo(QWidget):
    def __init__(self, size=74):
        super().__init__()
        self.setFixedSize(size + 26, size + 28)
        self.s = size; self.t = 0.0
        self._tm = QTimer(self); self._tm.timeout.connect(self._tick); self._tm.start(33)

    def _tick(self):
        w = self.window()
        if self.isVisible() and not (w and w.isMinimized()):
            self.t += 0.035; self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        s = self.s; cx = self.width() / 2
        cy = self.height() / 2 + math.sin(self.t * 1.25) * 5
        glow = 0.5 + 0.5 * math.sin(self.t * 1.7)
        rg = QRadialGradient(cx, cy + s * .35, s)
        rg.setColorAt(0, QColor(139, 92, 246, int(60 + 70 * glow)))
        rg.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen); p.setBrush(rg)
        p.drawEllipse(int(cx - s), int(cy - s * .3), int(s * 2), int(s * 1.5))
        P = QPointF
        top = QPolygonF([P(cx, cy - s*.55), P(cx + s*.5, cy - s*.28),
                         P(cx, cy), P(cx - s*.5, cy - s*.28)])
        left = QPolygonF([P(cx - s*.5, cy - s*.28), P(cx, cy),
                          P(cx, cy + s*.55), P(cx - s*.5, cy + s*.28)])
        right = QPolygonF([P(cx + s*.5, cy - s*.28), P(cx, cy),
                           P(cx, cy + s*.55), P(cx + s*.5, cy + s*.28)])
        g = QLinearGradient(cx - s*.5, cy - s*.5, cx + s*.5, cy)
        g.setColorAt(0, QColor("#a78bfa")); g.setColorAt(1, QColor("#7c3aed"))
        p.setBrush(g); p.setPen(QPen(QColor("#c4b5fd"), 1)); p.drawPolygon(top)
        p.setBrush(QColor("#1b1233")); p.setPen(QPen(QColor("#0d0819"), 1)); p.drawPolygon(left)
        p.setBrush(QColor("#2a1b4d")); p.drawPolygon(right)
        p.setPen(QPen(QColor(196, 181, 253, int(110 + 110 * glow)), 1))
        for i in range(3):
            y = cy + s * (.12 + i * .16)
            p.drawLine(int(cx - s*.42), int(y), int(cx - s*.08), int(y + s*.16))
        p.end()


class VoidCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.stars = [(random.random(), random.random(), random.random()*1.4+.4,
                       random.random()*6.28) for _ in range(140)]
        self.shoot = None; self.t = 0.0
        self._tm = QTimer(self); self._tm.timeout.connect(self._tick); self._tm.start(33)

    def _tick(self):
        w = self.window()
        if not self.isVisible() or (w and w.isMinimized()):
            return
        self.t += 0.03
        if self.shoot is None and random.random() < 0.007:
            self.shoot = [random.random()*.6+.2, random.random()*.35, 0.0]
        if self.shoot:
            self.shoot[2] += 0.028
            if self.shoot[2] > 1:
                self.shoot = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self); w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor("#0b0718")); g.setColorAt(1, QColor("#030207"))
        p.fillRect(self.rect(), g)
        dx, dy = math.sin(self.t*.3)*26, math.cos(self.t*.22)*20
        for cx, cy, rr, col in [(.2+dx/max(w,1), .1, .6, QColor(124,58,237,42)),
                                (.85, .82+dy/max(h,1), .6, QColor(217,70,239,26)),
                                (.55, .45, .45, QColor(34,211,238,16))]:
            rg = QRadialGradient(cx*w, cy*h, rr*max(w,h))
            rg.setColorAt(0, col); rg.setColorAt(1, QColor(0,0,0,0))
            p.fillRect(self.rect(), rg)
        p.setPen(QPen(QColor(255,255,255,6), 1))
        for x in range(0, w, 32):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, 32):
            p.drawLine(0, y, w, y)
        for x, y, s, ph in self.stars:
            a = int(70 + 110*(0.5 + 0.5*math.sin(self.t*1.5 + ph)))
            p.setPen(QPen(QColor(226,220,255,a), s)); p.drawPoint(int(x*w), int(y*h))
        if self.shoot:
            sx, sy, pr = self.shoot
            x0, y0 = sx*w, sy*h
            x1, y1 = x0 + pr*240, y0 + pr*130
            p.setPen(QPen(QColor(230,225,255,int(210*(1-pr))), 1.6))
            p.drawLine(int(x0+(x1-x0)*.72), int(y0+(y1-y0)*.72), int(x1), int(y1))
        p.end()


class ShimmerProgress(QProgressBar):
    def __init__(self):
        super().__init__(); self.setTextVisible(False); self.setFixedHeight(10)
        self._t = 0
        self._tm = QTimer(self); self._tm.timeout.connect(self._tick); self._tm.start(45)

    def _tick(self):
        if self.isVisible():
            self._t += 4; self.update()

    def paintEvent(self, e):
        p = QPainter(self); w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#150f28"))
        p.setPen(QPen(QColor("#2e2447"), 1)); p.drawRect(0, 0, w-1, h-1)
        fw = int(w * self.value() / max(1, self.maximum()))
        if fw > 2:
            g = QLinearGradient(0, 0, fw, 0)
            g.setColorAt(0, QColor("#7c3aed")); g.setColorAt(1, QColor("#d946ef"))
            p.fillRect(1, 1, fw-2, h-2, g)
            x = self._t % max(fw, 1)
            p.fillRect(1+x, 1, min(14, max(0, fw-2-x)), h-2, QColor(255,255,255,60))
        p.end()


class Toast(QLabel):
    def __init__(self, parent, text, kind="info"):
        super().__init__(parent)
        self.setText(text); self.setWordWrap(True)
        col = {"success":"#4ade80","error":"#f87171","warning":"#fbbf24"}.get(kind, "#8b5cf6")
        self.setStyleSheet(f"background:#150f28; border:2px solid {col}; color:#efeaff;"
                           "padding:10px 14px; font-size:12px;")
        self.setMaximumWidth(360); self.adjustSize()
        self.eff = QGraphicsOpacityEffect(self); self.setGraphicsEffect(self.eff)
        self.eff.setOpacity(0)

    def play(self, x, y):
        self.move(x + 40, y)
        self.fade = QPropertyAnimation(self.eff, b"opacity", self)
        self.fade.setDuration(220); self.fade.setStartValue(0); self.fade.setEndValue(1)
        self.slide = QPropertyAnimation(self, b"pos", self)
        self.slide.setDuration(260)
        self.slide.setStartValue(QPointF(x+40, y).toPoint())
        self.slide.setEndValue(QPointF(x, y).toPoint())
        self.slide.setEasingCurve(QEasingCurve.OutCubic)
        self.grp = QParallelAnimationGroup(self)
        self.grp.addAnimation(self.fade); self.grp.addAnimation(self.slide); self.grp.start()
        QTimer.singleShot(3600, self._out)

    def _out(self):
        self.fade2 = QPropertyAnimation(self.eff, b"opacity", self)
        self.fade2.setDuration(280); self.fade2.setStartValue(1); self.fade2.setEndValue(0)
        self.fade2.finished.connect(self.deleteLater); self.fade2.start()


# ====================== диалог создания ======================
class CreateDialog(QDialog):
    LOADER_MAP = [None, "fabric", "forge", "neoforge", "quilt"]

    def __init__(self, parent, mc):
        super().__init__(parent)
        self.setWindowTitle("Создание сборки")
        self.setMinimumWidth(480)
        self.mc = mc
        lay = QVBoxLayout(self); lay.setSpacing(14); lay.setContentsMargins(22, 22, 22, 22)
        head = QLabel("Minecraft " + mc); head.setObjectName("h1")
        lay.addWidget(head)
        form = QFormLayout(); form.setSpacing(10)
        self.name_edit = QLineEdit("Minecraft " + mc)
        self.loader_combo = QComboBox()
        self.loader_combo.addItems(["Без лоадера (ванилла)", "Fabric", "Forge", "NeoForge", "Quilt"])
        self.loader_ver_combo = QComboBox()
        self.loader_ver_combo.addItem("Авто (рекомендуется)")
        self.loader_ver_combo.setEnabled(False)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Обычная (одиночная)", "Кооператив (для Void Link)"])
        form.addRow("Название:", self.name_edit)
        form.addRow("Лоадер модов:", self.loader_combo)
        form.addRow("Версия лоадера:", self.loader_ver_combo)
        form.addRow("Режим:", self.mode_combo)
        lay.addLayout(form)
        self.loader_status = QLabel("Выбери лоадер — список версий подтянется автоматически.")
        self.loader_status.setObjectName("muted"); self.loader_status.setWordWrap(True)
        lay.addWidget(self.loader_status)
        btns = QHBoxLayout()
        cancel = MCButton("ОТМЕНА"); ok = MCButton("СОЗДАТЬ И СКАЧАТЬ", "primary")
        btns.addStretch(); btns.addWidget(cancel); btns.addWidget(ok)
        lay.addLayout(btns)
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        self.loader_combo.currentIndexChanged.connect(self._on_loader_changed)

    def _on_loader_changed(self, idx):
        loader = self.LOADER_MAP[idx]
        self.loader_ver_combo.clear()
        self.loader_ver_combo.addItem("Авто (рекомендуется)")
        if not loader:
            self.loader_ver_combo.setEnabled(False)
            self.loader_status.setText("Ванилла — лоадер не нужен.")
            return
        self.loader_ver_combo.setEnabled(True)
        self.loader_status.setText(f"Загружаю список версий {loader}…")
        def fetch():
            vers = fetch_loader_versions(self.mc, loader)
            QTimer.singleShot(0, lambda: self._loader_loaded(vers))
        threading.Thread(target=fetch, daemon=True).start()

    def _loader_loaded(self, vers):
        for v in vers:
            self.loader_ver_combo.addItem(v)
        if vers:
            self.loader_status.setText(f"Доступно версий: {len(vers)}.")
        else:
            self.loader_status.setText("Список не получен — оставь «Авто». (Для NeoForge всегда авто.)")

    def result_data(self):
        loader = self.LOADER_MAP[self.loader_combo.currentIndex()]
        lv_idx = self.loader_ver_combo.currentIndex()
        lv = None if lv_idx <= 0 else self.loader_ver_combo.currentText()
        return {"name": self.name_edit.text().strip(), "loader": loader,
                "loader_version": lv,
                "mode": "coop" if self.mode_combo.currentIndex() == 1 else "vanilla"}


# ====================== диалог выбора сборки для мода ======================
class SelectInstanceDialog(QDialog):
    """Диалог выбора сборки для установки мода с проверкой совместимости."""
    def __init__(self, parent, mod_name, mod_info):
        super().__init__(parent)
        self.setWindowTitle("Установка мода")
        self.setMinimumWidth(500)
        self.mod_info = mod_info
        self.selected_inst = None
        
        lay = QVBoxLayout(self); lay.setSpacing(14); lay.setContentsMargins(22, 22, 22, 22)
        head = QLabel(f"Установка мода: {mod_name}")
        head.setObjectName("h1"); lay.addWidget(head)
        
        # Информация о моде
        versions = mod_info.get("versions", [])
        loaders_list = mod_info.get("loaders", [])
        info_text = f"Поддерживаемые версии: {', '.join(versions[:5]) if versions else 'неизвестно'}\n"
        info_text += f"Лоадеры: {', '.join(loaders_list) if loaders_list else 'неизвестно'}"
        info = QLabel(info_text)
        info.setObjectName("muted"); info.setWordWrap(True)
        lay.addWidget(info)
        
        lay.addWidget(QLabel("Выбери сборку:"))
        self.inst_list = QListWidget(); self.inst_list.setObjectName("list")
        self.inst_list.setIconSize(QSize(32, 32))
        lay.addWidget(self.inst_list)
        
        self.compat_label = QLabel("")
        self.compat_label.setObjectName("muted")
        self.compat_label.setWordWrap(True)
        lay.addWidget(self.compat_label)
        
        btns = QHBoxLayout()
        cancel = MCButton("ОТМЕНА"); ok = MCButton("УСТАНОВИТЬ", "primary")
        ok.setEnabled(False); self.ok_btn = ok
        btns.addStretch(); btns.addWidget(cancel); btns.addWidget(ok)
        lay.addLayout(btns)
        
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        self.inst_list.currentItemChanged.connect(self._on_select)
        
        self._load_instances()
    
    def _load_instances(self):
        instances = config.list_instances()
        mod_versions = set(self.mod_info.get("versions", []))
        mod_loaders = set(self.mod_info.get("loaders", []))
        
        for inst in instances:
            mc = inst["mc"]
            loader = inst.get("loader") or "vanilla"
            
            # Проверка совместимости
            mc_ok = mc in mod_versions or "any" in mod_versions or not mod_versions
            loader_ok = loader in mod_loaders or "vanilla" in mod_loaders or not mod_loaders
            
            if mc_ok and loader_ok:
                status = "✓"
            elif mc_ok:
                status = "⚠ (другой лоадер)"
            elif loader_ok:
                status = "⚠ (другая версия MC)"
            else:
                status = "✗ (несовместимо)"
            
            text = f'{status}  {inst["name"]}  ·  {mc}  ·  {loader}'
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, inst["id"])
            item.setData(Qt.UserRole + 1, mc_ok and loader_ok)
            self.inst_list.addItem(item)
    
    def _on_select(self, current, previous):
        if not current:
            self.ok_btn.setEnabled(False)
            self.compat_label.setText("")
            return
        
        inst_id = current.data(Qt.UserRole)
        compatible = current.data(Qt.UserRole + 1)
        inst = config.get_instance(inst_id)
        
        if compatible:
            self.ok_btn.setEnabled(True)
            self.selected_inst = inst
            self.compat_label.setText(f"✓ Совместимо с {inst['mc']} + {inst.get('loader') or 'vanilla'}")
            self.compat_label.setStyleSheet("color: #4ade80;")
        else:
            self.ok_btn.setEnabled(False)
            self.selected_inst = None
            self.compat_label.setText("⚠ Мод несовместим с этой сборкой")
            self.compat_label.setStyleSheet("color: #fbbf24;")


# ====================== backend ======================
GAME_ARGS_PRESET = ("-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 "
                    "-XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:SurvivorRatio=32 "
                    "-XX:+PerfDisableSharedMem -XX:TargetSurvivorRatio=90 -XX:G1NewSizePercent=50 "
                    "-XX:G1MaxNewSizePercent=60 -XX:G1HeapRegionSize=32M -XX:G1ReservePercent=15 "
                    "-XX:InitiatingHeapOccupancyPercent=20 -XX:+AlwaysPreTouch -Dfile.encoding=UTF-8")


class Backend(QObject):
    sig_toast = Signal(str, str); sig_task = Signal(object); sig_log = Signal(object)
    sig_status = Signal(str); sig_versions = Signal(object); sig_instances = Signal(object)
    sig_active = Signal(str); sig_game = Signal(str); sig_packs = Signal(object)
    sig_accounts = Signal(object); sig_friends = Signal(object)
    sig_icon = Signal(str, bytes); sig_link = Signal(object); sig_confirm = Signal(object)
    sig_scan = Signal(object); sig_mods = Signal(object); sig_mod_icon = Signal(str, bytes)
    sig_gpu = Signal(object)

    def __init__(self):
        super().__init__()
        self.settings = config.load(); self.proc = None; self.busy = False
        self.stop_watch = threading.Event(); self._last = {}
        self._logbuf = []; self._loglock = threading.Lock()
        self.link = None
        self._closing = False
        net.set_strict(self.settings.get("strict_urls", True))

    def toast(self, t, k="info"):
        if self._closing: return
        self.sig_toast.emit(security.redact(t), k)
    def status(self, t):
        if self._closing: return
        self.sig_status.emit(security.redact(t))
    def log(self, t):
        if self._closing: return
        self.sig_log.emit([security.redact(str(t))]); LOG.info(t)

    def task(self, title, stage, done, total, st="run"):
        if self._closing: return
        now = time.time(); key = title + stage
        if st == "run" and done < total and now - self._last.get(key, 0) < 0.1:
            return
        self._last[key] = now
        self.sig_task.emit({"title": title, "stage": stage,
                            "percent": round(done/total*100) if total else 0, "status": st})

    def save(self, patch): self.settings.update(patch); config.save(self.settings)

    def _job(self, title, fn):
        if self.busy:
            self.toast("Лаунчер занят", "warning"); return
        self.busy = True
        self.sig_task.emit({"title": title, "stage": "Подготовка", "percent": 0, "status": "run"})
        def progress(s, d, t, l=""): self.task(title, l or s, d, t)
        def run():
            try:
                fn(progress, self.status)
                self.task(title, "Готово", 1, 1, "done")
            except Exception as e:
                LOG.exception("job")
                self.sig_log.emit([security.redact(traceback.format_exc())])
                self.task(title, "Ошибка", 0, 1, "error")
                self.toast(str(e), "error")
            finally:
                self.busy = False
        threading.Thread(target=run, daemon=True).start()

    def bootstrap_versions(self):
        cached = mojang.cached_versions() or config.load_versions_cache()
        if cached:
            self.sig_versions.emit(cached)
        def run():
            try:
                if cached and mojang.cache_age() < 900:
                    return
                self.sig_versions.emit(mojang.all_versions(force=True))
            except Exception as e:
                if not cached:
                    self.toast("Версии: " + str(e), "error")
        threading.Thread(target=run, daemon=True).start()

    def refresh_versions(self):
        def run():
            try:
                self.sig_versions.emit(mojang.all_versions(force=True))
                self.toast("Список версий обновлён", "success")
            except Exception as e:
                self.toast("Версии: " + str(e), "error")
        threading.Thread(target=run, daemon=True).start()

    def refresh_instances(self):
        self.sig_instances.emit(config.list_instances())

    def detect_gpu(self):
        def run():
            try:
                info = gpu.detect_gpu()
                info["nvidia"] = gpu.nvidia_available()
                self.sig_gpu.emit(info)
            except Exception as e:
                self.sig_gpu.emit({"error": str(e)})
        threading.Thread(target=run, daemon=True).start()

    def create_instance(self, name, mc, mode, loader=None, loader_version=None):
        name = (name or "").strip()
        if not name or not mc:
            self.toast("Укажи имя и версию", "warning"); return
        iid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "inst"
        iid += "-" + uuid.uuid4().hex[:6]
        inst = {"id": iid, "name": name, "mc": mc, "type": mode, "loader": loader,
                "loader_version": loader_version, "source": "mojang",
                "created": time.time(), "last_played": 0, "playtime": 0, "icon": ""}
        def job(p, s):
            security.secure_dir(config.instance_dir(iid))
            s("Качаю файлы " + mc + "…")
            mojang.install_base(mc, self.settings, p, s)
            if loader:
                s(f"Устанавливаю лоадер {loader} {loader_version or '(авто)'}…")
                loaders.install_loader(inst, self.settings, p, s)
            config.save_instance(inst)
            self.settings["active_instance"] = iid
            config.save(self.settings)
            self.refresh_instances()
            self.sig_active.emit(iid)
            self.toast("Сборка «" + name + "» готова", "success")
        self._job("Создание сборки", job)

    def select_instance(self, iid):
        if config.get_instance(iid):
            self.settings["active_instance"] = iid
            config.save(self.settings)
            self.sig_active.emit(iid)

    def delete_instance(self, iid):
        try:
            shutil.rmtree(config.instance_dir(iid), ignore_errors=True)
        except ValueError:
            return
        if self.settings.get("active_instance") == iid:
            rest = config.list_instances()
            self.settings["active_instance"] = rest[0]["id"] if rest else ""
            config.save(self.settings)
        self.refresh_instances()
        self.toast("Сборка удалена", "info")

    def set_icon(self, iid, src):
        inst = config.get_instance(iid)
        if not inst or not src:
            return
        try:
            pm = QPixmap(src)
            if pm.isNull():
                raise RuntimeError("Не читается как изображение")
            pm = pm.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            dst = os.path.join(config.instance_dir(iid), "icon.png")
            pm.save(dst, "PNG")
            os.chmod(dst, 0o600)
            inst["icon"] = dst
            config.save_instance(inst)
            self.refresh_instances()
            self.toast("Аватар обновлён", "success")
        except Exception as e:
            self.toast("Аватар: " + str(e), "error")

    def install_tunnel(self):
        inst = config.get_instance(self.settings.get("active_instance"))
        if not inst:
            self.toast("Сборка не выбрана", "warning"); return
        def job(p, s):
            from core import coop
            if not coop.e4all_supported(inst["mc"]):
                raise RuntimeError("Туннель-мод только для 1.18+. Для старых версий используй прямой Void Link.")
            inst["loader"] = "fabric"
            loaders.install_loader(inst, self.settings, p, s)
            coop.install_coop(inst, self.settings, p, s, self.toast)
            config.save_instance(inst)
            self.refresh_instances()
            self.toast("Туннель-мод установлен", "success")
        self._job("Туннель", job)

    def search_packs(self, source, q, sort):
        def run():
            try:
                hits = (modpacks.modrinth_search(q, sort, 0) if source == "modrinth"
                        else modpacks.curseforge_search(q, sort, self.settings.get("cf_key", ""), 0))
                self.sig_packs.emit({"ok": True, "hits": hits})
                for h in hits:
                    if h.get("icon"):
                        threading.Thread(target=self._icon, args=(h["id"], h["icon"]),
                                         daemon=True).start()
            except Exception as e:
                self.sig_packs.emit({"ok": False, "error": security.redact(e)})
        threading.Thread(target=run, daemon=True).start()

    def _icon(self, key, url):
        try:
            self.sig_icon.emit(key, net.get_bytes(url, 2 << 20))
        except Exception:
            pass

    def install_pack(self, source, pid, name):
        def job(p, s):
            inst = (modpacks.modrinth_install(pid, name, self.settings, p, s)
                    if source == "modrinth"
                    else modpacks.curseforge_install(pid, None, name, self.settings, p, s))
            self.settings["active_instance"] = inst["id"]
            config.save(self.settings)
            self.refresh_instances()
            self.sig_active.emit(inst["id"])
            self.toast("Сборка установлена", "success")
        self._job("Установка сборки", job)

    def import_pack(self, path):
        def job(p, s):
            inst = modpacks.import_pack(path, self.settings, p, s)
            self.settings["active_instance"] = inst["id"]
            config.save(self.settings)
            self.refresh_instances()
            self.sig_active.emit(inst["id"])
            self.toast("Импортировано", "success")
        self._job("Импорт", job)

    def search_mods(self, source, q, mc, loader, sort):
        def run():
            try:
                hits = (mods.modrinth_search(q, mc, sort, 0) if source == "modrinth"
                        else mods.curseforge_search(q, mc, sort, self.settings.get("cf_key", ""), 0))
                
                # Получаем детальную информацию о версиях для каждого мода
                for h in hits:
                    if source == "modrinth":
                        try:
                            versions_data = net.get_json(f"{mods.MODRINTH}/project/{h['id']}/version")
                            mc_versions = set()
                            loaders_set = set()
                            for v in versions_data[:10]:  # Берём последние 10 версий
                                mc_versions.update(v.get("game_versions", []))
                                loaders_set.update(v.get("loaders", []))
                            h["versions"] = sorted(mc_versions, reverse=True)[:10]
                            h["loaders"] = sorted(loaders_set)
                        except Exception:
                            h["versions"] = []
                            h["loaders"] = []
                    else:
                        # Для CurseForge информация уже есть в поиске
                        h["versions"] = h.get("versions", [])
                        h["loaders"] = h.get("loaders", [])
                
                self.sig_mods.emit({"ok": True, "hits": hits, "mc": mc, "loader": loader})
                for h in hits:
                    if h.get("icon"):
                        threading.Thread(target=self._mod_icon, args=(h["id"], h["icon"]),
                                         daemon=True).start()
            except Exception as e:
                self.sig_mods.emit({"ok": False, "error": security.redact(e)})
        threading.Thread(target=run, daemon=True).start()

    def _mod_icon(self, key, url):
        try:
            self.sig_mod_icon.emit(key, net.get_bytes(url, 2 << 20))
        except Exception:
            pass

    def install_mod(self, source, pid, inst):
        """Устанавливает мод в указанную сборку с проверкой совместимости."""
        if not inst:
            self.toast("Сборка не выбрана", "warning"); return
        
        # Проверяем, что сборка существует
        inst_data = config.get_instance(inst["id"])
        if not inst_data:
            self.toast("Сборка не найдена", "error"); return
        
        def job(p, s):
            mc = inst_data["mc"]
            loader = inst_data.get("loader") or "fabric"
            
            s(f"Проверяю совместимость {mc} + {loader}…")
            
            try:
                if source == "modrinth":
                    # Получаем версии мода
                    versions_data = net.get_json(f"{mods.MODRINTH}/project/{pid}/version")
                    compatible_version = None
                    
                    for v in versions_data:
                        game_versions = v.get("game_versions", [])
                        loaders = v.get("loaders", [])
                        
                        if mc in game_versions and loader in loaders:
                            compatible_version = v
                            break
                    
                    if not compatible_version:
                        raise RuntimeError(f"Мод несовместим с {mc} + {loader}")
                    
                    files = compatible_version.get("files", [])
                    if not files:
                        raise RuntimeError("У мода нет файлов")
                    
                    fname = mods.modrinth_install(pid, inst_data, self.settings, p, s)
                else:
                    fname = mods.curseforge_install(pid, inst_data, self.settings, p, s)
                
                self.toast(f"Мод {fname} установлен в {inst_data['name']}", "success")
            except Exception as e:
                self.toast(f"Ошибка установки: {e}", "error")
                raise
        
        self._job(f"Установка мода в {inst['name']}", job)

    def download_skin(self, nick):
        inst = config.get_instance(self.settings.get("active_instance"))
        if not inst:
            self.toast("Сборка не выбрана", "warning"); return
        nick = (nick or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", nick):
            self.toast("Ник 3–16 символов", "warning"); return
        def job(p, s):
            s("Скачиваю скин для " + nick + "…")
            skin_path = skins.download_skin(nick, config.instance_dir(inst["id"]))
            skins.set_active_skin(inst, skin_path)
            self.toast(f"Скин для {nick} установлен", "success")
        self._job("Скачивание скина", job)

    def install_skin_mod(self):
        inst = config.get_instance(self.settings.get("active_instance"))
        if not inst:
            self.toast("Сборка не выбрана", "warning"); return
        def job(p, s):
            skins.install_skin_mod(inst, self.settings, p, s)
            self.toast("Мод для скинов установлен", "success")
        self._job("Установка мода для скинов", job)

    def play(self, iid=None, force=False):
        if self.busy:
            self.toast("Занят", "warning"); return
        iid = iid or self.settings.get("active_instance")
        inst = config.get_instance(iid)
        if not inst:
            self.toast("Сборка не выбрана", "warning"); return
        rep = modscan.scan_instance(config.instance_dir(iid))
        if rep["crit"] and not force:
            self.sig_scan.emit({"crit": rep["crit"], "warn": rep["warn"], "iid": iid})
            return
        self.busy = True
        self.sig_game.emit("preparing")
        iso = self.settings.get("net_isolate", False)
        need_net = (inst.get("type") == "coop") or (self.link is not None)
        use_net = (not iso) or need_net
        if iso and need_net:
            self.toast("Изоляция сети отключена: нужен мультиплеер", "info")
        def run():
            try:
                self.proc = mojang.launch(inst, self.settings,
                    lambda s, d, t, l="": self.task("Запуск Minecraft", l or s, d, t),
                    self.status, self.toast, net=use_net)
            except Exception as e:
                LOG.exception("launch")
                self.sig_log.emit([security.redact(traceback.format_exc())])
                self.busy = False; self.proc = None
                self.sig_game.emit("idle")
                self.toast(str(e), "error")
                return
            self.sig_game.emit("running")
            inst["last_played"] = time.time()
            config.save_instance(inst)
            self.refresh_instances()
            threading.Thread(target=self._stdout, daemon=True).start()
            self.stop_watch.clear()
            self.proc.wait()
            self.stop_watch.set()
            self.busy = False; self.proc = None
            self.sig_game.emit("idle")
        threading.Thread(target=run, daemon=True).start()

    def _stdout(self):
        proc = self.proc
        def flush():
            while True:
                time.sleep(0.3)
                with self._loglock:
                    buf, self._logbuf = self._logbuf, []
                if buf:
                    self.sig_log.emit([security.redact(x) for x in buf[:200]])
                elif proc.poll() is not None:
                    break
        threading.Thread(target=flush, daemon=True).start()
        try:
            for line in proc.stdout:
                with self._loglock:
                    if len(self._logbuf) < 1500:
                        self._logbuf.append(line.rstrip())
        except Exception:
            pass

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _confirm_peer(self, addr):
        if not self.settings.get("link_confirm", True):
            return True
        box = {"ev": threading.Event(), "ok": False}
        self.sig_confirm.emit({"addr": f"{addr[0]}:{addr[1]}", "box": box})
        box["ev"].wait(60)
        return box["ok"]

    def link_host(self, name, port):
        if not isinstance(port, int) or port <= 0 or port > 65535:
            self.toast("Введи корректный порт (1–65535)", "warning"); return
        def run():
            try:
                if self.link:
                    self.link.stop()
                self.link, code = voidlink.host(
                    name or "Void world", port,
                    on_status=lambda k, i=None: self.sig_link.emit({"ev": k, **(i or {})}),
                    on_log=lambda m: self.log(m),
                    allow_stun=self.settings.get("allow_stun", True))
                self.sig_link.emit({"code": code, "state": "hosting", "lan": port})
                self.toast(f"Код готов (порт {port})", "success")
            except Exception as e:
                self.toast("Void Link: " + str(e), "error")
        threading.Thread(target=run, daemon=True).start()

    def link_join(self, code):
        def run():
            try:
                if self.link:
                    self.link.stop()
                self.link, port = voidlink.join(
                    code,
                    on_status=lambda k, i=None: self.sig_link.emit({"ev": k, **(i or {})}),
                    on_log=lambda m: self.log(m))
                self.sig_link.emit({"joined": port, "state": "joining"})
                self.toast("Пробиваюсь к хосту…", "info")
            except Exception as e:
                self.toast("Void Link: " + str(e), "error")
        threading.Thread(target=run, daemon=True).start()

    def link_stop(self):
        if self.link:
            self.link.stop()
            self.link = None
        self.sig_link.emit({"state": "idle"})

    def add_account(self, n):
        n = (n or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", n):
            self.toast("Ник 3–16 символов", "warning"); return
        if any(a["nick"] == n for a in self.settings["accounts"]):
            self.toast("Уже есть", "warning"); return
        self.settings["accounts"].append({"nick": n})
        self.settings["active_account"] = n
        config.save(self.settings)
        self.sig_accounts.emit(self.settings["accounts"])

    def select_account(self, n):
        self.settings["active_account"] = n
        config.save(self.settings)
        self.sig_accounts.emit(self.settings["accounts"])

    def remove_account(self, n):
        accs = self.settings.get("accounts", [])
        if len(accs) <= 1:
            self.toast("Нельзя удалить последний аккаунт", "warning"); return
        self.settings["accounts"] = [a for a in accs if a["nick"] != n]
        if self.settings.get("active_account") == n:
            self.settings["active_account"] = self.settings["accounts"][0]["nick"]
        config.save(self.settings)
        self.sig_accounts.emit(self.settings["accounts"])
        self.toast("Аккаунт удалён", "info")

    def add_friend(self, n):
        n = (n or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", n):
            self.toast("Ник 3–16 символов", "warning"); return
        f = self.settings.setdefault("friends", [])
        if n not in f:
            f.append(n)
        config.save(self.settings)
        self.sig_friends.emit(f)

    def remove_friend(self, n):
        f = self.settings.get("friends", [])
        if n in f:
            f.remove(n)
        config.save(self.settings)
        self.sig_friends.emit(f)

    def open(self, path):
        security.secure_dir(path)
        subprocess.Popen(["xdg-open", path])


# ====================== окно ======================
class VoidWindow(QMainWindow):
    def __init__(self, be):
        super().__init__()
        self.be = be; self.pages = {}; self._drag = None
        self._allv = []; self._inst = []; self._hits = []; self._icon_labels = {}
        self._mod_hits = []; self._mod_icon_labels = {}
        self._toasts = []
        self.setWindowTitle("Void Launcher")
        self.resize(1320, 860); self.setMinimumSize(1120, 720)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(140)
        self._search_timer.timeout.connect(self._render_versions)
        self._build(); self._wire(); self._load()

    def panel(self):
        return AnimPanel()

    def _build(self):
        root = VoidCanvas(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        tb = QWidget(); tb.setObjectName("titlebar"); tb.setFixedHeight(54)
        tl = QHBoxLayout(tb); tl.setContentsMargins(16, 0, 8, 0)
        logo = QLabel("VOID LAUNCHER"); logo.setObjectName("logo")
        sub = QLabel("· minecraft из пустоты"); sub.setObjectName("sub")
        tl.addWidget(logo); tl.addWidget(sub); tl.addStretch(1)
        self.btn_min = QPushButton("—"); self.btn_min.setObjectName("win")
        self.btn_close = QPushButton("✕"); self.btn_close.setObjectName("win")
        tl.addWidget(self.btn_min); tl.addWidget(self.btn_close)
        
        # Перетаскивание через нативный метод Qt
        tb.mousePressEvent = self._tb_press
        for child in (logo, sub):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        
        outer.addWidget(tb)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        outer.addLayout(body, 1)

        side = QWidget(); side.setObjectName("sidebar"); side.setFixedWidth(230)
        sl = QVBoxLayout(side); sl.setContentsMargins(14, 12, 14, 14); sl.setSpacing(5)
        sl.addWidget(VoidLogo(70), 0, Qt.AlignHCenter)
        self.nav = {}
        for key, label in [("home", "ГЛАВНАЯ"), ("versions", "ВЕРСИИ"), ("modpacks", "СБОРКИ"),
                           ("mods", "МОДЫ"), ("instances", "МОИ СБОРКИ"),
                           ("link", "VOID LINK"), ("settings", "НАСТРОЙКИ")]:
            b = QPushButton(label); b.setObjectName("nav"); b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            self.nav[key] = b; sl.addWidget(b)
        sl.addStretch(1)
        self.acc_label = QLabel("Player"); self.acc_label.setObjectName("accname")
        sl.addWidget(self.acc_label)
        body.addWidget(side)

        right = QVBoxLayout(); right.setContentsMargins(18, 16, 18, 14); right.setSpacing(12)
        body.addLayout(right, 1)
        self.stack = QStackedWidget(); right.addWidget(self.stack, 1)

        dock = self.panel(); dl = QVBoxLayout(dock); dl.setContentsMargins(14, 10, 14, 10)
        row = QHBoxLayout()
        self.task_title = QLabel("Готов"); self.task_pct = QLabel("")
        row.addWidget(self.task_title); row.addStretch(1); row.addWidget(self.task_pct)
        dl.addLayout(row)
        self.task_bar = ShimmerProgress(); dl.addWidget(self.task_bar)
        self.task_stage = QLabel(""); self.task_stage.setObjectName("muted")
        dl.addWidget(self.task_stage)
        dock.hide(); self.dock = dock; right.addWidget(dock)

        self._page_home(); self._page_versions(); self._page_modpacks()
        self._page_mods(); self._page_instances(); self._page_link(); self._page_settings()

    def _add(self, k, w):
        self.pages[k] = w; self.stack.addWidget(w)

    def _page_home(self):
        w = QWidget(); l = QVBoxLayout(w); l.setSpacing(14)
        welcome = QLabel("С возвращением в пустоту"); welcome.setObjectName("welcome")
        l.addWidget(welcome)

        hero = self.panel(); hero.setObjectName("hero")
        hl = QHBoxLayout(hero); hl.setContentsMargins(26, 24, 26, 24); hl.setSpacing(24)
        self.hero_icon = QLabel(); self.hero_icon.setFixedSize(130, 130)
        self.hero_icon.setObjectName("avatar"); self.hero_icon.setAlignment(Qt.AlignCenter)
        hl.addWidget(self.hero_icon)
        col = QVBoxLayout(); col.setSpacing(8)
        self.hero_name = QLabel("Сборка не выбрана"); self.hero_name.setObjectName("h1")
        self.hero_meta = QLabel("—"); self.hero_meta.setObjectName("muted")
        col.addWidget(self.hero_name); col.addWidget(self.hero_meta)
        col.addSpacing(10)
        row = QHBoxLayout()
        self.play_btn = MCButton("▶  ИГРАТЬ", "primary"); self.play_btn.setBreathing(True)
        self.play_btn.setMinimumHeight(52)
        self.stop_btn = MCButton("■ СТОП", "danger"); self.stop_btn.hide()
        row.addWidget(self.play_btn); row.addWidget(self.stop_btn); row.addStretch(1)
        col.addLayout(row)
        self.status = QLabel("Готов к работе."); self.status.setObjectName("muted")
        col.addWidget(self.status)
        hl.addLayout(col, 1); l.addWidget(hero)

        acts = QHBoxLayout(); acts.setSpacing(10)
        self.act_create = MCButton("＋ Новая сборка"); self.act_create.setMinimumHeight(46)
        self.act_coop = MCButton("⇄ Играть с другом"); self.act_coop.setMinimumHeight(46)
        self.act_settings = MCButton("⚙ Настройки"); self.act_settings.setMinimumHeight(46)
        acts.addWidget(self.act_create); acts.addWidget(self.act_coop); acts.addWidget(self.act_settings)
        l.addLayout(acts)

        quick = self.panel(); ql = QGridLayout(quick); ql.setContentsMargins(20, 14, 20, 14)
        ql.addWidget(QLabel("ПАМЯТЬ"), 0, 0)
        self.ram = QSlider(Qt.Horizontal); self.ram.setRange(1024, 16384); self.ram.setSingleStep(256)
        self.ram_val = QLabel("3.0 ГБ")
        ql.addWidget(self.ram, 0, 1); ql.addWidget(self.ram_val, 0, 2)
        l.addWidget(quick)

        rec = self.panel(); rl = QVBoxLayout(rec); rl.setContentsMargins(18, 14, 18, 14)
        rl.addWidget(QLabel("ПОСЛЕДНЯЯ СЫГРАННАЯ"))
        self.recent = QListWidget(); self.recent.setObjectName("list")
        self.recent.setIconSize(QSize(32, 32)); self.recent.setFixedHeight(160)
        rl.addWidget(self.recent); l.addWidget(rec)
        self._add("home", w)

    def _page_versions(self):
        w = QWidget(); l = QVBoxLayout(w); l.setSpacing(10)
        bar = self.panel(); bl = QHBoxLayout(bar); bl.setContentsMargins(12, 10, 12, 10)
        self.ver_tabs = QComboBox()
        self.ver_tabs.addItems(["Релизы", "Снапшоты", "Beta", "Alpha", "Все"])
        self.ver_search = QLineEdit()
        self.ver_search.setPlaceholderText("Поиск: 1.21.1, 1.16.5, b1.7.3…")
        self.ver_count = QLabel("0"); self.ver_count.setObjectName("muted")
        self.ver_refresh = MCButton("ОБНОВИТЬ")
        bl.addWidget(self.ver_tabs); bl.addWidget(self.ver_search, 1)
        bl.addWidget(self.ver_count); bl.addWidget(self.ver_refresh)
        l.addWidget(bar)
        
        hint = QLabel("ПКМ по версии — создать сборку с этой версией")
        hint.setObjectName("muted"); l.addWidget(hint)
        
        self.ver_list = QListWidget(); self.ver_list.setObjectName("list")
        self.ver_list.setContextMenuPolicy(Qt.CustomContextMenu)
        l.addWidget(self.ver_list, 1)
        self._add("versions", w)

    def _page_modpacks(self):
        w = QWidget(); l = QVBoxLayout(w); l.setSpacing(10)
        bar = self.panel(); bl = QHBoxLayout(bar); bl.setContentsMargins(12, 10, 12, 10)
        self.pack_source = QComboBox(); self.pack_source.addItems(["Modrinth", "CurseForge"])
        self.pack_search = QLineEdit(); self.pack_search.setPlaceholderText("Поиск сборок…")
        self.pack_go = MCButton("НАЙТИ", "primary"); self.pack_import = MCButton("ИМПОРТ")
        bl.addWidget(self.pack_source); bl.addWidget(self.pack_search, 1)
        bl.addWidget(self.pack_go); bl.addWidget(self.pack_import)
        l.addWidget(bar)
        self.pack_scroll = QScrollArea(); self.pack_scroll.setObjectName("scroll")
        self.pack_scroll.setWidgetResizable(True)
        self.pack_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.pack_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pack_box = QWidget(); self.pack_box.setObjectName("packbox")
        self.pack_vbox = QVBoxLayout(self.pack_box)
        self.pack_vbox.setContentsMargins(4, 4, 14, 4); self.pack_vbox.setSpacing(8)
        self.pack_vbox.addStretch(1)
        self.pack_scroll.setWidget(self.pack_box)
        self.pack_scroll.verticalScrollBar().setSingleStep(30)
        l.addWidget(self.pack_scroll, 1)
        self._add("modpacks", w)

    def _page_mods(self):
        w = QWidget(); l = QVBoxLayout(w); l.setSpacing(10)
        info = QLabel("Моды устанавливаются в выбранную сборку. Выбери сборку на странице «МОИ СБОРКИ» или при установке.")
        info.setObjectName("muted"); info.setWordWrap(True); l.addWidget(info)
        
        bar = self.panel(); bl = QHBoxLayout(bar); bl.setContentsMargins(12, 10, 12, 10)
        self.mod_source = QComboBox(); self.mod_source.addItems(["Modrinth", "CurseForge"])
        self.mod_search = QLineEdit(); self.mod_search.setPlaceholderText("Поиск модов…")
        self.mod_go = MCButton("НАЙТИ", "primary")
        bl.addWidget(self.mod_source); bl.addWidget(self.mod_search, 1)
        bl.addWidget(self.mod_go)
        l.addWidget(bar)
        self.mod_scroll = QScrollArea(); self.mod_scroll.setObjectName("scroll")
        self.mod_scroll.setWidgetResizable(True)
        self.mod_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.mod_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.mod_box = QWidget(); self.mod_box.setObjectName("packbox")
        self.mod_vbox = QVBoxLayout(self.mod_box)
        self.mod_vbox.setContentsMargins(4, 4, 14, 4); self.mod_vbox.setSpacing(8)
        self.mod_vbox.addStretch(1)
        self.mod_scroll.setWidget(self.mod_box)
        self.mod_scroll.verticalScrollBar().setSingleStep(30)
        l.addWidget(self.mod_scroll, 1)
        self._add("mods", w)

    def _page_instances(self):
        w = QWidget(); l = QVBoxLayout(w); l.setSpacing(10)
        self.inst_list = QListWidget(); self.inst_list.setObjectName("list")
        self.inst_list.setIconSize(QSize(40, 40))
        l.addWidget(self.inst_list, 1)
        row = QHBoxLayout()
        self.inst_play = MCButton("ИГРАТЬ", "primary")
        self.inst_sel = MCButton("АКТИВНОЙ")
        self.inst_icon = MCButton("АВАТАР")
        self.inst_folder = MCButton("ПАПКА")
        self.inst_del = MCButton("УДАЛИТЬ", "danger")
        for b in (self.inst_play, self.inst_sel, self.inst_icon,
                  self.inst_folder, self.inst_del):
            row.addWidget(b)
        row.addStretch(1); l.addLayout(row)
        self._add("instances", w)

    def _page_link(self):
        w = QWidget(); l = QVBoxLayout(w); l.setSpacing(10)
        hero = self.panel(); hl = QVBoxLayout(hero); hl.setContentsMargins(22, 18, 22, 18)
        t = QLabel("VOID LINK"); t.setObjectName("h1")
        d = QLabel("Игра с другом через интернет без проброса портов. Порт вводишь сам — "
                   "он показывается в игре после «Открыть для сети». Работает между разными сетями, "
                   "если оба роутера пропускают пробитие.")
        d.setWordWrap(True); d.setObjectName("muted")
        hl.addWidget(t); hl.addWidget(d)
        self.link_state = QLabel("Запусти игру, открой мир для сети и введи порт ниже.")
        self.link_state.setObjectName("muted"); self.link_state.setWordWrap(True)
        hl.addWidget(self.link_state)
        self.link_sas = QLabel(""); self.link_sas.setObjectName("h1")
        hl.addWidget(self.link_sas)
        l.addWidget(hero)

        hostp = self.panel(); hp = QVBoxLayout(hostp); hp.setContentsMargins(20, 16, 20, 16)
        hp.addWidget(QLabel("Я ХОСТ"))
        r1 = QHBoxLayout()
        self.link_name = QLineEdit(); self.link_name.setPlaceholderText("Название мира")
        self.link_port = QLineEdit(); self.link_port.setPlaceholderText("Порт (например 25565)")
        self.link_port.setFixedWidth(170)
        self.link_host_btn = MCButton("СОЗДАТЬ КОД", "primary")
        r1.addWidget(self.link_name, 1); r1.addWidget(self.link_port); r1.addWidget(self.link_host_btn)
        hp.addLayout(r1)
        hint = QLabel("Порт смотри в чате игры после «Открыть для сети»: "
                      "«Локальная игра размещена на порту XXXXX».")
        hint.setObjectName("muted"); hint.setWordWrap(True); hp.addWidget(hint)
        self.link_code = QPlainTextEdit(); self.link_code.setReadOnly(True)
        self.link_code.setFixedHeight(70)
        self.link_code.setPlaceholderText("Здесь появится инвайт-код")
        hp.addWidget(self.link_code)
        self.link_copy = MCButton("КОПИРОВАТЬ КОД"); hp.addWidget(self.link_copy)
        l.addWidget(hostp)

        joinp = self.panel(); jp = QVBoxLayout(joinp); jp.setContentsMargins(20, 16, 20, 16)
        jp.addWidget(QLabel("Я ДРУГ"))
        self.join_code = QLineEdit(); self.join_code.setPlaceholderText("Вставь код VOID1:…")
        jp.addWidget(self.join_code)
        r2 = QHBoxLayout()
        self.link_join_btn = MCButton("ПОДКЛЮЧИТЬСЯ", "primary")
        self.link_stop_btn = MCButton("ОТКЛЮЧИТЬ", "danger")
        r2.addWidget(self.link_join_btn); r2.addWidget(self.link_stop_btn); r2.addStretch(1)
        jp.addLayout(r2); l.addWidget(joinp)

        note = QLabel("Если не пробивается — скорее всего симметричный NAT. Тогда используй "
                      "туннель-мод ниже (он идёт через чужой сервер и работает всегда для 1.18+).")
        note.setObjectName("muted"); note.setWordWrap(True); l.addWidget(note)

        tun = self.panel(); tl2 = QHBoxLayout(tun); tl2.setContentsMargins(20, 12, 20, 12)
        tl2.addWidget(QLabel("Надёжный кооп для 1.18+ (через мод-релей):"))
        self.tunnel_btn = MCButton("УСТАНОВИТЬ ТУННЕЛЬ-МОД")
        tl2.addWidget(self.tunnel_btn); tl2.addStretch(1)
        l.addWidget(tun); l.addStretch(1)
        self._add("link", w)

    def _page_settings(self):
        w = QWidget(); l = QVBoxLayout(w); l.setSpacing(10)
        scroll = QScrollArea(); scroll.setObjectName("scroll"); scroll.setWidgetResizable(True)
        inner = QWidget(); inner.setObjectName("packbox")
        il = QVBoxLayout(inner); il.setSpacing(10)

        c = self.panel(); g = QGridLayout(c)
        g.setContentsMargins(20, 16, 20, 16); g.setVerticalSpacing(10)
        g.addWidget(QLabel("ПОТОКИ ЗАГРУЗКИ"), 0, 0)
        self.threads = QSlider(Qt.Horizontal); self.threads.setRange(4, 32)
        self.threads_val = QLabel("16")
        g.addWidget(self.threads, 0, 1); g.addWidget(self.threads_val, 0, 2)
        self.verify = QCheckBox("Проверять хэши файлов")
        g.addWidget(self.verify, 1, 0, 1, 3)
        g.addWidget(QLabel("Java path"), 2, 0)
        self.java_path = QLineEdit()
        g.addWidget(self.java_path, 2, 1, 1, 2)
        g.addWidget(QLabel("JVM аргументы"), 3, 0)
        self.java_args = QPlainTextEdit()
        self.java_args.setFixedHeight(64)
        self.java_args.setPlaceholderText("Пусто = стандартные. Кнопка ниже заполнит оптимальные.")
        g.addWidget(self.java_args, 3, 1, 1, 2)
        self.preset_btn = MCButton("🎮 ИГРОВОЙ ПРЕСЕТ (аргументы + 6 ГБ)", "primary")
        g.addWidget(self.preset_btn, 4, 1, 1, 2)
        g.addWidget(QLabel("CurseForge key"), 5, 0)
        self.cf_key = QLineEdit(); self.cf_key.setEchoMode(QLineEdit.Password)
        g.addWidget(self.cf_key, 5, 1, 1, 2)
        il.addWidget(c)

        acc = self.panel(); al = QVBoxLayout(acc); al.setContentsMargins(20, 14, 20, 14)
        al.addWidget(QLabel("АККАУНТЫ"))
        self.acc_list = QListWidget(); self.acc_list.setObjectName("list")
        self.acc_list.setFixedHeight(110); al.addWidget(self.acc_list)
        ar = QHBoxLayout()
        self.acc_input = QLineEdit(); self.acc_input.setPlaceholderText("Новый ник")
        self.acc_add = MCButton("＋")
        self.acc_sel = MCButton("ВЫБРАТЬ")
        self.acc_del = MCButton("УДАЛИТЬ", "danger")
        ar.addWidget(self.acc_input, 1)
        ar.addWidget(self.acc_add); ar.addWidget(self.acc_sel); ar.addWidget(self.acc_del)
        al.addLayout(ar); il.addWidget(acc)

        skin = self.panel(); skl = QVBoxLayout(skin); skl.setContentsMargins(20, 14, 20, 14)
        skl.addWidget(QLabel("СКИНЫ"))
        skin_row = QHBoxLayout()
        self.skin_nick = QLineEdit(); self.skin_nick.setPlaceholderText("Ник для скина (Mojang)")
        self.skin_download = MCButton("СКАЧАТЬ СКИН")
        self.skin_mod = MCButton("УСТАНОВИТЬ МОД ДЛЯ СКИНОВ")
        skin_row.addWidget(self.skin_nick, 1)
        skin_row.addWidget(self.skin_download)
        skin_row.addWidget(self.skin_mod)
        skl.addLayout(skin_row)
        skin_hint = QLabel("Скин скачивается с minotar.net по нику. Мод CustomSkinLoader "
                           "позволяет использовать кастомные скины без лицензии Mojang.")
        skin_hint.setObjectName("muted"); skin_hint.setWordWrap(True)
        skl.addWidget(skin_hint)
        il.addWidget(skin)

        fr = self.panel(); fl = QVBoxLayout(fr); fl.setContentsMargins(20, 14, 20, 14)
        fl.addWidget(QLabel("ДРУЗЬЯ (для коопа)"))
        f2 = QHBoxLayout()
        self.friend_input = QLineEdit(); self.friend_add = MCButton("＋")
        f2.addWidget(self.friend_input, 1); f2.addWidget(self.friend_add)
        fl.addLayout(f2)
        self.friend_list = QListWidget(); self.friend_list.setObjectName("list")
        self.friend_list.setFixedHeight(80)
        fl.addWidget(self.friend_list)
        self.friend_del = MCButton("УБРАТЬ"); fl.addWidget(self.friend_del)
        il.addWidget(fr)

        sec = self.panel(); s2 = QVBoxLayout(sec); s2.setContentsMargins(20, 14, 20, 14)
        s2.addWidget(QLabel("ПРИВАТНОСТЬ И ИЗОЛЯЦИЯ"))
        self.sandbox = QCheckBox("Песочница (не даёт моду читать домашнюю папку)")
        self.strict_urls = QCheckBox("Блокировать внутренние адреса + DNS-пиннинг")
        self.allow_stun = QCheckBox("Разрешить STUN (нужно для коопа)")
        self.link_confirm = QCheckBox("Спрашивать разрешение на подключение")
        self.net_isolate = QCheckBox("Изолировать сеть в одиночной игре")
        self.allow_x11 = QCheckBox("Разрешить X11 (лучше выключить на Wayland)")
        for x in (self.sandbox, self.strict_urls, self.allow_stun, self.link_confirm,
                  self.net_isolate, self.allow_x11):
            s2.addWidget(x)
        self.scan_btn = MCButton("ПРОВЕРИТЬ МОДЫ АКТИВНОЙ СБОРКИ")
        s2.addWidget(self.scan_btn)
        self.sandbox_state = QLabel(""); self.sandbox_state.setObjectName("muted")
        self.sandbox_state.setWordWrap(True); s2.addWidget(self.sandbox_state)
        self.wipe_log = MCButton("ЗАТЕРЕТЬ ЛОГ", "danger"); s2.addWidget(self.wipe_log)
        il.addWidget(sec)

        gpu_panel = self.panel(); gl = QVBoxLayout(gpu_panel); gl.setContentsMargins(20, 14, 20, 14)
        gl.addWidget(QLabel("ВИДЕОКАРТА"))
        self.gpu_info = QLabel("Нажми кнопку, чтобы определить GPU…")
        self.gpu_info.setObjectName("muted"); self.gpu_info.setWordWrap(True)
        gl.addWidget(self.gpu_info)
        gpu_row = QHBoxLayout()
        self.gpu_detect_btn = MCButton("ОПРЕДЕЛИТЬ GPU")
        self.force_nvidia = QCheckBox("Принудительно использовать NVIDIA")
        gpu_row.addWidget(self.gpu_detect_btn); gpu_row.addWidget(self.force_nvidia); gpu_row.addStretch(1)
        gl.addLayout(gpu_row)
        il.addWidget(gpu_panel)

        paths = self.panel(); pl = QHBoxLayout(paths); pl.setContentsMargins(20, 12, 20, 12)
        self.open_versions = MCButton("VERSIONS")
        self.open_data = MCButton("КЭШ")
        self.open_cfg = MCButton("ЛОГИ")
        pl.addWidget(self.open_versions); pl.addWidget(self.open_data)
        pl.addWidget(self.open_cfg); pl.addStretch(1)
        il.addWidget(paths); il.addStretch(1)

        scroll.setWidget(inner); l.addWidget(scroll)
        self._add("settings", w)

    def go(self, key):
        w = self.pages[key]; self.stack.setCurrentWidget(w)
        eff = QGraphicsOpacityEffect(w); w.setGraphicsEffect(eff)
        fade = QPropertyAnimation(eff, b"opacity", self)
        fade.setDuration(230); fade.setStartValue(0); fade.setEndValue(1)
        fade.setEasingCurve(QEasingCurve.OutCubic); fade.start()
        for k, b in self.nav.items():
            b.setChecked(k == key)
        self.be.save({"last_page": key})
        if key == "modpacks" and not self._hits:
            self.be.search_packs(self._src(), self.pack_search.text(), "relevance")
        if key == "mods" and not self._mod_hits:
            mc, loader = self._active_mc_loader()
            self.be.search_mods(self._mod_src(), self.mod_search.text(), mc, loader, "relevance")

    def _tb_press(self, e):
        if e.button() == Qt.LeftButton:
            if self.windowHandle():
                self.windowHandle().startSystemMove()
            e.accept()

    def _src(self):
        return "modrinth" if self.pack_source.currentIndex() == 0 else "curseforge"

    def _mod_src(self):
        return "modrinth" if self.mod_source.currentIndex() == 0 else "curseforge"

    def _active_mc_loader(self):
        inst = config.get_instance(self.be.settings.get("active_instance"))
        if inst:
            return inst["mc"], inst.get("loader") or "fabric"
        return "", "fabric"

    def _wire(self):
        be = self.be
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_close.clicked.connect(self.close)
        self.play_btn.clicked.connect(lambda: be.play())
        self.stop_btn.clicked.connect(be.stop)
        self.ram.valueChanged.connect(self._ram)
        self.act_create.clicked.connect(lambda: self.go("versions"))
        self.act_coop.clicked.connect(lambda: self.go("link"))
        self.act_settings.clicked.connect(lambda: self.go("settings"))
        self.ver_refresh.clicked.connect(be.refresh_versions)
        self.ver_search.textChanged.connect(lambda _: self._search_timer.start())
        self.ver_tabs.currentIndexChanged.connect(self._render_versions)
        self.ver_list.customContextMenuRequested.connect(self._ver_context_menu)
        self.pack_go.clicked.connect(
            lambda: be.search_packs(self._src(), self.pack_search.text(), "relevance"))
        self.pack_search.returnPressed.connect(self.pack_go.click)
        self.pack_source.currentIndexChanged.connect(self.pack_go.click)
        self.pack_import.clicked.connect(self._import)
        self.mod_go.clicked.connect(self._search_mods)
        self.mod_search.returnPressed.connect(self._search_mods)
        self.mod_source.currentIndexChanged.connect(self._search_mods)
        self.inst_play.clicked.connect(lambda: self._inst_act("play"))
        self.inst_sel.clicked.connect(lambda: self._inst_act("select"))
        self.inst_icon.clicked.connect(lambda: self._inst_act("icon"))
        self.inst_folder.clicked.connect(lambda: self._inst_act("folder"))
        self.inst_del.clicked.connect(lambda: self._inst_act("delete"))
        self.link_host_btn.clicked.connect(self._on_link_host)
        self.link_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.link_code.toPlainText()))
        self.link_join_btn.clicked.connect(lambda: be.link_join(self.join_code.text()))
        self.link_stop_btn.clicked.connect(be.link_stop)
        self.tunnel_btn.clicked.connect(be.install_tunnel)
        self.threads.valueChanged.connect(self._thr)
        self.verify.toggled.connect(lambda v: be.save({"verify_files": v}))
        self.java_path.textChanged.connect(lambda t: be.save({"java_path": t}))
        self.java_args.textChanged.connect(
            lambda: be.save({"java_args": self.java_args.toPlainText()}))
        self.preset_btn.clicked.connect(self._apply_game_preset)
        self.cf_key.textChanged.connect(lambda t: be.save({"cf_key": t}))
        self.acc_add.clicked.connect(
            lambda: (be.add_account(self.acc_input.text()), self.acc_input.clear()))
        self.acc_sel.clicked.connect(self._acc_sel)
        self.acc_del.clicked.connect(self._acc_del)
        self.skin_download.clicked.connect(lambda: be.download_skin(self.skin_nick.text()))
        self.skin_mod.clicked.connect(be.install_skin_mod)
        self.gpu_detect_btn.clicked.connect(self._detect_gpu)
        self.force_nvidia.toggled.connect(lambda v: be.save({"force_nvidia": v}))
        self.friend_add.clicked.connect(
            lambda: (be.add_friend(self.friend_input.text()), self.friend_input.clear()))
        self.friend_del.clicked.connect(self._friend_del)
        self.sandbox.toggled.connect(self._sandbox_toggled)
        self.strict_urls.toggled.connect(
            lambda v: (be.save({"strict_urls": v}), net.set_strict(v)))
        self.allow_stun.toggled.connect(lambda v: be.save({"allow_stun": v}))
        self.link_confirm.toggled.connect(lambda v: be.save({"link_confirm": v}))
        self.net_isolate.toggled.connect(lambda v: be.save({"net_isolate": v}))
        self.allow_x11.toggled.connect(lambda v: be.save({"allow_x11": v}))
        self.scan_btn.clicked.connect(self._manual_scan)
        self.wipe_log.clicked.connect(self._wipe_log)
        self.open_versions.clicked.connect(lambda: be.open(config.VERSIONS_DIR))
        self.open_data.clicked.connect(lambda: be.open(config.SHARED_DIR))
        self.open_cfg.clicked.connect(lambda: be.open(config.CONFIG_DIR))
        self.recent.itemDoubleClicked.connect(lambda it: be.play(it.data(Qt.UserRole)))

        be.sig_toast.connect(self._toast)
        be.sig_task.connect(self._task)
        be.sig_log.connect(lambda l: LOG.info("\n".join(l)))
        be.sig_status.connect(self.status.setText)
        be.sig_versions.connect(self._versions)
        be.sig_instances.connect(self._instances)
        be.sig_active.connect(self._active)
        be.sig_game.connect(self._game)
        be.sig_packs.connect(self._packs)
        be.sig_mods.connect(self._mods)
        be.sig_mod_icon.connect(self._mod_icon)
        be.sig_accounts.connect(self._accounts)
        be.sig_friends.connect(self._friends)
        be.sig_icon.connect(self._icon)
        be.sig_link.connect(self._link)
        be.sig_confirm.connect(self._ask_confirm)
        be.sig_scan.connect(self._scan_block)
        be.sig_gpu.connect(self._gpu_info)

    def _ver_context_menu(self, pos):
        item = self.ver_list.itemAt(pos)
        if not item:
            return
        mc = item.data(Qt.UserRole)
        menu = QMenu(self)
        act_create = menu.addAction(f"Создать сборку с {mc}")
        action = menu.exec(self.ver_list.mapToGlobal(pos))
        if action == act_create:
            dlg = CreateDialog(self, mc)
            if dlg.exec() == QDialog.Accepted:
                d = dlg.result_data()
                name = d["name"] or ("Minecraft " + mc)
                self.be.create_instance(name, mc, d["mode"], d["loader"], d["loader_version"])

    def _search_mods(self):
        mc, loader = self._active_mc_loader()
        if not mc:
            self._toast("Сначала выбери активную сборку", "warning")
            return
        self.be.search_mods(self._mod_src(), self.mod_search.text(), mc, loader, "relevance")

    def _on_link_host(self):
        txt = self.link_port.text().strip()
        if not txt:
            self._toast("Введи порт из игры", "warning"); return
        try:
            port = int(txt)
        except ValueError:
            self._toast("Порт должен быть числом", "warning"); return
        self.be.link_host(self.link_name.text(), port)

    def _sandbox_toggled(self, v):
        self.be.save({"sandbox": v})
        if v and not sandbox.available():
            self._toast("Нет bwrap: sudo pacman -S bubblewrap", "error")
        self._sandbox_state()

    def _sandbox_state(self):
        if sandbox.available():
            self.sandbox_state.setText("bubblewrap найден — изоляция доступна.")
        else:
            self.sandbox_state.setText("bubblewrap НЕ найден. Поставь: sudo pacman -S bubblewrap")

    def _apply_game_preset(self):
        self.ram.setValue(6144); self._ram(6144)
        self.java_args.setPlainText(GAME_ARGS_PRESET)
        self.be.save({"java_args": GAME_ARGS_PRESET, "ram_mb": 6144})
        self.net_isolate.setChecked(False)
        self.be.save({"net_isolate": False})
        self._toast("Игровой пресет применён: 6 ГБ + оптимизированный G1GC", "success")

    def _detect_gpu(self):
        self.gpu_info.setText("Определяю GPU…")
        self.be.detect_gpu()

    def _gpu_info(self, info):
        if "error" in info:
            self.gpu_info.setText("Ошибка: " + info["error"])
            return
        renderer = info.get("renderer", "unknown")
        vendor = info.get("vendor", "unknown")
        nvidia = info.get("nvidia", False)
        software = info.get("software", False)
        text = f"Renderer: {renderer}\nVendor: {vendor}\n"
        if software:
            text += "⚠ Программный рендер (llvmpipe) — будет тормозить!\n"
            if nvidia:
                text += "NVIDIA найдена. Включи «Принудительно использовать NVIDIA» или запускай через prime-run."
            else:
                text += "NVIDIA не найдена."
        elif nvidia:
            text += "✓ NVIDIA доступна."
        else:
            text += "✓ GPU работает."
        self.gpu_info.setText(text)

    def _load(self):
        s = self.be.settings
        self.ram.setValue(s["ram_mb"]); self._ram(s["ram_mb"])
        self.threads.setValue(s["threads"]); self._thr(s["threads"])
        self.verify.setChecked(s.get("verify_files", False))
        self.java_path.setText(s.get("java_path", ""))
        self.java_args.setPlainText(s.get("java_args", ""))
        self.cf_key.setText(s.get("cf_key", ""))
        self.sandbox.setChecked(s.get("sandbox", True))
        self.strict_urls.setChecked(s.get("strict_urls", True))
        self.allow_stun.setChecked(s.get("allow_stun", True))
        self.link_confirm.setChecked(s.get("link_confirm", True))
        self.net_isolate.setChecked(s.get("net_isolate", False))
        self.allow_x11.setChecked(s.get("allow_x11", True))
        self.force_nvidia.setChecked(s.get("force_nvidia", False))
        self._sandbox_state()
        self._accounts(s["accounts"])
        self._friends(s.get("friends", []))
        self._instances(config.list_instances())
        self.go(s.get("last_page", "home"))
        self.be.bootstrap_versions()
        QTimer.singleShot(250, self._autopick_active)
        QTimer.singleShot(500, self._detect_gpu)

    def _autopick_active(self):
        s = self.be.settings
        cur = s.get("active_instance")
        if cur and config.get_instance(cur):
            return
        lst = config.list_instances()
        # Берём только ту, в которую реально играли
        played = [i for i in lst if i.get("last_played", 0) > 0]
        if not played:
            return
        last = played[0]["id"]
        s["active_instance"] = last; config.save(s)
        self.be.sig_active.emit(last)
        self._toast("Автоматически выбрана последняя сыгранная сборка", "info")

    def _ram(self, v):
        self.ram_val.setText(f"{v/1024:.1f} ГБ"); self.be.save({"ram_mb": v})

    def _thr(self, v):
        self.threads_val.setText(str(v)); self.be.save({"threads": v})

    def _versions(self, vs):
        self._allv = vs; self._render_versions()

    def _render_versions(self):
        t = ["release", "snapshot", "old_beta", "old_alpha", "all"][self.ver_tabs.currentIndex()]
        q = self.ver_search.text().strip().lower()
        self.ver_list.setUpdatesEnabled(False); self.ver_list.clear()
        n = 0
        for v in self._allv:
            if t != "all" and v["type"] != t:
                continue
            if q and q not in v["id"].lower():
                continue
            it = QListWidgetItem(
                f'{v["id"]}    ·    {v["type"]}    ·    {v.get("releaseTime", "")}')
            it.setData(Qt.UserRole, v["id"])
            self.ver_list.addItem(it); n += 1
        self.ver_list.setUpdatesEnabled(True)
        self.ver_count.setText(str(n))

    def _pixmap(self, path, size):
        pm = QPixmap()
        if path and os.path.exists(path):
            pm.load(path)
        if pm.isNull():
            pm = QPixmap(size, size); pm.fill(QColor("#1b1233"))
            p = QPainter(pm); p.setPen(QColor("#7c3aed"))
            f = p.font(); f.setPointSize(max(10, size // 3)); f.setBold(True); p.setFont(f)
            p.drawText(pm.rect(), Qt.AlignCenter, "◈"); p.end()
        return pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

    def _instances(self, lst):
        self._inst = lst
        self.inst_list.clear(); self.recent.clear()
        for i in lst:
            loader_str = i.get("loader") or "vanilla"
            it = QListWidgetItem(
                QIcon(self._pixmap(i.get("icon"), 40)),
                f'{i["name"]}   ·   {i["mc"]}   ·   {loader_str}   ·   {i["type"]}')
            it.setData(Qt.UserRole, i["id"])
            self.inst_list.addItem(it)
            r = QListWidgetItem(
                QIcon(self._pixmap(i.get("icon"), 32)),
                i["name"] + "   ·   " + i["mc"])
            r.setData(Qt.UserRole, i["id"])
            self.recent.addItem(r)
        self._active(self.be.settings.get("active_instance", ""))

    def _active(self, iid):
        inst = next((i for i in self._inst if i["id"] == iid), None)
        self.hero_name.setText(inst["name"] if inst else "Сборка не выбрана")
        if inst:
            loader_str = inst.get("loader") or "vanilla"
            self.hero_meta.setText(f'{inst["mc"]} · {loader_str} · {inst["type"]}')
        else:
            self.hero_meta.setText("—")
        self.hero_icon.setPixmap(self._pixmap(inst.get("icon") if inst else None, 130))
        self.play_btn.setEnabled(bool(inst))
        if inst:
            self.link_name.setText(inst["name"])

    def _inst_act(self, act):
        it = self.inst_list.currentItem()
        if not it:
            return
        iid = it.data(Qt.UserRole)
        if act == "play":
            self.be.select_instance(iid); self.be.play(iid)
        elif act == "select":
            self.be.select_instance(iid)
        elif act == "folder":
            self.be.open(config.instance_dir(iid))
        elif act == "delete":
            self.be.delete_instance(iid)
        elif act == "icon":
            path, _ = QFileDialog.getOpenFileName(
                self, "Аватар сборки", "",
                "Изображения (*.png *.jpg *.jpeg *.webp *.bmp)")
            if path:
                self.be.set_icon(iid, path)

    def _packs(self, res):
        while self.pack_vbox.count() > 1:
            item = self.pack_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._icon_labels.clear(); self._hits = []
        if not res["ok"]:
            lb = QLabel("Ошибка: " + res["error"])
            lb.setObjectName("muted"); lb.setWordWrap(True)
            self.pack_vbox.insertWidget(0, lb); return
        self._hits = res["hits"]
        for p in self._hits:
            card = self.panel()
            cl = QHBoxLayout(card); cl.setContentsMargins(12, 10, 12, 10); cl.setSpacing(12)
            ico = QLabel(); ico.setFixedSize(56, 56); ico.setObjectName("packicon")
            ico.setPixmap(self._pixmap(None, 56)); cl.addWidget(ico)
            self._icon_labels[p["id"]] = ico
            col = QVBoxLayout(); col.setSpacing(2)
            title = QLabel(p["name"]); title.setObjectName("packtitle")
            meta = QLabel(f'{p["author"]} · ⬇ {p["downloads"]}'); meta.setObjectName("muted")
            desc = QLabel(p["desc"][:170]); desc.setObjectName("muted"); desc.setWordWrap(True)
            col.addWidget(title); col.addWidget(meta); col.addWidget(desc)
            cl.addLayout(col, 1)
            btn = MCButton("УСТАНОВИТЬ", "primary"); btn.setFixedWidth(140)
            btn.clicked.connect(lambda _=False, pp=p: self._install_pack(pp))
            cl.addWidget(btn)
            self.pack_vbox.insertWidget(self.pack_vbox.count() - 1, card)
        self.pack_scroll.verticalScrollBar().setValue(0)

    def _mods(self, res):
        while self.mod_vbox.count() > 1:
            item = self.mod_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._mod_icon_labels = {}
        self._mod_hits = []
        if not res["ok"]:
            lb = QLabel("Ошибка: " + res["error"])
            lb.setObjectName("muted"); lb.setWordWrap(True)
            self.mod_vbox.insertWidget(0, lb); return
        self._mod_hits = res["hits"]
        mc = res.get("mc", "")
        loader = res.get("loader", "")
        info = QLabel(f"Поиск для: {mc} + {loader}")
        info.setObjectName("muted")
        self.mod_vbox.insertWidget(0, info)
        for p in self._mod_hits:
            card = self.panel()
            cl = QHBoxLayout(card); cl.setContentsMargins(12, 10, 12, 10); cl.setSpacing(12)
            ico = QLabel(); ico.setFixedSize(56, 56); ico.setObjectName("packicon")
            ico.setPixmap(self._pixmap(None, 56)); cl.addWidget(ico)
            self._mod_icon_labels[p["id"]] = ico
            col = QVBoxLayout(); col.setSpacing(2)
            title = QLabel(p["name"]); title.setObjectName("packtitle")
            meta = QLabel(f'{p["author"]} · ⬇ {p["downloads"]}'); meta.setObjectName("muted")
            desc = QLabel(p["desc"][:170]); desc.setObjectName("muted"); desc.setWordWrap(True)
            col.addWidget(title); col.addWidget(meta); col.addWidget(desc)
            cl.addLayout(col, 1)
            btn = MCButton("УСТАНОВИТЬ В СБОРКУ", "primary"); btn.setFixedWidth(180)
            btn.clicked.connect(lambda _=False, pp=p: self._install_mod(pp))
            cl.addWidget(btn)
            self.mod_vbox.insertWidget(self.mod_vbox.count() - 1, card)
        self.mod_scroll.verticalScrollBar().setValue(0)

    def _icon(self, key, data):
        lb = self._icon_labels.get(key)
        if not lb:
            return
        pm = QPixmap()
        if pm.loadFromData(data):
            lb.setPixmap(pm.scaled(56, 56, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

    def _mod_icon(self, key, data):
        lb = self._mod_icon_labels.get(key)
        if not lb:
            return
        pm = QPixmap()
        if pm.loadFromData(data):
            lb.setPixmap(pm.scaled(56, 56, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

    def _install_pack(self, p):
        name, ok = QInputDialog.getText(self, "Название", "Название инстанса:", text=p["name"])
        if ok:
            self.be.install_pack(p["source"], p["id"], name)

    def _install_mod(self, p):
        dlg = SelectInstanceDialog(self, p["name"], p)
        if dlg.exec() == QDialog.Accepted and dlg.selected_inst:
            self.be.install_mod(p["source"], p["id"], dlg.selected_inst)

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт", "", "Modpacks (*.mrpack *.zip)")
        if path:
            self.be.import_pack(path)

    def _friends(self, f):
        self.friend_list.clear(); self.friend_list.addItems(f)

    def _friend_del(self):
        it = self.friend_list.currentItem()
        if it:
            self.be.remove_friend(it.text())

    def _accounts(self, accs):
        self.acc_list.clear()
        active = self.be.settings.get("active_account")
        for a in accs:
            mark = "  ✓" if a["nick"] == active else ""
            self.acc_list.addItem(a["nick"] + mark)
        self.acc_label.setText(active or "Player")

    def _acc_sel(self):
        it = self.acc_list.currentItem()
        if it:
            self.be.select_account(it.text().replace("  ✓", ""))

    def _acc_del(self):
        it = self.acc_list.currentItem()
        if not it:
            self._toast("Выбери аккаунт из списка", "warning"); return
        nick = it.text().replace("  ✓", "")
        r = QMessageBox.question(self, "Удаление аккаунта",
                                 f"Удалить аккаунт «{nick}»?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            self.be.remove_account(nick)

    def _ask_confirm(self, d):
        r = QMessageBox.question(self, "Void Link",
                                 f"К тебе подключается друг\nадрес: {d['addr']}\n\nПустить в мир?",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        d["box"]["ok"] = (r == QMessageBox.Yes)
        d["box"]["ev"].set()

    def _scan_block(self, d):
        lines = [f"{jar}: {desc}" for jar, desc, _ in d["crit"][:12]]
        txt = "СКАНЕР НАШЁЛ ОПАСНЫЕ ПАТТЕРНЫ:\n" + "\n".join(lines)
        if d["warn"]:
            txt += f"\n\nПредупреждений: {len(d['warn'])}"
        r = QMessageBox.warning(self, "Проверка модов",
                                txt + "\n\nЗапустить всё равно?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r == QMessageBox.Yes:
            self.be.play(d["iid"], force=True)

    def _manual_scan(self):
        inst = config.get_instance(self.be.settings.get("active_instance"))
        if not inst:
            self._toast("Сборка не выбрана", "warning"); return
        rep = modscan.scan_instance(config.instance_dir(inst["id"]), force=True)
        msg = (f"Файлов: {rep['files']}\nКритичных: {len(rep['crit'])}\n"
               f"Предупреждений: {len(rep['warn'])}\nИнфо: {len(rep['info'])}")
        if rep["crit"]:
            msg += "\n\n" + "\n".join(f"{j}: {d}" for j, d, _ in rep["crit"][:15])
        QMessageBox.information(self, "Результат сканирования", msg)

    def _wipe_log(self):
        security.wipe_file(config.LOG_PATH)
        setup_logging()
        self._toast("Лог затёрт", "success")

    def _link(self, d):
        if d.get("lan"):
            self.link_state.setText(f'Порт игры: {d["lan"]}. Код создан ниже.')
        if d.get("code"):
            self.link_code.setPlainText(d["code"])
            self.link_state.setText(
                f'Хостим на порту {d.get("lan", "?")}. Отправь код другу и не закрывай мир.')
        if d.get("joined"):
            self.link_state.setText(
                f'Подключаемся… Мир появится в «Сетевая игра» (порт {d["joined"]}).')
        if d.get("sas"):
            self.link_sas.setText("Код сверки: " + d["sas"])
            self._toast("Сверь код с другом голосом: " + d["sas"], "warning")
        ev = d.get("ev")
        if ev == "connected":
            self.link_state.setText("Друг подключён ✓")
            self._toast("Друг подключён", "success")
        elif ev == "rejected":
            self.link_state.setText("Подключение отклонено.")
        elif ev == "expired":
            self.link_state.setText("Код истёк — создай новый.")
        elif ev == "closed":
            self.link_state.setText("Друг отключился."); self.link_sas.setText("")
        elif ev == "idle_timeout":
            self.link_state.setText("Отключено по таймауту."); self.link_sas.setText("")
        elif ev == "punch_failed":
            self.link_state.setText("Не удалось пробиться (симметричный NAT). Используй туннель-мод.")
            self._toast("P2P не пробился — нужен туннель-мод", "warning")
        if d.get("state") == "idle":
            self.link_code.clear(); self.link_sas.setText("")

    def _game(self, st):
        if st == "preparing":
            self.play_btn.setEnabled(False)
            self.play_btn.setText("ГОТОВЛЮ…"); self.stop_btn.hide()
        elif st == "running":
            self.play_btn.setEnabled(False)
            self.play_btn.setText("ЗАПУЩЕНО ✓"); self.stop_btn.show()
        else:
            self.play_btn.setEnabled(bool(self._inst))
            self.play_btn.setText("▶  ИГРАТЬ"); self.stop_btn.hide()

    def _task(self, t):
        self.dock.show()
        self.task_title.setText(t["title"])
        self.task_stage.setText(t["stage"])
        if t["status"] == "done":
            self.task_bar.setValue(100); self.task_pct.setText("✓")
            QTimer.singleShot(1500, self.dock.hide)
        elif t["status"] == "error":
            self.task_pct.setText("✕")
            QTimer.singleShot(4500, self.dock.hide)
        else:
            self.task_bar.setValue(t["percent"])
            self.task_pct.setText(str(t["percent"]) + "%")

    def _toast(self, text, kind="info"):
        self.status.setText(text)
        LOG.info("[%s] %s", kind, text)
        alive = []
        for t in self._toasts:
            try:
                if t.parent() is not None:
                    alive.append(t)
            except RuntimeError:
                pass
        self._toasts = alive
        t = Toast(self, text, kind); t.show()
        y = 70 + sum(x.height() + 8 for x in self._toasts)
        self._toasts.append(t)
        t.play(self.width() - t.width() - 24, y)
        def drop():
            if t in self._toasts:
                self._toasts.remove(t)
        QTimer.singleShot(4200, drop)

    def closeEvent(self, e):
        # Безопасное закрытие: останавливаем фоновые операции
        self.be._closing = True
        self.be.stop()
        self.be.link_stop()
        self._search_timer.stop()
        # Даём event loop обработать оставшиеся события
        QApplication.processEvents()
        e.accept()


# ====================== стили ======================
QSS = """
QWidget { color:#e9e4ff; font-family:'JetBrains Mono','DejaVu Sans Mono',monospace; font-size:12px; }
#titlebar { background:#0c0818; border-bottom:2px solid #2e2447; }
#logo { font-size:15px; font-weight:800; color:#c4b5fd; letter-spacing:3px; }
#sub { color:#6f6a8a; font-size:11px; }
#sidebar { background:#0a0616; border-right:2px solid #2e2447; }
#accname { color:#a78bfa; font-weight:700; padding:6px; }
#panel { background:#120c22; border:2px solid #2e2447; }
#hero { background:#130d26; border:2px solid #3a2a5e; }
#packbox { background:transparent; }
#h1 { font-size:22px; font-weight:800; color:#f2ecff; letter-spacing:1px; }
#welcome { font-size:13px; color:#8a86a8; letter-spacing:2px; }
#eye { color:#8b5cf6; font-size:10px; letter-spacing:3px; font-weight:700; }
#muted { color:#8a86a8; }
#packtitle { font-size:13px; font-weight:700; color:#efeaff; }
#avatar, #packicon { background:#1b1233; border:2px solid #3a2d5c; }
QPushButton#nav { text-align:left; padding:10px 12px; color:#9a96b8; background:#130e24; border:2px solid #241c3c; }
QPushButton#nav:hover { background:#1c1533; color:#e9e4ff; }
QPushButton#nav:checked { background:#2a1b4d; color:#fff; border:2px solid #7c3aed; }
QPushButton#win { color:#8a86a8; padding:6px 12px; background:transparent; border:none; }
QPushButton#win:hover { background:#2a1b4d; color:#fff; }
QLineEdit, QComboBox, QPlainTextEdit { background:#0a0616; border:2px solid #2a1f45; padding:8px 10px; selection-background-color:#7c3aed; }
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color:#8b5cf6; }
QComboBox QAbstractItemView { background:#120c22; border:2px solid #2a1f45; selection-background-color:#7c3aed; }
QListWidget#list { background:#0d0919; border:2px solid #241c3c; outline:none; }
QListWidget#list::item { padding:9px 10px; color:#c9c4e0; border-bottom:1px solid #1a1430; }
QListWidget#list::item:hover { background:#1c1533; }
QListWidget#list::item:selected { background:#2a1b4d; color:#fff; }
QScrollArea#scroll { background:#0d0919; border:2px solid #241c3c; }
QSlider::groove:horizontal { height:8px; background:#150f28; border:1px solid #2e2447; }
QSlider::sub-page:horizontal { background:#7c3aed; }
QSlider::handle:horizontal { width:14px; height:18px; margin:-6px 0; background:#c4b5fd; border:2px solid #2a1b4d; }
QCheckBox { spacing:8px; color:#c9c4e0; }
QCheckBox::indicator { width:16px; height:16px; border:2px solid #2a1f45; background:#0a0616; }
QCheckBox::indicator:checked { background:#8b5cf6; border-color:#c4b5fd; }
QScrollBar:vertical { background:#0a0616; width:12px; border:1px solid #241c3c; }
QScrollBar::handle:vertical { background:#3b2a63; min-height:34px; border:1px solid #5b4a8f; }
QScrollBar::handle:vertical:hover { background:#7c3aed; }
QScrollBar::add-line, QScrollBar::sub-line { height:0; }
QMenu { background:#120c22; border:2px solid #2e2447; color:#e9e4ff; }
QMenu::item:selected { background:#2a1b4d; }
"""


# ====================== точка входа ======================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doctor", metavar="MC", help="проверить загрузку версии без GUI")
    ap.add_argument("--selfcheck", action="store_true", help="хэши файлов проекта")
    args = ap.parse_args()

    setup_logging()
    s = config.load()
    net.set_strict(s.get("strict_urls", True))

    if args.doctor:
        print("Проверка", args.doctor, "→", config.SHARED_DIR)
        mojang.doctor(args.doctor, s)
        return

    if args.selfcheck:
        import hashlib
        here = os.path.dirname(os.path.abspath(__file__))
        for sub in ("core",):
            d = os.path.join(here, sub)
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if os.path.isfile(p):
                    print(hashlib.sha256(open(p, "rb").read()).hexdigest(), f"{sub}/{fn}")
        return

    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    be = Backend()
    win = VoidWindow(be)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()