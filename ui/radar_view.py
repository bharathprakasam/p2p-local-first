"""ui/radar_view.py - Animated radar visualization of nearby peers."""
import math, random, time
from typing import Dict
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush,
                          QRadialGradient, QFont, QConicalGradient)
from ui.theme import (CYAN, MAGENTA, GREEN, RED, YELLOW,
                       BG_BASE, BG_SURFACE, TEXT_SEC, TEXT_PRI,
                       CYAN_DIM, BORDER_SUB)


class PeerNode:
    def __init__(self, peer_id, name, status):
        self.peer_id     = peer_id
        self.name        = name
        self.status      = status
        self.angle       = random.uniform(0, 2 * math.pi)
        self.radius      = random.uniform(0.25, 0.44)
        self.drift_speed = random.uniform(-0.002, 0.002)
        self.pulse_phase = random.uniform(0, 2 * math.pi)
        self.ripple      = 0.0
        self.ripple_active = True

    def update(self):
        self.angle       += self.drift_speed
        self.pulse_phase += 0.05
        if self.ripple_active:
            self.ripple += 0.03
            if self.ripple >= 1.0:
                self.ripple = 0.0
                self.ripple_active = False

    def screen_pos(self, cx, cy, max_r) -> QPointF:
        r = self.radius * max_r
        return QPointF(cx + r * math.cos(self.angle), cy + r * math.sin(self.angle))

    def color(self) -> QColor:
        return QColor({"trusted": GREEN, "pending": YELLOW,
                       "blocked": RED, "discovered": CYAN}.get(self.status, TEXT_SEC))


class RadarView(QWidget):
    peer_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._nodes: Dict[str, PeerNode] = {}
        self._connected: set = set()
        self._sweep = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def add_peer(self, peer_id, name, status="discovered"):
        if peer_id not in self._nodes:
            self._nodes[peer_id] = PeerNode(peer_id, name, status)
        else:
            self._nodes[peer_id].status = status
            self._nodes[peer_id].name   = name

    def remove_peer(self, peer_id): self._nodes.pop(peer_id, None)

    def update_peer_status(self, peer_id, status):
        if peer_id in self._nodes:
            self._nodes[peer_id].status = status
            self._nodes[peer_id].ripple_active = True
            self._nodes[peer_id].ripple = 0.0

    def set_connected(self, peer_ids: list):
        self._connected = set(peer_ids)

    def _tick(self):
        self._sweep = (self._sweep + 1.2) % 360
        for n in self._nodes.values(): n.update()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        max_r  = min(w, h) / 2 - 10
        p.fillRect(self.rect(), QColor(BG_BASE))
        self._draw_grid(p, cx, cy, max_r)
        self._draw_sweep(p, cx, cy, max_r)
        self._draw_connections(p, cx, cy, max_r)
        self._draw_nodes(p, cx, cy, max_r)
        self._draw_self(p, cx, cy)

    def _draw_grid(self, p, cx, cy, max_r):
        for frac in [0.33, 0.66, 1.0]:
            r = frac * max_r
            pen = QPen(QColor(30, 50, 80, 100), 1, Qt.PenStyle.DotLine)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)
        pen = QPen(QColor(20, 40, 70, 80), 1, Qt.PenStyle.DotLine)
        p.setPen(pen)
        p.drawLine(int(cx - max_r), int(cy), int(cx + max_r), int(cy))
        p.drawLine(int(cx), int(cy - max_r), int(cx), int(cy + max_r))

    def _draw_sweep(self, p, cx, cy, max_r):
        grad = QConicalGradient(cx, cy, -self._sweep)
        grad.setColorAt(0.0,  QColor(0, 255, 231, 90))
        grad.setColorAt(0.15, QColor(0, 255, 231, 0))
        grad.setColorAt(1.0,  QColor(0, 255, 231, 0))
        p.setBrush(QBrush(grad)); p.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(cx - max_r, cy - max_r, max_r * 2, max_r * 2)
        p.drawPie(rect, int(-self._sweep * 16), int(-60 * 16))
        sr = math.radians(self._sweep)
        ex, ey = cx + max_r * math.cos(sr), cy + max_r * math.sin(sr)
        p.setPen(QPen(QColor(0, 255, 231, 200), 1.5))
        p.drawLine(int(cx), int(cy), int(ex), int(ey))

    def _draw_connections(self, p, cx, cy, max_r):
        for pid, node in self._nodes.items():
            if pid not in self._connected: continue
            pos   = node.screen_pos(cx, cy, max_r)
            pulse = abs(math.sin(node.pulse_phase)) * 0.6 + 0.4
            p.setPen(QPen(QColor(0, 255, 231, int(pulse * 160)), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(cx, cy), pos)
            t = (math.sin(node.pulse_phase) + 1) / 2
            dot = QPointF(cx + (pos.x()-cx)*t, cy + (pos.y()-cy)*t)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(0, 255, 231, 220)))
            p.drawEllipse(dot, 3, 3)

    def _draw_nodes(self, p, cx, cy, max_r):
        p.setFont(QFont("JetBrains Mono", 9))
        for pid, node in self._nodes.items():
            pos   = node.screen_pos(cx, cy, max_r)
            color = node.color()
            pulse = abs(math.sin(node.pulse_phase)) * 0.4 + 0.6
            r = 8
            if node.ripple > 0:
                rr = r + node.ripple * 30
                al = int((1 - node.ripple) * 140)
                p.setPen(QPen(QColor(color.red(),color.green(),color.blue(),al), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(pos, rr, rr)
            grad = QRadialGradient(pos, r * 2.5)
            grad.setColorAt(0, QColor(color.red(),color.green(),color.blue(),int(100*pulse)))
            grad.setColorAt(1, QColor(0,0,0,0))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(grad))
            p.drawEllipse(pos, r * 2.5, r * 2.5)
            p.setBrush(QBrush(color))
            p.setPen(QPen(color.lighter(180), 1.5))
            p.drawEllipse(pos, r, r)
            p.setPen(QPen(QColor(TEXT_PRI)))
            name = node.name[:12] + "…" if len(node.name) > 12 else node.name
            p.drawText(QPointF(pos.x() + r + 5, pos.y() + 4), name)

    def _draw_self(self, p, cx, cy):
        pulse = abs(math.sin(time.time() * 2)) * 0.5 + 0.5
        for rr, al in [(28,15),(18,45),(10,110)]:
            g = QRadialGradient(QPointF(cx,cy), rr)
            g.setColorAt(0, QColor(0,255,231,int(al*pulse)))
            g.setColorAt(1, QColor(0,0,0,0))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(cx,cy), rr, rr)
        p.setBrush(QBrush(QColor(CYAN))); p.setPen(QPen(QColor(200,255,250),2))
        p.drawEllipse(QPointF(cx,cy), 10, 10)
        p.setFont(QFont("JetBrains Mono", 8, QFont.Weight.Bold))
        p.setPen(QPen(QColor(CYAN)))
        p.drawText(QPointF(cx - 12, cy + 26), "[ YOU ]")

    def mousePressEvent(self, event):
        w, h   = self.width(), self.height()
        cx, cy = w/2, h/2
        max_r  = min(w,h)/2 - 10
        click  = event.position()
        for pid, node in self._nodes.items():
            pos = node.screen_pos(cx, cy, max_r)
            if math.hypot(click.x()-pos.x(), click.y()-pos.y()) < 16:
                self.peer_selected.emit(pid); return
        super().mousePressEvent(event)
