"""
Réservations dans les poutres continues BA - application de bureau (PyQt6).

On dessine la poutre (appuis déplaçables à la souris, armatures et étriers tracés à la souris),
et le logiciel colore la poutre pour montrer où une réservation de dimensions données peut
être faite SANS RENFORT (vert), AVEC RENFORTS (orange) ou n'est PAS possible (rouge).
"""
from __future__ import annotations

import base64
import html
import math
import os
import sys

import numpy as np
from PyQt6.QtCore import Qt, QPointF, QRectF, QThread, QTimer, pyqtSignal, QBuffer, QIODevice
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPolygonF, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget, QButtonGroup, QRadioButton, QMessageBox,
)
from PyQt6.QtGui import QIcon, QPixmap, QDesktopServices
from PyQt6.QtCore import QUrl


def res_path(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import branding as BR
from engine import (Analysis, Checker, LongBar, Opening, Project, StirrupZone, auto_reinforcement,
                    default_project, STATUS_OK, STATUS_REINF, STATUS_NO, STATUS_LABEL)

COL = {STATUS_OK: QColor(46, 160, 67), STATUS_REINF: QColor(238, 150, 28), STATUS_NO: QColor(214, 62, 62)}
HEX = {STATUS_OK: "#2ea043", STATUS_REINF: "#ee961c", STATUS_NO: "#d63e3e"}


# ======================================================================================
# Calcul des cartes en arrière-plan
# ======================================================================================
class MapWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, prj_dict, a, ho, mode, shape, gen, skip=None, parent=None):
        super().__init__(parent)
        self.prj_dict, self.a, self.ho, self.mode, self.shape, self.gen = prj_dict, a, ho, mode, shape, gen
        self.skip = skip

    def run(self):
        try:
            prj = Project.from_dict(self.prj_dict)
            an = Analysis(prj)
            ck = Checker(an)
            ck.others = [(o.xc, o.a, o.ho_eff) for i, o in enumerate(prj.openings) if i != self.skip]
            m = "auto" if self.mode == "auto" else "center"
            xs, st = ck.scan(self.a, self.ho, 0.05, mode=m, shape=self.shape)
            hx, hg, hr = ck.max_height_curves(self.a, 0.1, mode=m, shape=self.shape)
            lx, ag, ar = ck.max_length_curves(self.ho, 0.1, mode=m, shape=self.shape)
            self.done.emit(dict(gen=self.gen, xs=xs, st=st, hx=hx, hg=hg, hr=hr, lx=lx, ag=ag, ar=ar,
                                a=self.a, ho=self.ho, ok=True))
        except Exception as e:  # pragma: no cover
            self.done.emit(dict(gen=self.gen, ok=False, err=repr(e)))


