"""ui/network_map.py - Force-directed P2P topology visualization."""
import math, random
from typing import Dict, List, Tuple
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QTimer, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QRadialGradient, QFont

from ui.theme import (CYAN, CYAN_DIM, MAGENTA, GREEN, RED, YELLOW,
                       BG_BASE, TEXT_PRI, TEXT_SEC, TEXT_DIM, BORDER_SUB)


class ForceNode:
    def __init__(self, nid, label, is_self=False):
        self.id      = nid
        self.label   = label
        self.is_self = is_self
        self.x = random.uniform(0.2, 0.8)
        self.y = random.uniform(0.2, 0.8)
        self.vx = self.vy = 0.0
        self.connected = False
        self.status = "trusted"

    def apply(self, fx, fy, damp=0.85):
        self.vx = (self.vx + fx) * damp
        self.vy = (self.vy + fy) * damp
        self.x  = max(0.1, min(0.9, self.x + self.vx))
        self.y  = max(0.1, min(0.9, self.y + self.vy))


class NetworkMapView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._nodes: Dict[str, ForceNode] = {}
        self._edges: List[Tuple[str,str]]  = []
        self._self_id = "self"
        self._phase   = 0.0
        self._nodes[self._self_id] = ForceNode(self._self_id, "YOU", is_self=True)
        t = QTimer(self); t.timeout.connect(self._tick); t.start(32)

    def update_topology(self, peers, connected_ids):
        seen = {self._self_id}
        for peer in peers:
            pid    = peer["peer_id"] if hasattr(peer,"__getitem__") else peer.get("peer_id")
            name   = peer["name"]    if hasattr(peer,"__getitem__") else peer.get("name","?")
            status = peer["status"]  if hasattr(peer,"__getitem__") else peer.get("status","pending")
            if not pid: continue
            seen.add(pid)
            if pid not in self._nodes:
                n = ForceNode(pid, name); n.status = status; self._nodes[pid] = n
            else:
                self._nodes[pid].status    = status
                self._nodes[pid].connected = pid in connected_ids
        for nid in list(self._nodes.keys()):
            if nid not in seen: del self._nodes[nid]
        self._edges = [(self._self_id, pid) for pid in connected_ids if pid in self._nodes]

    def _tick(self):
        self._phase += 0.04
        self._simulate()
        self.update()

    def _simulate(self):
        nodes = list(self._nodes.values())
        for i, a in enumerate(nodes):
            if a.is_self:
                a.apply((0.5-a.x)*0.05, (0.5-a.y)*0.05); continue
            fx = fy = 0.0
            for j, b in enumerate(nodes):
                if i==j: continue
                dx = a.x-b.x; dy = a.y-b.y
                dist = max(math.hypot(dx,dy), 0.01)
                f = 0.0008/(dist**2)
                fx += f*dx/dist; fy += f*dy/dist
            fx += (0.5-a.x)*0.003; fy += (0.5-a.y)*0.003
            a.apply(fx, fy)
        for a_id, b_id in self._edges:
            a = self._nodes.get(a_id); b = self._nodes.get(b_id)
            if not a or not b: continue
            dx = b.x-a.x; dy = b.y-a.y
            dist = max(math.hypot(dx,dy), 0.01)
            f = (dist-0.35)*0.02
            a.apply(f*dx/dist, f*dy/dist)
            b.apply(-f*dx/dist, -f*dy/dist)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(BG_BASE))

        def sc(nx, ny): return QPointF(nx*w, ny*h)

        # Edges
        for a_id, b_id in self._edges:
            a = self._nodes.get(a_id); b = self._nodes.get(b_id)
            if not a or not b: continue
            pa = sc(a.x,a.y); pb = sc(b.x,b.y)
            pulse = abs(math.sin(self._phase + hash(a_id+b_id)%100*0.1))
            p.setPen(QPen(QColor(0,255,231,int(80+pulse*120)),1.5,Qt.PenStyle.DashLine))
            p.drawLine(pa, pb)
            t = (math.sin(self._phase*1.5)+1)/2
            dot = QPointF(pa.x()+(pb.x()-pa.x())*t, pa.y()+(pb.y()-pa.y())*t)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(QColor(0,255,231,200)))
            p.drawEllipse(dot, 4, 4)

        # Nodes
        p.setFont(QFont("JetBrains Mono", 9))
        for nid, node in self._nodes.items():
            pos = sc(node.x, node.y)
            if node.is_self:
                for rr,al in [(30,15),(18,45),(10,110)]:
                    g = QRadialGradient(pos, rr)
                    g.setColorAt(0, QColor(0,255,231,al)); g.setColorAt(1, QColor(0,0,0,0))
                    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g)); p.drawEllipse(pos,rr,rr)
                p.setBrush(QBrush(QColor(CYAN))); p.setPen(QPen(QColor(200,255,255),2))
                p.drawEllipse(pos,12,12)
                p.setPen(QPen(QColor(CYAN))); p.drawText(QPointF(pos.x()-14,pos.y()+28),"[ YOU ]")
            else:
                col = QColor({"trusted":GREEN,"pending":YELLOW,"blocked":RED}.get(node.status,TEXT_SEC))
                g = QRadialGradient(pos, 20)
                g.setColorAt(0, QColor(col.red(),col.green(),col.blue(),60)); g.setColorAt(1,QColor(0,0,0,0))
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g)); p.drawEllipse(pos,20,20)
                r = 10 if node.connected else 7
                p.setBrush(QBrush(col)); p.setPen(QPen(col.lighter(150),1.5)); p.drawEllipse(pos,r,r)
                if node.connected:
                    p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(QColor(GREEN),1)); p.drawEllipse(pos,r+5,r+5)
                name = node.label[:10]+("…" if len(node.label)>10 else "")
                p.setPen(QPen(QColor(TEXT_PRI))); p.drawText(QPointF(pos.x()+r+5,pos.y()+4),name)

        # Legend
        items = [(GREEN,"Trusted/Connected"),(YELLOW,"Pending"),(RED,"Blocked"),(CYAN,"Self")]
        x,y = 12, h-12-len(items)*18
        p.setFont(QFont("JetBrains Mono", 8))
        for color, label in items:
            p.setBrush(QBrush(QColor(color))); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x+5,y-4),5,5)
            p.setPen(QPen(QColor(TEXT_SEC))); p.drawText(x+15,y,label); y+=18