# ======================================================================================
# Dessin
# ======================================================================================
class Canvas(QWidget):
    xcChanged = pyqtSignal(float)
    spansChanged = pyqtSignal(list)
    barDrawn = pyqtSignal(str, float, float)
    openingClicked = pyqtSignal(int)
    stirrupDrawn = pyqtSignal(float, float)

    ML, MR = 74, 30
    EL_TOP = 56

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumSize(780, 700)
        self.fit = True            # ajusté à la largeur de la fenêtre
        self.zoom = 100.0          # px par mètre quand fit = False
        self.true_scale = True     # échelle réelle : même échelle en x et en y
        self.prj = None
        self.an = None
        self.maps = None
        self.res = None
        self.xc = 3.0
        self.tool = "select"
        self.drag = None            # ("support", k) | ("draw", x0, x1)
        self.hover_x = None
        self.shape = "rect"
        self.openings_res = []
        self.sel_open = None
        self.probe = None
        self._open_rects = []

    def set_model(self, prj, an):
        self.prj, self.an = prj, an
        self.update_size()

    # ---- conversions
    def _sw(self, k):
        """largeur (m) de l'appui k"""
        return self.prj.support_w[k] / 1000.0

    def geo_len(self):
        """longueur dessinée (m) : longueur de calcul + largeur de tous les appuis"""
        return self.an.Ltot + sum(self.prj.support_w[:len(self.an.xsup)]) / 1000.0

    def _off(self, k):
        """décalage géométrique (m) des points de la travée k : appui 0 + appuis intérieurs à gauche de la travée"""
        return sum(self._sw(j) for j in range(0, min(k, len(self.prj.spans) - 1) + 1))

    def _sx(self):
        if self.fit:
            return (self.width() - self.ML - self.MR) / max(self.geo_len(), 1e-6)
        return self.zoom

    def hp(self):
        """Hauteur de poutre en pixels : échelle réelle (1 mm en y = 1 mm en x) ou exagérée."""
        if self.prj is None:
            return 190
        if self.true_scale:
            return max(24.0, self.prj.h / 1000.0 * self._sx())
        return 190.0

    def total_height(self):
        return int(self.EL_TOP + self.hp() + 454 + 90 + 40)

    def update_size(self):
        if self.an is None:
            return
        w = 0 if self.fit else int(self.ML + self.MR + self.geo_len() * self.zoom)
        self.setMinimumSize(max(w, 700), self.total_height())
        self.updateGeometry()
        self.update()

    def zoom_by(self, f):
        if self.an is None:
            return
        if self.fit:
            self.zoom = self._sx()
            self.fit = False
        self.zoom = min(max(self.zoom * f, 20.0), 2000.0)
        self.update_size()

    def set_fit(self):
        self.fit = True
        self.update_size()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.fit and self.an is not None:
            self.setMinimumHeight(self.total_height())

    def _span_idx(self, x, end=False):
        xs = self.an.xsup
        n = len(xs) - 1
        for k in range(n):
            if (x <= xs[k + 1] + 1e-9) if end else (x < xs[k + 1] - 1e-9):
                return k
        return n - 1

    def X(self, x, k=None, end=False):
        """x : abscisse de calcul (m, appuis = discontinuités sans longueur) ; k : travée (sinon déduite).
        end=True : un point situé sur un appui est rattaché à la travée de gauche."""
        if k is None:
            k = self._span_idx(x, end)
        return self.ML + (x + self._off(k)) * self._sx()

    def xfrom(self, px):
        """pixel -> abscisse de calcul ; un clic sur un appui donne l'abscisse de l'appui"""
        g = (px - self.ML) / self._sx() - self._sw(0)
        xs = self.an.xsup
        n = len(xs) - 1
        acc = 0.0
        for k in range(1, n):
            if g < xs[k] + acc:
                return g - acc
            if g < xs[k] + acc + self._sw(k):
                return float(xs[k])
            acc += self._sw(k)
        return g - acc

    # ---- événements souris
    def _near_support(self, pos):
        if self.an is None:
            return None
        yb = self.EL_TOP + self.hp()
        if not (yb - 4 <= pos.y() <= yb + 34):
            return None
        for k in range(1, len(self.an.xsup)):
            if k < len(self.an.xsup) - 1:
                cx = self.X(self.an.xsup[k], k) - self._sw(k) * self._sx() / 2
            else:
                cx = self.X(self.an.xsup[k], k - 1, True) + self._sw(k) * self._sx() / 2
            half = max(8.0, self._sw(k) * self._sx() / 2)
            if abs(pos.x() - cx) < half + 3:
                return k
        return None

    def mousePressEvent(self, ev):
        if self.an is None or ev.button() != Qt.MouseButton.LeftButton:
            return
        pos = ev.position()
        x = min(max(self.xfrom(pos.x()), 0.0), self.an.Ltot)
        if self.tool == "select":
            for rect, i in self._open_rects:
                if rect.contains(pos):
                    self.openingClicked.emit(i)
                    return
            k = self._near_support(pos)
            if k is not None:
                self.drag = ("support", k)
                return
            self.xc = round(x / 0.05) * 0.05
            self.xcChanged.emit(self.xc)
        else:
            x = round(x / 0.1) * 0.1
            self.drag = ("draw", x, x)
        self.update()

    def mouseMoveEvent(self, ev):
        if self.an is None:
            return
        pos = ev.position()
        x = min(max(self.xfrom(pos.x()), 0.0), self.an.Ltot)
        self.hover_x = x
        if self.drag and self.drag[0] == "support":
            k = self.drag[1]
            xs = self.an.xsup
            nx = round(x / 0.05) * 0.05
            L = list(self.prj.spans)
            lo = xs[k - 1] + 1.0
            if k < len(L):
                hi = xs[k + 1] - 1.0
                nx = min(max(nx, lo), hi)
                L[k - 1] = round(nx - xs[k - 1], 3)
                L[k] = round(xs[k + 1] - nx, 3)
            else:
                nx = max(nx, lo)
                L[k - 1] = round(nx - xs[k - 1], 3)
            self.spansChanged.emit(L)
        elif self.drag and self.drag[0] == "draw":
            self.drag = ("draw", self.drag[1], round(x / 0.1) * 0.1)
            self.update()
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._near_support(pos) is not None
                           else (Qt.CursorShape.CrossCursor if self.tool != "select" else Qt.CursorShape.ArrowCursor))
            self.update()

    def mouseReleaseEvent(self, ev):
        if self.drag and self.drag[0] == "draw":
            _, x0, x1 = self.drag
            x0, x1 = min(x0, x1), max(x0, x1)
            self.drag = None
            if x1 - x0 >= 0.2:
                if self.tool in ("bar_bot", "bar_top"):
                    self.barDrawn.emit("bot" if self.tool == "bar_bot" else "top", x0, x1)
                elif self.tool == "stirrup":
                    self.stirrupDrawn.emit(x0, x1)
        self.drag = None
        self.update()

    # ---- dessin
    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_all(qp)

    def paint_all(self, qp):
        qp.fillRect(self.rect(), QColor(255, 255, 255))
        qp.setFont(QFont("Segoe UI", 8))
        if self.an is None:
            return
        self.draw_elevation(qp)
        self.draw_band(qp)
        self.draw_M(qp)
        self.draw_V(qp)
        self.draw_amax(qp)

    def render_pixmap(self):
        pm = QPixmap(self.size())
        pm.fill(QColor(255, 255, 255))
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_all(qp)
        qp.end()
        return pm

    def _grid_x(self, qp, y0, y1):
        qp.setPen(QPen(QColor(225, 225, 225), 1))
        step = 1.0 if self.an.Ltot <= 15 else 2.0
        x = 0.0
        while x <= self.an.Ltot + 1e-6:
            qp.drawLine(int(self.X(x)), int(y0), int(self.X(x)), int(y1))
            x += step

    def draw_elevation(self, qp):
        p, an = self.prj, self.an
        sx = self._sx()
        top, hp = self.EL_TOP, self.hp()
        bot = top + hp
        sy = hp / p.h
        x_left, x_right = self.X(0), self.X(an.Ltot, None, True)
        qp.setPen(QColor(60, 60, 60))
        f = QFont("Segoe UI", 10, QFont.Weight.Bold)
        qp.setFont(f)
        qp.drawText(int(self.ML), 18, "Élévation de la poutre à l'échelle" + (" (x = y)" if self.true_scale else " (hauteur exagérée)") + "  -  clic = position, ↔ appuis déplaçables")
        qp.setFont(QFont("Segoe UI", 8))
        # corps de poutre
        qp.setPen(QPen(QColor(70, 70, 70), 1.6))
        qp.setBrush(QBrush(QColor(238, 238, 238)))
        qp.drawRect(QRectF(x_left, top, x_right - x_left, hp))
        # teintes des zones (pour la réservation courante)
        if self.maps and self.maps.get("ok"):
            xs, st = self.maps["xs"], self.maps["st"]
            i = 0
            while i < len(xs):
                j = i
                while j + 1 < len(xs) and st[j + 1] == st[i]:
                    j += 1
                c = QColor(COL[int(st[i])])
                c.setAlpha(110)
                xa = max(0.0, xs[i] - 0.025)
                xb = min(an.Ltot, xs[j] + 0.025)
                qp.fillRect(QRectF(self.X(xa), top + 1, self.X(xb) - self.X(xa), hp - 1), c)
                i = j + 1
            # silhouettes de hauteur max
            hx, hg, hr = self.maps["hx"], self.maps["hg"], self.maps["hr"]
            mid = top + hp / 2
            for arr, colr in ((hr, COL[STATUS_REINF]), (hg, COL[STATUS_OK])):
                if arr.max() <= 0:
                    continue
                poly_top = [QPointF(self.X(x), mid - v * sy / 2) for x, v in zip(hx, arr) if v > 0]
                poly_bot = [QPointF(self.X(x), mid + v * sy / 2) for x, v in zip(hx, arr) if v > 0]
                # segments continus
                seg = []
                for x, v in zip(hx, arr):
                    if v > 0:
                        seg.append((x, v))
                    else:
                        self._poly(qp, seg, mid, sy, colr)
                        seg = []
                self._poly(qp, seg, mid, sy, colr)
        # étriers
        qp.setPen(QPen(QColor(150, 150, 150, 120), 1))
        for z in an.stir:
            n = max(1, int((z.x1 - z.x0) * 1000.0 / z.spacing))
            if (z.spacing / 1000.0) * sx < 3:
                c = QColor(120, 120, 120, 50)
                qp.fillRect(QRectF(self.X(z.x0), top + 5, self.X(z.x1, None, True) - self.X(z.x0), hp - 10), c)
            else:
                xx = z.x0 + 0.5 * z.spacing / 1000.0
                while xx < z.x1 - 1e-6:
                    qp.drawLine(int(self.X(xx)), int(top + 6), int(self.X(xx)), int(bot - 6))
                    xx += z.spacing / 1000.0
        # armatures longitudinales
        for br in an.bars:
            dep = an.d_row(br.row, br.dmax) * sy
            y = top + dep if br.face == "top" else bot - dep
            col = QColor(30, 90, 200) if br.face == "top" else QColor(200, 40, 40)
            qp.setPen(QPen(col, 2.2))
            qp.drawLine(int(self.X(br.x0)), int(y), int(self.X(br.x1, None, True)), int(y))
            qp.setFont(QFont("Segoe UI", 7))
            qp.setPen(col)
            yy = y - 3 if br.face == "bot" else y + 11
            qp.drawText(int((self.X(br.x0) + self.X(br.x1, None, True)) / 2 - 22), int(yy), br.label)
        qp.setFont(QFont("Segoe UI", 8))
        # appuis (largeur réelle)
        nsp = len(an.xsup)
        for k, xsup in enumerate(an.xsup):
            wpx = max(8.0, p.support_w[k] / 1000.0 * sx)
            if k == 0:
                cx = self.X(0, 0) - wpx / 2
            elif k < nsp - 1:
                cx = self.X(xsup, k) - wpx / 2
            else:
                cx = self.X(xsup, k - 1, True) + wpx / 2
            rect = QRectF(cx - wpx / 2, bot, wpx, 24)
            qp.setPen(QPen(QColor(40, 40, 40), 1.4))
            qp.setBrush(QBrush(QColor(150, 150, 150), Qt.BrushStyle.BDiagPattern))
            qp.drawRect(rect)
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.setPen(QColor(60, 60, 60))
            qp.drawText(int(cx - 22), int(bot + 38), f"A{k+1} ({p.support_w[k]:.0f})")
        # cotes des travées
        qp.setPen(QColor(60, 60, 60))
        for k, L in enumerate(p.spans):
            xm = self.X(an.xsup[k] + L / 2, k)
            qp.drawText(int(xm - 30), int(bot + 54), f"L = {L:.2f} m")
        qp.drawText(int(self.ML - 68), int(top + 12), f"h = {p.h:.0f}")
        qp.drawText(int(self.ML - 68), int(top + 26), f"b = {p.b:.0f}")
        if p.is_T:
            qp.drawText(int(self.ML - 68), int(top + 40), f"bf = {p.bf:.0f}")
            qp.drawText(int(self.ML - 68), int(top + 54), f"hf = {p.hf:.0f}")
            qp.setPen(QPen(QColor(90, 90, 90), 1.2, Qt.PenStyle.DashLine))
            qp.drawLine(int(x_left), int(top + p.hf * sy), int(x_right), int(top + p.hf * sy))
            qp.setPen(QColor(60, 60, 60))
        # réservations enregistrées
        self._open_rects = []
        for i, r in enumerate(self.openings_res):
            op = p.openings[i]
            cx = self.X(op.xc)
            wpx = r.a / 1000.0 * sx
            rect = QRectF(cx - wpx / 2, top + r.h1 * sy, wpx, r.ho * sy)
            sel = (i == self.sel_open)
            if sel and r.info.get("hw_mm", 0) > 0 and "marge_haut_mm" in r.info:
                hw = r.info["hw_mm"]
                qp.setPen(QPen(QColor(46, 160, 67), 1.2, Qt.PenStyle.DashLine))
                qp.setBrush(Qt.BrushStyle.NoBrush)
                qp.drawRect(QRectF(cx - hw / 2000.0 * sx, top + r.info["marge_haut_mm"] * sy, hw / 1000.0 * sx, hw * sy))
            qp.setPen(QPen(COL[r.status].darker(130), 3.6 if sel else 2.2))
            qp.setBrush(QBrush(QColor(255, 255, 255, 235)))
            if op.shape == "circle":
                qp.drawEllipse(rect)
            else:
                qp.drawRect(rect)
            qp.setPen(QColor(30, 30, 30))
            qp.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            qp.drawText(int(cx - 10), int(top + r.h1 * sy - 3), op.name or f"R{i+1}")
            qp.setFont(QFont("Segoe UI", 8))
            self._open_rects.append((rect.adjusted(-3, -3, 3, 3), i))
        # réservation d'essai (non enregistrée)
        r = self.probe
        if r is not None:
            info = r.info or {}
            cx = self.X(self.xc)
            if info.get("hw_mm", 0) > 0 and "marge_haut_mm" in info:
                hw = info["hw_mm"]
                qp.setPen(QPen(QColor(46, 160, 67), 1.2, Qt.PenStyle.DashLine))
                qp.setBrush(Qt.BrushStyle.NoBrush)
                qp.drawRect(QRectF(cx - hw / 2000.0 * sx, top + info["marge_haut_mm"] * sy, hw / 1000.0 * sx, hw * sy))
            wpx = r.a / 1000.0 * sx
            rect = QRectF(cx - wpx / 2, top + r.h1 * sy, wpx, r.ho * sy)
            qp.setPen(QPen(COL[r.status].darker(130), 2.2, Qt.PenStyle.DashLine))
            qp.setBrush(QBrush(QColor(255, 255, 255, 200)))
            if self.shape == "circle":
                qp.drawEllipse(rect)
            else:
                qp.drawRect(rect)
            qp.setPen(QColor(90, 90, 90))
            qp.drawText(int(cx - 14), int(top + r.h1 * sy - 3), "essai")
        # prévisualisation du tracé
        if self.drag and self.drag[0] == "draw":
            _, x0, x1 = self.drag
            a_, b_ = min(x0, x1), max(x0, x1)
            col = {"bar_bot": QColor(200, 40, 40), "bar_top": QColor(30, 90, 200), "stirrup": QColor(90, 90, 90)}[self.tool]
            qp.setPen(QPen(col, 2, Qt.PenStyle.DashLine))
            if self.tool == "bar_bot":
                qp.drawLine(int(self.X(a_)), int(bot - 14), int(self.X(b_)), int(bot - 14))
            elif self.tool == "bar_top":
                qp.drawLine(int(self.X(a_)), int(top + 14), int(self.X(b_)), int(top + 14))
            else:
                qp.setBrush(QBrush(QColor(90, 90, 90, 40)))
                qp.drawRect(QRectF(self.X(a_), top + 5, self.X(b_) - self.X(a_), hp - 10))
            qp.drawText(int(self.X(a_)), int(top - 6), f"{a_:.1f} → {b_:.1f} m")
        if self.hover_x is not None and not self.drag:
            qp.setPen(QPen(QColor(150, 150, 150), 1, Qt.PenStyle.DotLine))
            qp.drawLine(int(self.X(self.hover_x)), int(top - 20), int(self.X(self.hover_x)), int(top))
            qp.setPen(QColor(110, 110, 110))
            qp.drawText(int(self.X(self.hover_x) + 4), int(top - 8), f"x = {self.hover_x:.2f} m")

    def _poly(self, qp, seg, mid, sy, colr):
        if len(seg) < 1:
            return
        pts_t = [QPointF(self.X(x, None, True), mid - v * sy / 2) for x, v in seg]
        pts_b = [QPointF(self.X(x, None, True), mid + v * sy / 2) for x, v in reversed(seg)]
        poly = QPolygonF(pts_t + pts_b)
        c = QColor(colr)
        c.setAlpha(60)
        qp.setBrush(QBrush(c))
        qp.setPen(QPen(colr, 1.4))
        qp.drawPolygon(poly)

    def draw_band(self, qp):
        y0 = self.EL_TOP + self.hp() + 88
        qp.setPen(QColor(60, 60, 60))
        qp.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        if self.maps and self.maps.get("ok"):
            lab = ("cercle Ø" if self.shape == "circle" else "réservation") + f" {self.maps['a']:.0f}" + \
                  ("" if self.shape == "circle" else f" × {self.maps['ho']:.0f}") + " mm"
        else:
            lab = "calcul en cours…"
        qp.drawText(int(self.ML), int(y0 - 6), f"Où peut-on placer une {lab} ?")
        qp.setFont(QFont("Segoe UI", 8))
        h = 22
        qp.setPen(QPen(QColor(120, 120, 120), 1))
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.drawRect(QRectF(self.X(0), y0, self.X(self.an.Ltot, None, True) - self.X(0), h))
        if self.maps and self.maps.get("ok"):
            xs, st = self.maps["xs"], self.maps["st"]
            for x, s in zip(xs, st):
                xa, xb = max(0.0, x - 0.025), min(self.an.Ltot, x + 0.025)
                qp.fillRect(QRectF(self.X(xa), y0, self.X(xb) - self.X(xa) + 0.6, h), COL[int(s)])
        # légende
        lx = self.ML
        for s in (STATUS_OK, STATUS_REINF, STATUS_NO):
            qp.fillRect(QRectF(lx, y0 + h + 8, 12, 12), COL[s])
            qp.setPen(QColor(50, 50, 50))
            qp.drawText(int(lx + 17), int(y0 + h + 18), STATUS_LABEL[s])
            lx += 215
        # position courante
        cx = self.X(self.xc)
        qp.setPen(QPen(QColor(20, 20, 20), 1.6))
        qp.drawLine(int(cx), int(self.EL_TOP - 14), int(cx), int(y0 + h + 4))

    def _panel_frame(self, qp, y0, y1, title):
        qp.setPen(QPen(QColor(150, 150, 150), 1))
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.drawRect(QRectF(self.X(0), y0, self.X(self.an.Ltot, None, True) - self.X(0), y1 - y0))
        self._grid_x(qp, y0, y1)
        qp.setPen(QColor(50, 50, 50))
        qp.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        qp.drawText(int(self.ML), int(y0 - 5), title)
        qp.setFont(QFont("Segoe UI", 8))

    def draw_M(self, qp):
        an = self.an
        y0 = self.EL_TOP + self.hp() + 162
        y1 = y0 + 120
        self._panel_frame(qp, y0, y1, "Moments fléchissants ELU (kN·m) - enveloppe, positif vers le bas")
        vmin, vmax = min(an.Mmin.min(), 0.0), max(an.Mmax.max(), 0.0)
        rng = max(vmax - vmin, 1e-6)
        Y = lambda v: y0 + 8 + (v - vmin) / rng * (y1 - y0 - 16)
        for arr, col in ((an.Mmax, QColor(30, 90, 200, 70)), (an.Mmin, QColor(200, 60, 60, 70))):
            pts = [QPointF(self.X(x, int(kk)), Y(v)) for x, kk, v in zip(an.xs, an.sid, arr)]
            poly = QPolygonF(pts + [QPointF(self.X(an.xs[-1], int(an.sid[-1])), Y(0)), QPointF(self.X(an.xs[0], 0), Y(0))])
            qp.setBrush(QBrush(col))
            qp.setPen(QPen(col.darker(150), 1))
            qp.drawPolygon(poly)
        qp.setPen(QPen(QColor(80, 80, 80), 1))
        qp.drawLine(int(self.X(0)), int(Y(0)), int(self.X(an.Ltot, None, True)), int(Y(0)))
        qp.setPen(QColor(30, 90, 200))
        qp.drawText(int(self.X(0) + 4), int(y1 - 4), f"M+ max = {an.Mmax.max():.0f}")
        qp.setPen(QColor(200, 60, 60))
        qp.drawText(int(self.X(0) + 110), int(y1 - 4), f"M- min = {an.Mmin.min():.0f}")
        qp.setPen(QPen(QColor(20, 20, 20), 1.2))
        qp.drawLine(int(self.X(self.xc)), int(y0), int(self.X(self.xc)), int(y1))

    def draw_V(self, qp):
        an = self.an
        y0 = self.EL_TOP + self.hp() + 314
        y1 = y0 + 110
        self._panel_frame(qp, y0, y1, "Efforts tranchants ELU (kN) - enveloppe")
        vmin, vmax = min(an.Vmin.min(), 0.0), max(an.Vmax.max(), 0.0)
        rng = max(vmax - vmin, 1e-6)
        Y = lambda v: y1 - 8 - (v - vmin) / rng * (y1 - y0 - 16)
        for arr, col in ((an.Vmax, QColor(30, 150, 90, 70)), (an.Vmin, QColor(200, 120, 30, 70))):
            pts = [QPointF(self.X(x, int(kk)), Y(v)) for x, kk, v in zip(an.xs, an.sid, arr)]
            poly = QPolygonF(pts + [QPointF(self.X(an.xs[-1], int(an.sid[-1])), Y(0)), QPointF(self.X(an.xs[0], 0), Y(0))])
            qp.setBrush(QBrush(col))
            qp.setPen(QPen(col.darker(150), 1))
            qp.drawPolygon(poly)
        qp.setPen(QPen(QColor(80, 80, 80), 1))
        qp.drawLine(int(self.X(0)), int(Y(0)), int(self.X(an.Ltot, None, True)), int(Y(0)))
        qp.setPen(QColor(60, 60, 60))
        qp.drawText(int(self.X(0) + 4), int(y1 - 4), f"V max = {max(abs(vmin), vmax):.0f}")
        qp.setPen(QPen(QColor(20, 20, 20), 1.2))
        qp.drawLine(int(self.X(self.xc)), int(y0), int(self.X(self.xc)), int(y1))

    def draw_amax(self, qp):
        an = self.an
        y0 = self.EL_TOP + self.hp() + 454
        y1 = y0 + 90
        ho = self.maps["ho"] if self.maps and self.maps.get("ok") else 0
        self._panel_frame(qp, y0, y1, f"Longueur maximale de réservation de hauteur {ho:.0f} mm (mm)")
        if not (self.maps and self.maps.get("ok")):
            return
        lx, ag, ar = self.maps["lx"], self.maps["ag"], self.maps["ar"]
        amax = max(float(ar.max()), 1.0)
        Y = lambda v: y1 - 6 - v / amax * (y1 - y0 - 14)
        for arr, colr in ((ar, COL[STATUS_REINF]), (ag, COL[STATUS_OK])):
            pts = [QPointF(self.X(x), Y(v)) for x, v in zip(lx, arr)]
            poly = QPolygonF([QPointF(self.X(lx[0]), Y(0))] + pts + [QPointF(self.X(lx[-1]), Y(0))])
            c = QColor(colr)
            c.setAlpha(100)
            qp.setBrush(QBrush(c))
            qp.setPen(QPen(colr, 1.2))
            qp.drawPolygon(poly)
        qp.setPen(QColor(60, 60, 60))
        qp.drawText(int(self.X(0) + 4), int(y0 + 12), f"max = {amax:.0f} mm")
        qp.setPen(QPen(QColor(20, 20, 20), 1.2))
        qp.drawLine(int(self.X(self.xc)), int(y0), int(self.X(self.xc)), int(y1))


# ======================================================================================
# Boîtes de dialogue de tracé
# ======================================================================================
class BarDialog(QDialog):
    def __init__(self, face, x0, x1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Armatures longitudinales " + ("inférieures" if face == "bot" else "supérieures"))
        form = QFormLayout(self)
        form.addRow(QLabel(f"De x = {x0:.2f} m à x = {x1:.2f} m"))
        self.n = QSpinBox(); self.n.setRange(1, 30); self.n.setValue(3)
        self.dia = QComboBox()
        for d in (8, 10, 12, 14, 16, 20, 25, 32, 40):
            self.dia.addItem(f"HA{d}", d)
        self.dia.setCurrentIndex(5)
        self.row = QSpinBox(); self.row.setRange(1, 3); self.row.setValue(1)
        form.addRow("Nombre de barres", self.n)
        form.addRow("Diamètre", self.dia)
        form.addRow("Lit (1 = contre la face)", self.row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)


class StirrupDialog(QDialog):
    def __init__(self, x0, x1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Armatures transversales (étriers)")
        form = QFormLayout(self)
        form.addRow(QLabel(f"De x = {x0:.2f} m à x = {x1:.2f} m"))
        self.dia = QComboBox()
        for d in (6, 8, 10, 12):
            self.dia.addItem(f"HA{d}", d)
        self.dia.setCurrentIndex(1)
        self.sp = QSpinBox(); self.sp.setRange(50, 500); self.sp.setSingleStep(25); self.sp.setValue(150); self.sp.setSuffix(" mm")
        self.legs = QSpinBox(); self.legs.setRange(2, 8); self.legs.setSingleStep(2); self.legs.setValue(2)
        form.addRow("Diamètre", self.dia)
        form.addRow("Espacement", self.sp)
        form.addRow("Brins verticaux par cours", self.legs)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)


# ======================================================================================
# Fenêtre principale
# ======================================================================================
def dspin(lo, hi, val, dec=1, step=1.0, suffix=""):
    s = QDoubleSpinBox()
    s.setRange(lo, hi); s.setDecimals(dec); s.setSingleStep(step); s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Réservations dans les poutres continues - zones sans renfort")
        self.resize(1560, 960)
        self.prj = default_project()
        self.an = None
        self.ck = None
        self.res = None
        self.maps = None
        self.sel_open = None
        self._loading = False
        self.gen = 0
        self.worker = None
        self.pending = False
        self.path = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.start_worker)
        self._build_ui()
        self.load_project_into_ui()
        self.on_edit()

    # ================================================================ UI
    def _build_ui(self):
        # --- barre haute : réservation d'essai / sélectionnée
        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(6, 4, 6, 0)
        self.shape = QComboBox(); self.shape.addItems(["Rectangulaire", "Circulaire"])
        self.sa = dspin(50, 4000, 300, 0, 25, " mm")
        self.sho = dspin(50, 2000, 200, 0, 10, " mm")
        self.lab_a = QLabel("Longueur a"); self.lab_h = QLabel("Hauteur h")
        self.mode = QComboBox(); self.mode.addItems(["Auto (meilleure position)", "Centrée", "Manuelle"])
        self.sh1 = dspin(0, 2000, 200, 0, 10, " mm")
        self.sh1.setEnabled(False)
        self.sxc = dspin(0, 200, 2.8, 2, 0.05, " m")
        self.lab_sel = QLabel("<b>Réservation d'essai :</b>")
        for w_ in (self.lab_sel, self.shape, self.lab_a, self.sa, self.lab_h, self.sho,
                   QLabel("Position verticale"), self.mode, QLabel("h1 (haut)"), self.sh1, QLabel("x"), self.sxc):
            tl.addWidget(w_)
        self.btn_add_open = QPushButton("➕ Ajouter à la poutre")
        self.btn_add_open.clicked.connect(self.add_opening)
        self.btn_new_open = QPushButton("Nouvelle (désélectionner)")
        self.btn_new_open.clicked.connect(lambda: self.select_opening(None))
        tl.addWidget(self.btn_add_open)
        tl.addWidget(self.btn_new_open)
        tl.addStretch(1)

        tools = QWidget()
        tt = QHBoxLayout(tools)
        tt.setContentsMargins(6, 0, 6, 4)
        tt.addWidget(QLabel("<b>Outil :</b>"))
        self.toolgroup = QButtonGroup(self)
        for key, lab in (("select", "↖ Position / appuis"), ("bar_bot", "━ Armatures inf."),
                         ("bar_top", "━ Armatures sup."), ("stirrup", "┃ Étriers")):
            rb = QRadioButton(lab)
            rb.setProperty("tool", key)
            if key == "select":
                rb.setChecked(True)
            self.toolgroup.addButton(rb)
            tt.addWidget(rb)
        self.toolgroup.buttonClicked.connect(self.on_tool)
        tt.addSpacing(14)
        for lab, fn in (("Zoom −", lambda: self.canvas.zoom_by(1 / 1.25)), ("Zoom +", lambda: self.canvas.zoom_by(1.25)),
                        ("Ajuster", lambda: self.canvas.set_fit())):
            b_ = QPushButton(lab); b_.clicked.connect(fn); tt.addWidget(b_)
        self.cb_scale = QCheckBox("Échelle réelle (x = y)")
        self.cb_scale.setChecked(True)
        self.cb_scale.toggled.connect(self.on_scale)
        tt.addWidget(self.cb_scale)
        b1 = QPushButton("Ferraillage de départ"); b1.clicked.connect(self.do_auto_reinf)
        b2 = QPushButton("Effacer ferraillage"); b2.clicked.connect(self.clear_reinf)
        tt.addWidget(b1); tt.addWidget(b2)
        tt.addStretch(1)

        self.canvas = Canvas()
        self.canvas.xcChanged.connect(self.on_canvas_xc)
        self.canvas.spansChanged.connect(self.on_canvas_spans)
        self.canvas.barDrawn.connect(self.on_bar_drawn)
        self.canvas.stirrupDrawn.connect(self.on_stirrup_drawn)
        self.canvas.openingClicked.connect(self.select_opening)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(True)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(top)
        cl.addWidget(tools)
        cl.addWidget(self.scroll, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_beam(), "Poutre")
        self.tabs.addTab(self._tab_supports(), "Appuis")
        self.tabs.addTab(self._tab_bars(), "Armatures long.")
        self.tabs.addTab(self._tab_stir(), "Étriers")
        self.tabs.addTab(self._tab_openings(), "Réservations")
        self.result = QTextBrowser()
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.result, 1)

        sp = QSplitter()
        sp.addWidget(self.tabs)
        sp.addWidget(center)
        sp.addWidget(right)
        sp.setStretchFactor(0, 0); sp.setStretchFactor(1, 1); sp.setStretchFactor(2, 0)
        self.tabs.setMinimumWidth(520); right.setMinimumWidth(400)
        sp.setSizes([540, 760, 420])
        self.setCentralWidget(sp)

        mb = self.menuBar()
        fm = mb.addMenu("Fichier")
        for lab, fn in (("Nouveau", self.new_project), ("Ouvrir…", self.open_project), ("Enregistrer", self.save_project),
                        ("Enregistrer sous…", self.save_project_as), (None, None),
                        ("Exporter l'image (PNG)…", self.export_png), ("Exporter la note de calcul (HTML)…", self.export_html),
                        (None, None), ("Quitter", self.close)):
            if lab is None:
                fm.addSeparator()
            else:
                a_ = fm.addAction(lab); a_.triggered.connect(fn)
        hm = mb.addMenu("Aide")
        for lab, fn in (("Manuel d'utilisation (PDF)", self.open_manual), ("Télécharger le code source", lambda: self.open_url(BR.CODE_ZIP_URL)),
                        ("Télécharger l'exécutable (Windows)", lambda: self.open_url(BR.EXE_URL)),
                        ("Page du projet (GitHub)", lambda: self.open_url(BR.REPO_URL)), (None, None),
                        ("À propos / Contact…", self.about)):
            if lab is None:
                hm.addSeparator()
            else:
                a_ = hm.addAction(lab); a_.triggered.connect(fn)
        self.status = self.statusBar()
        self.status.showMessage("Prêt")
        sig = QLabel(f"{BR.AUTEUR} - {BR.EMAIL}")
        sig.setStyleSheet("color:#555;padding-right:8px")
        self.status.addPermanentWidget(sig)
        logo = res_path(os.path.join("assets", "logo.png"))
        if os.path.exists(logo):
            self.setWindowIcon(QIcon(logo))

        for w_ in (self.sa, self.sho, self.sh1, self.sxc):
            w_.valueChanged.connect(self.on_opening_changed)
        self.shape.currentIndexChanged.connect(self.on_opening_changed)
        self.mode.currentIndexChanged.connect(self.on_opening_changed)

    def _table(self, headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setDefaultSectionSize(26)
        t.itemChanged.connect(self.on_edit)
        return t

    def _tab_beam(self):
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("Section et matériaux")
        f = QFormLayout(g)
        self.cb_code = QComboBox(); self.cb_code.addItems(["Eurocode 2", "BAEL 91"])
        self.sb = dspin(100, 2000, 300, 0, 10, " mm")
        self.sh = dspin(200, 3000, 600, 0, 10, " mm")
        self.cb_T = QCheckBox("Poutre en T (table de compression en haut)")
        self.sbf = dspin(150, 5000, 900, 0, 50, " mm")
        self.shf = dspin(40, 600, 120, 0, 10, " mm")
        self.sbf.setEnabled(False); self.shf.setEnabled(False)
        self.cb_T.toggled.connect(lambda c: (self.sbf.setEnabled(c), self.shf.setEnabled(c)))
        self.sfck = dspin(12, 90, 25, 0, 1, " MPa")
        self.sfyk = dspin(400, 600, 500, 0, 10, " MPa")
        self.scnom = dspin(10, 80, 30, 0, 5, " mm")
        self.sphit = dspin(6, 16, 8, 0, 2, " mm")
        self.cb_sw = QCheckBox("Ajouter le poids propre (25 kN/m³)")
        self.cb_sw.setChecked(True)
        self.shl = dspin(60, 400, 125, 0, 5, " mm")
        self.sdap = dspin(0, 2000, 0, 0, 50, " mm")
        for lab, w_ in (("Méthode", self.cb_code), ("Largeur b", self.sb), ("Hauteur h", self.sh), ("", self.cb_T), ("Largeur de table b_f", self.sbf),
                        ("Épaisseur de table h_f", self.shf), ("f_ck", self.sfck),
                        ("f_yk", self.sfyk), ("Enrobage nominal", self.scnom), ("Ø étrier (enrobage)", self.sphit),
                        ("", self.cb_sw), ("Linteau minimal", self.shl), ("Distance mini à l'appui (0 = h/2)", self.sdap)):
            f.addRow(lab, w_)
            if isinstance(w_, QDoubleSpinBox):
                w_.valueChanged.connect(self.on_edit)
        self.cb_code.currentIndexChanged.connect(self.on_edit)
        self.cb_sw.toggled.connect(self.on_edit)
        self.cb_T.toggled.connect(self.on_edit)
        v.addWidget(g)
        g2 = QGroupBox("Travées et charges (ELU 1,35 G + 1,5 Q)")
        l2 = QVBoxLayout(g2)
        self.t_spans = self._table(["Travée", "Longueur de calcul (m)", "g (kN/m)", "q (kN/m)"])
        l2.addWidget(self.t_spans)
        h = QHBoxLayout()
        for lab, fn in (("Ajouter une travée", self.add_span), ("Supprimer la dernière", self.del_span)):
            b_ = QPushButton(lab); b_.clicked.connect(fn); h.addWidget(b_)
        l2.addLayout(h)
        v.addWidget(g2, 1)
        n = QLabel("Charges g hors poids propre. Les appuis se déplacent aussi à la souris sur le dessin.")
        n.setWordWrap(True)
        v.addWidget(n)
        return w

    def _tab_supports(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.t_sup = self._table(["Appui", "Largeur (mm)", "Position de l'axe (m)"])
        v.addWidget(self.t_sup)
        n = QLabel("Les portées sont comptées entre axes d'appuis. La largeur sert au dessin et à la distance minimale "
                   "entre une réservation et la face de l'appui.")
        n.setWordWrap(True)
        v.addWidget(n)
        return w

    def _tab_bars(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.t_bars = self._table(["Face", "Travée", "Début (m)", "Longueur (m)", "n", "Ø (mm)", "n 2e groupe", "Ø 2e groupe", "Lit", "de x … à x (m)"])
        v.addWidget(self.t_bars)
        h = QHBoxLayout()
        for lab, fn in (("Ajouter", self.add_bar_row), ("Supprimer la ligne", lambda: self.del_row(self.t_bars)),
                        ("Copier la travée choisie vers toutes", lambda: self.copy_span("bars"))):
            b_ = QPushButton(lab); b_.clicked.connect(fn); h.addWidget(b_)
        v.addLayout(h)
        n = QLabel("Début : distance depuis l'appui gauche de la travée (négatif = avant l'appui). "
                   "Une barre peut dépasser la travée (chapeaux, barres continues). "
                   "Vous pouvez aussi tracer à la souris avec les outils « Armatures ».")
        n.setWordWrap(True)
        v.addWidget(n)
        return w

    def _tab_stir(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.t_stir = self._table(["Travée", "Début (m)", "Longueur (m)", "Ø (mm)", "Espacement (mm)", "Brins", "de x … à x (m)"])
        v.addWidget(self.t_stir)
        h = QHBoxLayout()
        for lab, fn in (("Ajouter", self.add_stir_row), ("Supprimer la ligne", lambda: self.del_row(self.t_stir)),
                        ("Copier la travée choisie vers toutes", lambda: self.copy_span("stir"))):
            b_ = QPushButton(lab); b_.clicked.connect(fn); h.addWidget(b_)
        v.addLayout(h)
        n = QLabel("Un tableau par poutre : pour chaque travée, donnez les zones d'étriers (position depuis l'appui gauche, "
                   "longueur, diamètre, espacement, nombre de brins). Outil « Étriers » : tracé à la souris.")
        n.setWordWrap(True)
        v.addWidget(n)
        return w

    def _tab_openings(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.t_open = self._table(["Nom", "x centre (m)", "Forme", "a / Ø (mm)", "h (mm)", "Position vert.", "h1 (mm)", "Résultat"])
        self.t_open.itemSelectionChanged.connect(self.on_open_table_select)
        v.addWidget(self.t_open)
        h = QHBoxLayout()
        for lab, fn in (("➕ Ajouter", self.add_opening), ("Supprimer", self.del_opening)):
            b_ = QPushButton(lab); b_.clicked.connect(fn); h.addWidget(b_)
        v.addLayout(h)
        n = QLabel("Sélectionner une ligne (ou cliquer sur la réservation dans le dessin) pour la déplacer / la modifier "
                   "avec la barre du haut. Deux réservations doivent être espacées d'au moins 2 fois la plus grande hauteur.")
        n.setWordWrap(True)
        v.addWidget(n)
        return w

    # ================================================================ tables <-> projet
    def _set_item(self, t, r, c, val, editable=True):
        it = QTableWidgetItem(val if isinstance(val, str) else f"{val:g}")
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if not editable:
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            it.setForeground(QColor(110, 110, 110))
        t.setItem(r, c, it)

    def _combo(self, t, r, c, items, index):
        cb = QComboBox()
        cb.addItems(items)
        cb.setCurrentIndex(max(0, min(index, len(items) - 1)))
        cb.currentIndexChanged.connect(self.on_edit)
        t.setCellWidget(r, c, cb)

    def _span_names(self):
        return [f"T{i+1}" for i in range(len(self.prj.spans))]

    def load_project_into_ui(self):
        p = self.prj
        p.sync()
        self._loading = True
        self.cb_code.setCurrentIndex(0 if p.code == "EC2" else 1)
        self.sb.setValue(p.b); self.sh.setValue(p.h)
        self.cb_T.setChecked(p.is_T)
        if p.bf > 0: self.sbf.setValue(p.bf)
        if p.hf > 0: self.shf.setValue(p.hf)
        self.sfck.setValue(p.fck); self.sfyk.setValue(p.fyk)
        self.scnom.setValue(p.cnom); self.sphit.setValue(p.phi_t); self.cb_sw.setChecked(p.selfweight)
        self.shl.setValue(p.hlint_min); self.sdap.setValue(p.dist_appui)
        xs = p.xsup()
        self.t_spans.setRowCount(len(p.spans))
        for i, L in enumerate(p.spans):
            self._set_item(self.t_spans, i, 0, f"T{i+1}", False)
            self._set_item(self.t_spans, i, 1, L)
            self._set_item(self.t_spans, i, 2, p.g[i])
            self._set_item(self.t_spans, i, 3, p.q[i])
        self.t_sup.setRowCount(len(p.spans) + 1)
        for k in range(len(p.spans) + 1):
            self._set_item(self.t_sup, k, 0, f"A{k+1}", False)
            self._set_item(self.t_sup, k, 1, p.support_w[k])
            self._set_item(self.t_sup, k, 2, f"{xs[k]:.2f}", False)
        names = self._span_names()
        self.t_bars.setRowCount(len(p.bars))
        for i, br in enumerate(p.bars):
            x0, x1 = p.bar_x(br)
            self._combo(self.t_bars, i, 0, ["Inf.", "Sup."], 0 if br.face == "bot" else 1)
            self._combo(self.t_bars, i, 1, names, br.span)
            for c, val in ((2, br.start), (3, br.length), (4, br.n), (5, br.dia), (6, br.n2), (7, br.dia2), (8, br.row)):
                self._set_item(self.t_bars, i, c, val)
            self._set_item(self.t_bars, i, 9, f"{x0:.2f} → {x1:.2f}", False)
        self.t_stir.setRowCount(len(p.stirrups))
        for i, z in enumerate(p.stirrups):
            x0, x1 = p.stir_x(z)
            self._combo(self.t_stir, i, 0, names, z.span)
            for c, val in ((1, z.start), (2, z.length), (3, z.dia), (4, z.spacing), (5, z.legs)):
                self._set_item(self.t_stir, i, c, val)
            self._set_item(self.t_stir, i, 6, f"{x0:.2f} → {x1:.2f}", False)
        self._loading = False
        self.refresh_openings_table()

    def refresh_openings_table(self):
        p = self.prj
        was = self._loading
        self._loading = True
        t = self.t_open
        t.blockSignals(True)
        t.setRowCount(len(p.openings))
        res = self.canvas.openings_res if len(self.canvas.openings_res) == len(p.openings) else [None] * len(p.openings)
        for i, op in enumerate(p.openings):
            self._set_item(t, i, 0, op.name or f"R{i+1}")
            self._set_item(t, i, 1, op.xc)
            self._combo(t, i, 2, ["Rectangle", "Cercle"], 0 if op.shape == "rect" else 1)
            self._set_item(t, i, 3, op.a)
            self._set_item(t, i, 4, op.a if op.shape == "circle" else op.ho)
            self._combo(t, i, 5, ["Auto", "Centrée", "Manuelle"], ["auto", "center", "manual"].index(op.mode))
            self._set_item(t, i, 6, op.h1 if op.mode == "manual" else (res[i].h1 if res[i] else 0.0), op.mode == "manual")
            r = res[i]
            txt = "…" if r is None else {0: "Sans renfort", 1: "Avec renforts", 2: "Impossible"}[r.status]
            self._set_item(t, i, 7, txt, False)
            if r is not None:
                t.item(i, 7).setBackground(QColor(COL[r.status].red(), COL[r.status].green(), COL[r.status].blue(), 90))
        if self.sel_open is not None and self.sel_open < len(p.openings):
            t.selectRow(self.sel_open)
        t.blockSignals(False)
        t.doItemsLayout()
        self._loading = was

    def read_ui_into_project(self):
        p = self.prj

        def num(t, r, c, default=0.0):
            it = t.item(r, c)
            try:
                return float(it.text().replace(",", ".")) if it else default
            except ValueError:
                return default

        def combo_idx(t, r, c):
            w = t.cellWidget(r, c)
            return w.currentIndex() if w is not None else 0

        p.code = "EC2" if self.cb_code.currentIndex() == 0 else "BAEL"
        p.b, p.h, p.fck, p.fyk = self.sb.value(), self.sh.value(), self.sfck.value(), self.sfyk.value()
        p.bf, p.hf = (self.sbf.value(), self.shf.value()) if self.cb_T.isChecked() else (0.0, 0.0)
        p.cnom, p.phi_t, p.selfweight = self.scnom.value(), self.sphit.value(), self.cb_sw.isChecked()
        p.hlint_min, p.dist_appui = self.shl.value(), self.sdap.value()
        spans, g, q = [], [], []
        for r in range(self.t_spans.rowCount()):
            L = num(self.t_spans, r, 1, 5.0)
            if L > 0.5:
                spans.append(L); g.append(num(self.t_spans, r, 2)); q.append(num(self.t_spans, r, 3))
        if spans:
            p.spans, p.g, p.q = spans, g, q
        p.support_w = [max(50.0, num(self.t_sup, r, 1, 300.0)) for r in range(self.t_sup.rowCount())]
        bars = []
        for r in range(self.t_bars.rowCount()):
            face = "bot" if combo_idx(self.t_bars, r, 0) == 0 else "top"
            span = combo_idx(self.t_bars, r, 1)
            start, length = num(self.t_bars, r, 2), num(self.t_bars, r, 3)
            n_, dia, row = int(num(self.t_bars, r, 4, 2)), num(self.t_bars, r, 5, 16), int(num(self.t_bars, r, 8, 1))
            n2_, dia2 = int(num(self.t_bars, r, 6, 0)), num(self.t_bars, r, 7, 0)
            if n2_ <= 0 or dia2 <= 0:
                n2_, dia2 = 0, 0.0
            if length > 0 and n_ > 0 and dia > 0:
                bars.append(LongBar(face, span, start, length, n_, dia, max(1, row), n2_, dia2))
        p.bars = bars
        st = []
        for r in range(self.t_stir.rowCount()):
            span = combo_idx(self.t_stir, r, 0)
            start, length = num(self.t_stir, r, 1), num(self.t_stir, r, 2)
            dia, sp_, legs = num(self.t_stir, r, 3, 8), num(self.t_stir, r, 4, 150), int(num(self.t_stir, r, 5, 2))
            if length > 0 and sp_ > 0 and dia > 0:
                st.append(StirrupZone(span, start, length, dia, sp_, legs))
        p.stirrups = st
        ops = []
        for r in range(self.t_open.rowCount()):
            it0 = self.t_open.item(r, 0)
            shape = "rect" if combo_idx(self.t_open, r, 2) == 0 else "circle"
            mode = ["auto", "center", "manual"][combo_idx(self.t_open, r, 5)]
            a_ = num(self.t_open, r, 3, 200)
            ho_ = a_ if shape == "circle" else num(self.t_open, r, 4, 150)
            ops.append(Opening(num(self.t_open, r, 1), a_, ho_, shape, mode, num(self.t_open, r, 6), it0.text() if it0 else ""))
        if self.t_open.rowCount() == len(ops):
            p.openings = ops
        p.sync()

    # ================================================================ événements
    def on_edit(self, *args):
        if self._loading:
            return
        self.read_ui_into_project()
        self.load_derived_columns()
        self.rebuild()
        if self.sel_open is not None and self.sel_open < len(self.prj.openings):
            self.load_probe_from(self.prj.openings[self.sel_open])

    def load_derived_columns(self):
        """Met à jour les colonnes calculées (x absolus) sans relancer la saisie."""
        p = self.prj
        self._loading = True
        xs = p.xsup()
        for k in range(self.t_sup.rowCount()):
            if k < len(xs):
                self._set_item(self.t_sup, k, 2, f"{xs[k]:.2f}", False)
        for i, br in enumerate(p.bars[:self.t_bars.rowCount()]):
            x0, x1 = p.bar_x(br)
            self._set_item(self.t_bars, i, 9, f"{x0:.2f} → {x1:.2f}", False)
        for i, z in enumerate(p.stirrups[:self.t_stir.rowCount()]):
            x0, x1 = p.stir_x(z)
            self._set_item(self.t_stir, i, 6, f"{x0:.2f} → {x1:.2f}", False)
        self._loading = False

    def rebuild(self):
        try:
            self.an = Analysis(self.prj)
            self.ck = Checker(self.an)
        except Exception as e:  # pragma: no cover
            self.status.showMessage("Erreur : " + repr(e))
            return
        self.sxc.blockSignals(True)
        self.sxc.setMaximum(self.an.Ltot)
        self.sxc.blockSignals(False)
        self.canvas.set_model(self.prj, self.an)
        self.update_opening()
        self.request_map()

    def on_tool(self, btn):
        self.canvas.tool = btn.property("tool")
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if self.canvas.tool != "select" else Qt.CursorShape.ArrowCursor)

    def on_scale(self, on):
        self.canvas.true_scale = on
        self.canvas.update_size()

    def on_canvas_xc(self, x):
        self.sxc.setValue(x)                 # déclenche on_opening_changed
        self.on_opening_changed()

    def on_canvas_spans(self, L):
        self._loading = True
        for i, v in enumerate(L):
            self._set_item(self.t_spans, i, 1, v)
        self._loading = False
        self.on_edit()

    # ---- tracé à la souris
    def on_bar_drawn(self, face, x0, x1):
        dlg = BarDialog(face, x0, x1, self)
        if dlg.exec():
            k = self.prj.span_of(x0)
            self.prj.bars.append(LongBar(face, k, x0 - self.prj.xsup()[k], x1 - x0, dlg.n.value(),
                                         float(dlg.dia.currentData()), dlg.row.value()))
            self.load_project_into_ui()
            self.rebuild()

    def on_stirrup_drawn(self, x0, x1):
        dlg = StirrupDialog(x0, x1, self)
        if not dlg.exec():
            return
        p = self.prj
        xs = p.xsup()
        new = []
        for z in p.stirrups:                       # on retire la zone tracée des étriers existants
            zx0, zx1 = p.stir_x(z)
            if zx1 <= x0 or zx0 >= x1:
                new.append(z)
                continue
            if zx0 < x0:
                new.append(StirrupZone(z.span, z.start, x0 - zx0, z.dia, z.spacing, z.legs))
            if zx1 > x1:
                new.append(StirrupZone(z.span, x1 - xs[z.span], zx1 - x1, z.dia, z.spacing, z.legs))
        for k in range(len(p.spans)):              # une zone par travée traversée
            a, b = max(x0, xs[k]), min(x1, xs[k + 1])
            if b - a > 1e-6:
                new.append(StirrupZone(k, a - xs[k], b - a, float(dlg.dia.currentData()), float(dlg.sp.value()), dlg.legs.value()))
        p.stirrups = new
        self.load_project_into_ui()
        self.rebuild()

    # ---- structure
    def add_span(self):
        self.read_ui_into_project()
        p = self.prj
        p.spans.append(p.spans[-1]); p.g.append(p.g[-1]); p.q.append(p.q[-1]); p.support_w.append(p.support_w[-1])
        self.load_project_into_ui()
        self.rebuild()

    def del_span(self):
        self.read_ui_into_project()
        p = self.prj
        if len(p.spans) > 1:
            p.spans.pop(); p.g.pop(); p.q.pop(); p.support_w.pop()
            p.sync()
            self.load_project_into_ui()
            self.rebuild()

    def do_auto_reinf(self):
        self.read_ui_into_project()
        self.prj.bars, self.prj.stirrups = auto_reinforcement(self.prj)
        self.load_project_into_ui()
        self.rebuild()
        self.status.showMessage("Ferraillage de départ généré (à ajuster à votre ferraillage réel).")

    def clear_reinf(self):
        self.prj.bars, self.prj.stirrups = [], []
        self.load_project_into_ui()
        self.rebuild()

    def add_bar_row(self):
        self.read_ui_into_project()
        self.prj.bars.append(LongBar("bot", 0, 0.0, self.prj.spans[0], 3, 16, 1))
        self.load_project_into_ui()
        self.rebuild()

    def add_stir_row(self):
        self.read_ui_into_project()
        self.prj.stirrups.append(StirrupZone(0, 0.0, self.prj.spans[0], 8, 150, 2))
        self.load_project_into_ui()
        self.rebuild()

    def del_row(self, t):
        r = t.currentRow()
        if r >= 0:
            t.removeRow(r)
            self.on_edit()

    def copy_span(self, what):
        """Copie les armatures (ou étriers) de la travée de la ligne choisie vers toutes les autres travées."""
        self.read_ui_into_project()
        p = self.prj
        t = self.t_bars if what == "bars" else self.t_stir
        r = t.currentRow()
        items = p.bars if what == "bars" else p.stirrups
        if r < 0 or r >= len(items):
            QMessageBox.information(self, "Copier", "Sélectionnez d'abord une ligne dans le tableau.")
            return
        src = items[r].span
        keep = [x for x in items if x.span == src]
        out = list(keep)
        for k in range(len(p.spans)):
            if k == src:
                continue
            for x in keep:
                c = LongBar(**x.__dict__) if what == "bars" else StirrupZone(**x.__dict__)
                c.span = k
                if what == "bars" and c.face == "bot":
                    c.length = x.length if x.length <= p.spans[k] + 1e-9 else p.spans[k]
                out.append(c)
        if what == "bars":
            p.bars = out
        else:
            p.stirrups = out
        self.load_project_into_ui()
        self.rebuild()

    # ---- réservations enregistrées
    def probe_values(self):
        circle = self.shape.currentIndex() == 1
        a = self.sa.value()
        ho = a if circle else self.sho.value()
        mode = ["auto", "center", "manual"][self.mode.currentIndex()]
        return dict(xc=self.sxc.value(), a=a, ho=ho, shape=("circle" if circle else "rect"), mode=mode, h1=self.sh1.value())

    def add_opening(self):
        v = self.probe_values()
        self.read_ui_into_project()
        n = len(self.prj.openings)
        self.prj.openings.append(Opening(v["xc"], v["a"], v["ho"], v["shape"], v["mode"], v["h1"], f"R{n+1}"))
        self.load_project_into_ui()
        self.rebuild()
        self.select_opening(None)
        # place l'essai suivant au point le plus éloigné des réservations existantes
        L = sum(self.prj.spans)
        xs_o = [o.xc for o in self.prj.openings]
        best = max((i * 0.1 for i in range(5, int(L / 0.1) - 4)), key=lambda x: min(abs(x - xo) for xo in xs_o))
        self.sxc.setValue(best)
        self.status.showMessage(f"Réservation R{n+1} ajoutée. Déplacez l'essai pour en ajouter une autre.")

    def del_opening(self):
        i = self.sel_open if self.sel_open is not None else self.t_open.currentRow()
        if i is None or i < 0 or i >= len(self.prj.openings):
            return
        self.prj.openings.pop(i)
        self.sel_open = None
        self.load_project_into_ui()
        self.rebuild()
        self.select_opening(None)

    def on_open_table_select(self):
        if self._loading:
            return
        r = self.t_open.currentRow()
        if r >= 0 and r != self.sel_open:
            self.select_opening(r)

    def load_probe_from(self, op):
        for w_ in (self.sa, self.sho, self.sh1, self.sxc, self.shape, self.mode):
            w_.blockSignals(True)
        self.shape.setCurrentIndex(0 if op.shape == "rect" else 1)
        self.sa.setValue(op.a)
        self.sho.setValue(op.ho)
        self.mode.setCurrentIndex(["auto", "center", "manual"].index(op.mode))
        self.sh1.setValue(op.h1)
        self.sxc.setValue(op.xc)
        for w_ in (self.sa, self.sho, self.sh1, self.sxc, self.shape, self.mode):
            w_.blockSignals(False)
        circle = op.shape == "circle"
        self.lab_a.setText("Diamètre Ø" if circle else "Longueur a")
        self.lab_h.setVisible(not circle); self.sho.setVisible(not circle)
        self.sh1.setEnabled(op.mode == "manual")

    def select_opening(self, i):
        if i is not None and not (0 <= i < len(self.prj.openings)):
            i = None
        self.sel_open = i
        self.canvas.sel_open = i
        if i is not None:
            self.load_probe_from(self.prj.openings[i])
            self.lab_sel.setText(f"<b>Réservation {self.prj.openings[i].name or 'R'+str(i+1)} :</b>")
            self.btn_add_open.setText("➕ Ajouter une autre")
        else:
            self.lab_sel.setText("<b>Réservation d'essai :</b>")
            self.btn_add_open.setText("➕ Ajouter à la poutre")
        self.refresh_openings_table()
        self.update_opening()
        self.request_map()

    def on_opening_changed(self, *args):
        if self._loading:
            return
        v = self.probe_values()
        circle = v["shape"] == "circle"
        self.canvas.shape = v["shape"]
        self.lab_a.setText("Diamètre Ø" if circle else "Longueur a")
        self.lab_h.setVisible(not circle); self.sho.setVisible(not circle)
        self.sh1.setEnabled(v["mode"] == "manual")
        self.canvas.xc = v["xc"]
        i = self.sel_open
        if i is not None and i < len(self.prj.openings):
            op = self.prj.openings[i]
            op.xc, op.a, op.ho, op.shape, op.mode = v["xc"], v["a"], v["ho"], v["shape"], v["mode"]
            if v["mode"] == "manual":
                op.h1 = v["h1"]
        if self.an:
            self.update_opening()
            self.request_map()

    # ================================================================ calculs
    def compute_openings(self):
        res = []
        ops = self.prj.openings
        for i, op in enumerate(ops):
            others = [(o.xc, o.a, o.ho_eff) for j, o in enumerate(ops) if j != i]
            res.append(self.ck.check_opening(op, others))
        return res

    def update_opening(self):
        if not self.ck:
            return
        ops = self.prj.openings
        self.ck.others = [(o.xc, o.a, o.ho_eff) for j, o in enumerate(ops) if j != self.sel_open]
        self.canvas.openings_res = self.compute_openings()
        self.canvas.sel_open = self.sel_open
        v = self.probe_values()
        self.canvas.shape = v["shape"]
        self.canvas.xc = v["xc"]
        if self.sel_open is not None and self.sel_open < len(ops):
            self.res = self.canvas.openings_res[self.sel_open]
            self.canvas.probe = None
        else:
            self.res = self.ck.check(v["xc"], v["a"], v["ho"], h1=(v["h1"] if v["mode"] == "manual" else None),
                                     mode=v["mode"], shape=v["shape"])
            self.canvas.probe = self.res
        if v["mode"] != "manual" and self.res is not None:
            self.sh1.blockSignals(True)
            self.sh1.setValue(round(self.res.h1))
            self.sh1.blockSignals(False)
        self.canvas.res = self.res
        self.refresh_openings_table()
        self.canvas.update()
        self.result.setHtml(self.result_html())

    def request_map(self):
        self.timer.start(250)

    def start_worker(self):
        if self.worker is not None and self.worker.isRunning():
            self.pending = True
            return
        self.gen += 1
        v = self.probe_values()
        self.worker = MapWorker(self.prj.to_dict(), v["a"], v["ho"], v["mode"], v["shape"], self.gen, self.sel_open)
        self.worker.done.connect(self.on_map_done)
        self.status.showMessage("Calcul des zones de réservation…")
        self.worker.start()

    def on_map_done(self, m):
        if self.pending:
            self.pending = False
            self.timer.start(50)
            return
        if not m.get("ok"):
            self.status.showMessage("Erreur de calcul : " + m.get("err", ""))
            return
        self.canvas.maps = m
        self.maps = m
        self.canvas.update()
        self.result.setHtml(self.result_html())
        self.status.showMessage("Zones à jour.")

    # ================================================================ résultats
    def zones_ok(self):
        m = self.maps
        if not m:
            return []
        xs, st = m["xs"], m["st"]
        out = []
        i = 0
        while i < len(xs):
            if st[i] == STATUS_OK:
                j = i
                while j + 1 < len(xs) and st[j + 1] == STATUS_OK:
                    j += 1
                sel = (m["hx"] >= xs[i] - 1e-6) & (m["hx"] <= xs[j] + 1e-6)
                hmax = float(m["hg"][sel].max()) if sel.any() else 0.0
                out.append((float(xs[i]), float(xs[j]), hmax))
                i = j + 1
            else:
                i += 1
        return out

    def result_html(self):
        r = self.res
        if r is None:
            return ""
        e = html.escape
        circle = r.shape == "circle"
        size = f"Ø {r.a:.0f} mm" if circle else f"{r.a:.0f} × {r.ho:.0f} mm"
        nm = ""
        if self.sel_open is not None and self.sel_open < len(self.prj.openings):
            nm = (self.prj.openings[self.sel_open].name or f"R{self.sel_open+1}") + " - "
        h = [f"<h3 style='margin-bottom:2px'>{e(nm)}Réservation {size} à x = {r.xc:.2f} m</h3>"]
        h.append(f"<p style='font-size:15px;color:white;background:{HEX[r.status]};padding:6px;border-radius:4px'><b>{e(r.label)}</b></p>")
        if r.status != STATUS_NO:
            h.append(f"<p>Position verticale : h1 = {r.h1:.0f} mm sous la face supérieure, h2 = {r.h2:.0f} mm au-dessus de la face inférieure.</p>")
        for t in r.reasons:
            h.append(f"<p>• {e(t)}</p>")
        i = r.info or {}
        if "M_kNm" in i:
            h.append("<h4>Sollicitations dominantes (ELU)</h4><table border='0' cellpadding='2'>")
            h.append(f"<tr><td>Moment M</td><td align='right'><b>{i['M_kNm']:.0f} kN·m</b></td></tr>")
            h.append(f"<tr><td>Effort tranchant V</td><td align='right'><b>{i['V_kN']:.0f} kN</b></td></tr>")
            h.append(f"<tr><td>Axe neutre x<sub>u</sub></td><td align='right'>{i['xu_mm']:.0f} mm</td></tr>")
            h.append(f"<tr><td>Bras de levier z</td><td align='right'>{i['z_mm']:.0f} mm</td></tr>")
            h.append(f"<tr><td>Force dans les membrures F</td><td align='right'>{i['F_kN']:.0f} kN</td></tr>")
            h.append(f"<tr><td>Épaisseur de bielle a</td><td align='right'>{i['a_v_mm']:.0f} mm</td></tr>")
            h.append(f"<tr><td>Côté d'inscription du treillis h<sub>w</sub></td><td align='right'><b>{max(i['hw_mm'],0):.0f} mm</b></td></tr></table>")
            h.append(f"<p style='color:#444'>Critère du treillis : {e(i.get('critere_treillis',''))}.<br>"
                     f"Fenêtre libre : de {i['marge_haut_mm']:.0f} mm sous la face haute à {i['marge_bas_mm']:.0f} mm au-dessus de la face basse.</p>")
            h.append(f"<p>Aciers tendus en place : {e(i.get('lab_ts') or 'aucun')} = {i['A_ts_exist_cm2']:.2f} cm².<br>"
                     f"Armatures de suspension (V / f<sub>yd</sub>) : {i.get('susp_req_cm2', 0):.2f} cm² requis, "
                     f"{i.get('susp_exist_cm2', i.get('susp_g_cm2', 0)):.2f} cm² en place.</p>")
            if "nota_flexion" in i:
                h.append(f"<p style='color:#b35900'><i>{e(i['nota_flexion'])}</i></p>")
        if "linteaux" in i:
            h.append("<h4>Linteaux (méthode des grandes réservations)</h4>")
            for name, l in i["linteaux"].items():
                h.append(f"<p><b>Linteau {name}</b> (h = {l['h']:.0f} mm) : N = {l['N_kN']:.0f} kN, V = {l['V_kN']:.0f} kN, "
                         f"M<sub>0</sub> = {l['M0_kNm']:.1f} kN·m<br>"
                         f"Aciers face extérieure ({l['outer_face']}) : {l['A_outer_req_cm2']:.2f} cm² requis / {l['A_outer_exist_cm2']:.2f} en place "
                         f"→ <b>ajouter {l['extra_outer_cm2']:.2f} cm²</b><br>"
                         f"Aciers face de la réservation : <b>ajouter {l['extra_inner_cm2']:.2f} cm²</b><br>"
                         f"Cadres du linteau : {l['Asw_s_cm2_m']:.1f} cm²/m</p>")
            h.append(f"<p>Suspension de part et d'autre : {i.get('susp_req_cm2',0):.2f} cm² requis ; "
                     f"gauche {i.get('susp_g_cm2',0):.2f}, droite {i.get('susp_d_cm2',0):.2f} cm² en place "
                     f"→ <b>étriers à ajouter : {i.get('extra_susp_cm2',0):.2f} cm²</b></p>")
        elif i.get("extra_susp_cm2", 0) > 0:
            h.append(f"<p><b>Étriers à ajouter : {i['extra_susp_cm2']:.2f} cm²</b></p>")
        zs = self.zones_ok()
        h.append("<h4>Zones sans renfort pour cette dimension</h4>")
        if zs:
            h.append("<ul>" + "".join(f"<li>de x = {a:.2f} m à x = {b:.2f} m (hauteur max sans renfort ≈ {hm:.0f} mm pour a = {r.a:.0f} mm)</li>"
                                      for a, b, hm in zs) + "</ul>")
        elif self.maps:
            h.append("<p>Aucune zone : essayez une réservation plus petite.</p>")
        else:
            h.append("<p>Calcul en cours…</p>")
        ops = self.prj.openings
        res = self.canvas.openings_res
        if ops and len(res) == len(ops):
            h.append("<h4>Réservations de la poutre</h4><table border='1' cellspacing='0' cellpadding='3'>"
                     "<tr><th>Nom</th><th>x (m)</th><th>Dimension</th><th>Résultat</th></tr>")
            for k, (op, rr) in enumerate(zip(ops, res)):
                dim = f"Ø {op.a:.0f}" if op.shape == "circle" else f"{op.a:.0f} × {op.ho:.0f}"
                h.append(f"<tr><td>{e(op.name or 'R'+str(k+1))}</td><td align='right'>{op.xc:.2f}</td><td>{dim} mm</td>"
                         f"<td style='background:{HEX[rr.status]};color:white'>{e(rr.label)}</td></tr>")
            h.append("</table>")
        h.append(f"<hr><p style='color:#666;font-size:11px'>{BR.APP_NAME} - {BR.AUTEUR} - {BR.EMAIL}<br>Résultats indicatifs : à valider par l'ingénieur structure. "
                 "Vérification ELU, charges réparties, poutre rectangulaire ou en T, flexion plane ; ELS et flèche non traités.</p>")
        return "".join(h)

    # ================================================================ fichiers
    def new_project(self):
        self.prj = default_project()
        self.path = None
        self.sel_open = None
        self.load_project_into_ui()
        self.rebuild()

    def open_project(self):
        f, _ = QFileDialog.getOpenFileName(self, "Ouvrir un projet", "", "Projet (*.json)")
        if f:
            try:
                self.prj = Project.load(f)
            except Exception as ex:
                QMessageBox.warning(self, "Erreur", f"Fichier illisible : {ex}")
                return
            self.path = f
            self.sel_open = None
            self.load_project_into_ui()
            self.rebuild()

    def save_project(self):
        if not self.path:
            return self.save_project_as()
        self.read_ui_into_project()
        self.prj.save(self.path)
        self.status.showMessage("Projet enregistré : " + self.path)

    def save_project_as(self):
        f, _ = QFileDialog.getSaveFileName(self, "Enregistrer le projet", "poutre.json", "Projet (*.json)")
        if f:
            self.path = f
            self.save_project()

    def export_png(self):
        f, _ = QFileDialog.getSaveFileName(self, "Exporter l'image", "poutre_reservations.png", "PNG (*.png)")
        if f:
            self.canvas.render_pixmap().save(f)

    def export_html(self):
        f, _ = QFileDialog.getSaveFileName(self, "Exporter la note", "note_reservations.html", "HTML (*.html)")
        if not f:
            return
        pm = self.canvas.render_pixmap()
        buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        b64 = base64.b64encode(bytes(buf.data())).decode()
        p = self.prj
        rows = "".join(f"<tr><td>{br.label}</td><td>{'inf.' if br.face=='bot' else 'sup.'}</td><td>T{br.span+1}, début {br.start:.2f} m, L = {br.length:.2f} m</td></tr>" for br in p.bars)
        srows = "".join(f"<tr><td>HA{z.dia:g}</td><td>s = {z.spacing:.0f} mm ({z.legs} brins)</td><td>T{z.span+1}, début {z.start:.2f} m, L = {z.length:.2f} m</td></tr>" for z in p.stirrups)
        doc = (f"<html><head><meta charset='utf-8'><title>Note - réservations</title></head><body style='font-family:Segoe UI,Arial;max-width:1100px;margin:auto'>"
               f"<h1>Réservations dans une poutre continue</h1>"
               f"<p>Méthode : {'Eurocode 2' if p.code=='EC2' else 'BAEL 91'} - b × h = {p.b:.0f} × {p.h:.0f} mm - f<sub>ck</sub> = {p.fck:.0f} MPa - f<sub>yk</sub> = {p.fyk:.0f} MPa<br>"
               f"Travées : {', '.join(f'{L:.2f} m' for L in p.spans)} - appuis : {', '.join(f'{w:.0f}' for w in p.support_w)} mm - "
               f"g = {', '.join(f'{v:g}' for v in p.g)} - q = {', '.join(f'{v:g}' for v in p.q)} kN/m</p>"
               f"<img style='width:100%' src='data:image/png;base64,{b64}'/>"
               f"<h2>Armatures saisies</h2><table border='1' cellpadding='4'>{rows}</table><table border='1' cellpadding='4' style='margin-top:6px'>{srows}</table>"
               f"<h2>Réservation étudiée</h2>{self.result_html()}"
               f"</body></html>")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(doc)
        self.status.showMessage("Note exportée : " + f)


def _open_url(self, url):
    QDesktopServices.openUrl(QUrl(url))


def _open_manual(self):
    f = res_path("Livret_utilisation.pdf")
    if os.path.exists(f):
        QDesktopServices.openUrl(QUrl.fromLocalFile(f))
    else:
        QDesktopServices.openUrl(QUrl(BR.MANUEL_URL))


def _about(self):
    d = QMessageBox(self)
    d.setWindowTitle("À propos")
    logo = res_path(os.path.join("assets", "logo.png"))
    if os.path.exists(logo):
        d.setIconPixmap(QPixmap(logo).scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    lines = [f"<h3>{BR.APP_NAME} - version {BR.APP_VERSION}</h3>",
             "Zones de réservations (ouvertures) admissibles dans une poutre continue en béton armé - Eurocode 2 / BAEL 91.",
             f"<p><b>{BR.AUTEUR}</b><br>E-mail : <a href='mailto:{BR.EMAIL}'>{BR.EMAIL}</a>"]
    if BR.TEL:
        lines.append(f"<br>Tél. : {BR.TEL}")
    if BR.SITE:
        lines.append(f"<br>Site : <a href='{BR.SITE}'>{BR.SITE}</a>")
    lines.append("</p>")
    lines.append(f"<p>Code source : <a href='{BR.CODE_ZIP_URL}'>télécharger (zip)</a> - "
                 f"Exécutable Windows : <a href='{BR.EXE_URL}'>télécharger</a> - "
                 f"<a href='{BR.REPO_URL}'>page du projet</a></p>")
    lines.append("<p style='color:#666'>Résultats indicatifs : à valider par l'ingénieur structure.</p>")
    d.setTextFormat(Qt.TextFormat.RichText)
    d.setText("".join(lines))
    d.exec()


MainWindow.open_url = _open_url
MainWindow.open_manual = _open_manual
MainWindow.about = _about


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
