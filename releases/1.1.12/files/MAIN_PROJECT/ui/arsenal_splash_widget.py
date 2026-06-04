# -*- coding: utf-8 -*-
"""
Заставка (только Qt): полупрозрачное окно, карточка «стекла» и строка ARSENAL x TEPLYAKOV.
Анимация: полёт TEPLYAKOV, удар, отскок ARSENAL, «x», осколки (треугольники Делоне) падают и ложатся внизу сцены, затем затухание.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from PyQt5.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QPointF,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontInfo,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)
from PyQt5.QtWidgets import (
    QFrame,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QLabel,
    QVBoxLayout,
    QWidget,
)

_SPLASH_CARD_W = 640
_SPLASH_CARD_H = 340
_TEP_OFFSCREEN_PAD = 120.0
_FLY_MS = 280
_CARD_RADIUS = 10.0
_FONT_ARSENAL_PT = 44
_FONT_TEP_PT = 44
_FONT_CONNECTOR_PT = 32
# Запас по ширине строки относительно _SPLASH_CARD_W (слева+справа в сумме).
_SPLASH_TITLE_INNER_PAD = 48.0

_SPLASH_GREEN_TEST_BG = False
_SPLASH_GLASS_HEX = "#32CD32" if _SPLASH_GREEN_TEST_BG else "#2EB9D1"
_SPLASH_GLASS_BORDER_HEX = "#15803d" if _SPLASH_GREEN_TEST_BG else "#0288d1"
# Непрозрачность стекла: было 105 и 220; «на 30% меньше прозрачности» = +30% пути к alpha=255.
_SPLASH_CARD_BG_ALPHA = int(round(105 + (255 - 105) * 0.3))  # 150
_SPLASH_GLASS_BORDER_ALPHA = int(round(220 + (255 - 220) * 0.3))  # 231

_SPLASH_STATIC_INTRO_MS = 260
# True: полёт TEPLYAKOV, удар, появление «x», отскок ARSENAL. False — только карточка и подпись.
_SPLASH_PLAY_INTRO_ANIMATION = True
# Осколки: триангуляция Делоне по точкам на контуре + внутри (вытянутые треугольники — хорды).
_SHARD_BOUNDARY_STEPS = 88
_SHARD_INTERIOR_TARGET = 38
_SHARD_CHORD_LINES = 14
_SHARD_CHORD_T = (0.12, 0.28, 0.44, 0.58, 0.72, 0.86)
_SHARD_SITE_MIN_DIST_SQ = 5.76  # ~2.4 px между сайтами
_SHARD_TRI_MIN_AREA = 9.0
_SHARD_GRAVITY_PX_S2 = 1500.0
_SHARD_TIMER_MS = 16
_SHARD_MAX_FALL_MS = 14000
_SHARD_SPAWN_DELAY_MS = 90
_SHARD_POST_FALL_MS = 450
# Пол для осколков чуть ниже видимой нижней границы, чтобы часть уходила за экран.
_SHARD_FLOOR_PAD = 12.0
_SHARD_GROUND_FRICTION = 0.91
_SHARD_GROUND_ROT_DAMP = 0.86
_SHARD_VX_REST_EPS = 10.0
_SHARD_OMEGA_REST_EPS = 18.0
_SHARD_SETTLE_HOLD_MS = 520
# Отскок ARSENAL влево от удара, затем возврат (мс / пиксели).
_ARSENAL_BOUNCE_DX = -24.0
_ARSENAL_BOUNCE_OUT_MS = 88
_ARSENAL_BOUNCE_BACK_MS = 360


def _splash_card_fill() -> QColor:
    c = QColor(_SPLASH_GLASS_HEX)
    c.setAlpha(_SPLASH_CARD_BG_ALPHA)
    return c


def _splash_card_border_pen() -> QPen:
    bc = QColor(_SPLASH_GLASS_BORDER_HEX)
    bc.setAlpha(_SPLASH_GLASS_BORDER_ALPHA)
    pen = QPen(bc)
    pen.setWidth(2)
    return pen


def login_glass_card_size() -> tuple[int, int]:
    """Ширина и высота карточки «стекла» на заставке — для окна входа."""
    return _SPLASH_CARD_W, _SPLASH_CARD_H


def login_glass_panel_qss() -> str:
    """QSS панели как у QGraphicsPathItem карточки: тот же цвет, альфа и скругление."""
    fill = QColor(_SPLASH_GLASS_HEX)
    br = QColor(_SPLASH_GLASS_BORDER_HEX)
    fa = _SPLASH_CARD_BG_ALPHA / 255.0
    ba = _SPLASH_GLASS_BORDER_ALPHA / 255.0
    r = int(round(_CARD_RADIUS))
    return (
        "QFrame#LoginGlassPanel {"
        "background-color: rgba(%d,%d,%d,%.4f);"
        "border: 2px solid rgba(%d,%d,%d,%.4f);"
        "border-radius: %dpx;"
        "}"
        % (
            fill.red(),
            fill.green(),
            fill.blue(),
            fa,
            br.red(),
            br.green(),
            br.blue(),
            ba,
            r,
        )
    )


def _rounded_rect_path(rect: QRectF, radius: float) -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(rect, radius, radius)
    return p


def _dedupe_sites(pts: list[tuple[float, float]], min_dist_sq: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in pts:
        px, py = p
        if all((px - qx) ** 2 + (py - qy) ** 2 > min_dist_sq for qx, qy in out):
            out.append(p)
    return out


def _collect_shard_sites(
    clip: QPainterPath, rect: QRectF, rng: random.Random
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    n_b = max(12, _SHARD_BOUNDARY_STEPS)
    for k in range(n_b):
        t = k / float(n_b)
        q = clip.pointAtPercent(t)
        pts.append((q.x(), q.y()))

    left, top, w, h = rect.left(), rect.top(), rect.width(), rect.height()
    attempts = 0
    while len(pts) < n_b + _SHARD_INTERIOR_TARGET and attempts < _SHARD_INTERIOR_TARGET * 40:
        attempts += 1
        x = rng.uniform(left + 6.0, left + max(8.0, w - 6.0))
        y = rng.uniform(top + 6.0, top + max(8.0, h - 6.0))
        if clip.contains(QPointF(x, y)):
            pts.append((x, y))

    for _ in range(_SHARD_CHORD_LINES):
        for _try in range(28):
            ax = rng.uniform(left + 10.0, left + max(12.0, w - 10.0))
            ay = rng.uniform(top + 10.0, top + max(12.0, h - 10.0))
            bx = rng.uniform(left + 10.0, left + max(12.0, w - 10.0))
            by = rng.uniform(top + 10.0, top + max(12.0, h - 10.0))
            if not clip.contains(QPointF(ax, ay)) or not clip.contains(QPointF(bx, by)):
                continue
            for tv in _SHARD_CHORD_T:
                x = ax + (bx - ax) * tv
                y = ay + (by - ay) * tv
                if clip.contains(QPointF(x, y)):
                    pts.append((x, y))
            break
    return pts


def _tri_circumcircle(
    pts: list[tuple[float, float]], tri: tuple[int, int, int]
) -> tuple[float, float, float] | None:
    i, j, k = tri
    ax, ay = pts[i]
    bx, by = pts[j]
    cx, cy = pts[k]
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-14:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    r2 = (ax - ux) ** 2 + (ay - uy) ** 2
    return (ux, uy, r2)


def _point_in_circumcircle(px: float, py: float, cc: tuple[float, float, float] | None) -> bool:
    if cc is None:
        return False
    ux, uy, r2 = cc
    return (px - ux) ** 2 + (py - uy) ** 2 < r2 - 1e-7


def _bowyer_boundary_edges(bad: list[tuple[int, int, int]]) -> list[tuple[int, int]]:
    edges: list[tuple[int, int]] = []
    for tri in bad:
        i, j, k = tri
        for e in ((i, j), (j, k), (k, i)):
            er = (e[1], e[0])
            if er in edges:
                edges.remove(er)
            else:
                edges.append(e)
    return edges


def _bowyer_watson(coords: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    n_orig = len(coords)
    if n_orig < 3:
        return []
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    dx = xmax - xmin or 1.0
    dy = ymax - ymin or 1.0
    dmax = max(dx, dy)
    midx = (xmin + xmax) * 0.5
    midy = (ymin + ymax) * 0.5

    pts = list(coords)
    i0 = len(pts)
    pts.append((midx - 2.9 * dmax, midy - 1.0 * dmax))
    i1 = len(pts)
    pts.append((midx, midy + 2.9 * dmax))
    i2 = len(pts)
    pts.append((midx + 2.9 * dmax, midy - 1.0 * dmax))

    tris: list[tuple[int, int, int]] = [(i0, i1, i2)]
    for ip in range(n_orig):
        px, py = pts[ip]
        bad = [t for t in tris if _point_in_circumcircle(px, py, _tri_circumcircle(pts, t))]
        if not bad:
            pts[ip] = (
                px + 1e-3 * (1.0 + (ip % 5)),
                py + 1e-3 * (1.0 + ((ip * 7) % 4)),
            )
            px, py = pts[ip]
            bad = [t for t in tris if _point_in_circumcircle(px, py, _tri_circumcircle(pts, t))]
        if not bad:
            continue
        for t in bad:
            tris.remove(t)
        for a, b in _bowyer_boundary_edges(bad):
            tris.append((a, b, ip))

    return [t for t in tris if i0 not in t and i1 not in t and i2 not in t]


def _triangle_area(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) * 0.5


def _build_triangle_shard_polygons(
    rect: QRectF, radius: float, rng: random.Random
) -> list[tuple[QPolygonF, QPointF]]:
    """Треугольники Делоне внутри скруглённой карточки; локальные координаты относительно центроида."""
    clip = _rounded_rect_path(rect, radius)
    if rect.width() < 8 or rect.height() < 8:
        return []

    sites = _dedupe_sites(_collect_shard_sites(clip, rect, rng), _SHARD_SITE_MIN_DIST_SQ)
    if len(sites) < 3:
        return []
    sites.sort(key=lambda p: (p[0] + p[1], p[1]))

    tris = _bowyer_watson(sites)
    out: list[tuple[QPolygonF, QPointF]] = []
    for tri in tris:
        ia, ib, ic = tri
        a, b, c = sites[ia], sites[ib], sites[ic]
        cx = (a[0] + b[0] + c[0]) / 3.0
        cy = (a[1] + b[1] + c[1]) / 3.0
        if not clip.contains(QPointF(cx, cy)):
            continue
        if _triangle_area(a, b, c) < _SHARD_TRI_MIN_AREA:
            continue
        cen = QPointF(cx, cy)
        raw = QPolygonF([QPointF(a[0], a[1]), QPointF(b[0], b[1]), QPointF(c[0], c[1])])
        centered = QPolygonF()
        for idx in range(3):
            pt = raw.at(idx)
            centered.append(QPointF(pt.x() - cen.x(), pt.y() - cen.y()))
        out.append((centered, cen))
    return out


@dataclass
class _ShardSim:
    pos: QPointF
    vx: float
    vy: float
    rot_deg: float
    omega_deg_s: float
    resting: bool = False


def _make_text(label: str, size_pt: int) -> QGraphicsTextItem:
    it = QGraphicsTextItem(label)
    f = QFont("Arial Black", size_pt)
    if not QFontInfo(f).exactMatch():
        f = QFont("Arial", size_pt, QFont.Black)
    it.setFont(f)
    it.setDefaultTextColor(Qt.white)
    return it


def _set_text_item_font_pt(it: QGraphicsTextItem, size_pt: float) -> None:
    pt = max(8.0, float(size_pt))
    f = QFont("Arial Black", int(round(pt)))
    f.setPointSizeF(pt)
    if not QFontInfo(f).exactMatch():
        f = QFont("Arial", int(round(pt)), QFont.Black)
        f.setPointSizeF(pt)
    it.setFont(f)


def _center_item_at(it: QGraphicsTextItem, cx: float, cy: float) -> None:
    br = it.boundingRect()
    it.setPos(cx - br.width() / 2.0, cy - br.height() / 2.0)


class ArsenalSplashWidget(QWidget):
    """Полноэкранная полупрозрачная заставка; сцена 1:1 в пикселях."""

    def __init__(self, app):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window | Qt.WindowStaysOnTopHint)
        self._splash_aborted = False
        self.setObjectName("ArsenalSplashRoot")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("QWidget#ArsenalSplashRoot { background: transparent; border: none; }")

        self._cx = 400.0
        self._cy = 250.0
        self._card_rect = QRectF()

        self._view = QGraphicsView(self)
        self._view.setFrameShape(QFrame.NoFrame)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._view.setAutoFillBackground(False)
        self._view.viewport().setAutoFillBackground(False)
        self._view.setStyleSheet("background: transparent; border: none;")
        self._view.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self._view.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)

        self._scene = QGraphicsScene(self)
        self._scene.setBackgroundBrush(QBrush(Qt.NoBrush))
        self._view.setScene(self._scene)

        self._card_item: QGraphicsPathItem | None = None

        self._arsenal = _make_text("ARSENAL", _FONT_ARSENAL_PT)
        self._teplyakov = _make_text("TEPLYAKOV", _FONT_TEP_PT)
        self._connector = _make_text("x", _FONT_CONNECTOR_PT)

        for it in (self._arsenal, self._teplyakov, self._connector):
            self._scene.addItem(it)
            it.setZValue(8)

        self._flash = QGraphicsRectItem()
        self._flash.setBrush(QBrush(QColor(255, 255, 255)))
        self._flash.setPen(QPen(Qt.NoPen))
        self._flash.setZValue(50)
        self._flash.setOpacity(0.0)
        self._scene.addItem(self._flash)

        self._base_transform = QTransform()
        self._keep_refs: list = []

        self._rng = random.Random()
        self._shard_items: list[QGraphicsPolygonItem] = []
        self._shard_sims: list[_ShardSim] = []
        self._shard_timer = QTimer(self)
        self._shard_timer.setInterval(_SHARD_TIMER_MS)
        self._shard_timer.timeout.connect(self._shard_fall_tick)
        self._shard_fall_elapsed = QElapsedTimer()
        self._shard_step_elapsed = QElapsedTimer()
        self._shard_fall_done = False
        self._shard_settled_accum_ms = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._view)
        self._loading_phase_label = QLabel("", self)
        self._loading_phase_label.setAlignment(Qt.AlignCenter)
        self._loading_phase_label.setStyleSheet(
            "color: rgba(255,255,255,0.95); font-size: 13px; font-weight: 600; "
            "background: rgba(15,23,42,0.62); padding: 8px 18px; border-radius: 10px;"
        )
        self._loading_phase_label.hide()
        lay.addWidget(self._loading_phase_label, 0, Qt.AlignHCenter)

        screen = app.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        else:
            self.resize(800, 600)

        self._sync_scene_geometry()
        if _SPLASH_PLAY_INTRO_ANIMATION:
            self._place_opening()
        else:
            self._layout_final_phrase()
        self._fit()
        self._view.viewport().update()

    def closeEvent(self, event):  # noqa: N802
        self._splash_aborted = True
        self._stop_shard_fall()
        self._clear_shards()
        super().closeEvent(event)

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._sync_scene_geometry()
        if not _SPLASH_PLAY_INTRO_ANIMATION:
            self._layout_final_phrase()
        self._fit()

    def showEvent(self, ev):  # noqa: N802
        # Фиксируем полноэкранный режим, чтобы фон/анимация были без полей по краям.
        self.setWindowState(self.windowState() | Qt.WindowFullScreen)
        super().showEvent(ev)
        self._sync_scene_geometry()
        self._fit()
        self.ensure_animation_started()

    def set_loading_phase(self, text: str) -> None:
        """Короткая строка под сценой (загрузка БД / списка заказов) — не блокирует анимацию."""
        lab = getattr(self, "_loading_phase_label", None)
        if lab is None:
            return
        t = (text or "").strip()
        if not t:
            lab.hide()
            return
        lab.setText(t)
        lab.show()

    def ensure_animation_started(self) -> None:
        if getattr(self, "_splash_anim_started", False):
            return
        self._splash_anim_started = True
        if not _SPLASH_PLAY_INTRO_ANIMATION:
            self._sync_scene_geometry()
            self._layout_final_phrase()
            self._fit()
            self._view.viewport().update()
            return
        QTimer.singleShot(0, self._kick_animation_cycle)

    def _kick_animation_cycle(self) -> None:
        if getattr(self, "_splash_aborted", False):
            return
        self._fit()
        self._start_cycle()

    def _sync_scene_geometry(self) -> None:
        w = max(1, self.width())
        h = max(1, self.height())
        self._scene.setSceneRect(0.0, 0.0, float(w), float(h))
        self._cx = float(w) / 2.0
        self._cy = float(h) / 2.0
        cw = float(_SPLASH_CARD_W)
        ch = float(_SPLASH_CARD_H)
        self._card_rect = QRectF(self._cx - cw / 2.0, self._cy - ch / 2.0, cw, ch)

        if self._card_item is None:
            self._card_item = QGraphicsPathItem()
            self._card_item.setZValue(-6)
            self._scene.addItem(self._card_item)
        path = _rounded_rect_path(self._card_rect, _CARD_RADIUS)
        self._card_item.setPath(path)
        self._card_item.setBrush(QBrush(_splash_card_fill()))
        self._card_item.setPen(_splash_card_border_pen())

        self._flash.setRect(self._scene.sceneRect())
        self._fit_splash_title_fonts_to_card()

    def _fit_splash_title_fonts_to_card(self) -> None:
        """Кегль так, чтобы ARSENAL x TEPLYAKOV помещалась в ширину стекла с запасом."""
        gap = 14.0
        max_w = _SPLASH_CARD_W - _SPLASH_TITLE_INNER_PAD
        a_pt = float(_FONT_ARSENAL_PT)
        t_pt = float(_FONT_TEP_PT)
        ratio_ct = _FONT_CONNECTOR_PT / float(max(1, _FONT_TEP_PT))
        for _ in range(80):
            c_pt = max(14.0, t_pt * ratio_ct)
            _set_text_item_font_pt(self._arsenal, a_pt)
            _set_text_item_font_pt(self._teplyakov, t_pt)
            _set_text_item_font_pt(self._connector, c_pt)
            wa = self._arsenal.boundingRect().width()
            wc = self._connector.boundingRect().width()
            wt = self._teplyakov.boundingRect().width()
            total = wa + gap + wc + gap + wt
            if total <= max_w:
                break
            step = max(0.25, (total - max_w) * 0.07)
            a_pt = max(15.0, a_pt - step)
            t_pt = max(15.0, t_pt - step)

    def _fit(self) -> None:
        self._view.resetTransform()
        r = self._scene.sceneRect()
        if r.width() > 0 and r.height() > 0:
            self._view.fitInView(r, Qt.IgnoreAspectRatio)
        self._base_transform = self._view.transform()

    def _place_opening(self) -> None:
        if self._card_item is not None:
            self._card_item.setOpacity(1.0)
        _center_item_at(self._arsenal, self._cx, self._cy)
        br_t = self._teplyakov.boundingRect()
        scr = self._scene.sceneRect()
        right_edge = scr.right()
        start_cx = right_edge + _TEP_OFFSCREEN_PAD + br_t.width() / 2.0
        _center_item_at(self._teplyakov, start_cx, self._cy)
        _center_item_at(self._connector, self._cx, self._cy)
        self._connector.setOpacity(0.0)
        self._connector.setScale(0.01)
        self._arsenal.setOpacity(1.0)
        self._teplyakov.setOpacity(1.0)

    def _start_cycle(self) -> None:
        if not _SPLASH_PLAY_INTRO_ANIMATION:
            return
        if getattr(self, "_splash_aborted", False):
            return
        self._shard_fall_done = False
        self._shard_settled_accum_ms = 0
        self._stop_shard_fall()
        self._clear_shards()
        self._sync_scene_geometry()
        self._place_opening()
        self._flash.setOpacity(0.0)
        self._fit()
        QTimer.singleShot(_SPLASH_STATIC_INTRO_MS, self._fly_teplyakov)

    @staticmethod
    def _as_pointf(v) -> QPointF:
        if isinstance(v, QPointF):
            return v
        return QPointF(v)

    def _animate_item_pos(
        self,
        item: QGraphicsTextItem,
        p0: QPointF,
        p1: QPointF,
        duration_ms: int,
        easing: QEasingCurve,
        on_finished=None,
    ) -> None:
        anim = QVariantAnimation(self)
        anim.setDuration(duration_ms)
        anim.setStartValue(p0)
        anim.setEndValue(p1)
        anim.setEasingCurve(easing)

        def _apply(v):
            item.setPos(self._as_pointf(v))

        anim.valueChanged.connect(_apply)
        if on_finished is not None:
            anim.finished.connect(on_finished)
        anim.start()
        self._keep_refs.append(anim)

    def _fly_teplyakov(self) -> None:
        ar = self._arsenal.sceneBoundingRect()
        tbr = self._teplyakov.boundingRect()
        p0 = self._teplyakov.pos()
        target_left = ar.left() - tbr.width() - 6
        p1 = QPointF(target_left, p0.y())
        self._animate_item_pos(
            self._teplyakov,
            p0,
            p1,
            _FLY_MS,
            QEasingCurve(QEasingCurve.InExpo),
            self._impact,
        )

    def _impact(self) -> None:
        if getattr(self, "_splash_aborted", False):
            return
        self._flash.setOpacity(0.45)
        QTimer.singleShot(45, lambda: self._flash.setOpacity(0.0))

        self._layout_final_phrase()
        arsenal_home = QPointF(self._arsenal.pos())
        self._bounce_arsenal_impact(arsenal_home)

        self._connector.setScale(1.0)
        self._connector.setOpacity(0.0)
        c_anim = QVariantAnimation(self)
        c_anim.setDuration(140)
        c_anim.setStartValue(0.0)
        c_anim.setEndValue(1.0)
        c_anim.setEasingCurve(QEasingCurve.OutCubic)

        def _conn(v):
            self._connector.setOpacity(float(v))

        c_anim.valueChanged.connect(_conn)
        c_anim.start()
        self._keep_refs.append(c_anim)

        QTimer.singleShot(_SHARD_SPAWN_DELAY_MS, self._spawn_shards_and_fall)

    def _stop_shard_fall(self) -> None:
        self._shard_timer.stop()

    def _clear_shards(self) -> None:
        for it in self._shard_items:
            self._scene.removeItem(it)
        self._shard_items.clear()
        self._shard_sims.clear()

    def _spawn_shards_and_fall(self) -> None:
        if getattr(self, "_splash_aborted", False):
            return
        self._shard_fall_done = False
        self._shard_settled_accum_ms = 0
        self._stop_shard_fall()
        self._clear_shards()
        if self._card_item is not None:
            self._card_item.setOpacity(0.0)

        pairs = _build_triangle_shard_polygons(self._card_rect, _CARD_RADIUS, self._rng)
        if not pairs:
            self._finish_shard_fall()
            return

        brush = QBrush(_splash_card_fill())
        pen = _splash_card_border_pen()
        pen.setWidth(1)

        for poly_local, c0 in pairs:
            it = QGraphicsPolygonItem(poly_local)
            it.setBrush(brush)
            it.setPen(pen)
            it.setZValue(4)
            self._scene.addItem(it)
            vx = self._rng.uniform(-140.0, 140.0)
            vy = self._rng.uniform(-320.0, -90.0)
            omega = self._rng.uniform(-260.0, 260.0)
            it.setPos(c0)
            it.setRotation(0.0)
            self._shard_items.append(it)
            self._shard_sims.append(
                _ShardSim(pos=QPointF(c0), vx=vx, vy=vy, rot_deg=0.0, omega_deg_s=omega)
            )

        self._shard_fall_elapsed.start()
        self._shard_step_elapsed.start()
        self._shard_timer.start()

    def _shard_fall_tick(self) -> None:
        if getattr(self, "_splash_aborted", False):
            self._stop_shard_fall()
            return
        if self._shard_fall_done:
            return
        if not self._shard_sims:
            self._finish_shard_fall()
            return

        dt_ms = self._shard_step_elapsed.restart()
        dt = min(max(dt_ms / 1000.0, 0.001), 0.09)
        g = _SHARD_GRAVITY_PX_S2
        floor_y = self._scene.sceneRect().bottom() + _SHARD_FLOOR_PAD

        for sim, it in zip(self._shard_sims, self._shard_items):
            if not sim.resting:
                sim.vy += g * dt
                sim.pos = QPointF(sim.pos.x() + sim.vx * dt, sim.pos.y() + sim.vy * dt)
                sim.rot_deg += sim.omega_deg_s * dt
            it.setPos(sim.pos)
            it.setRotation(sim.rot_deg)

            br = it.sceneBoundingRect()
            if br.bottom() >= floor_y:
                over = br.bottom() - floor_y
                sim.pos = QPointF(sim.pos.x(), sim.pos.y() - over)
                it.setPos(sim.pos)
                sim.vy = 0.0
                sim.vx *= _SHARD_GROUND_FRICTION
                sim.omega_deg_s *= _SHARD_GROUND_ROT_DAMP
                if (
                    abs(sim.vx) < _SHARD_VX_REST_EPS
                    and abs(sim.omega_deg_s) < _SHARD_OMEGA_REST_EPS
                ):
                    sim.resting = True
                    sim.vx = 0.0
                    sim.omega_deg_s = 0.0

        elapsed = self._shard_fall_elapsed.elapsed()
        if self._shard_sims and all(s.resting for s in self._shard_sims):
            self._shard_settled_accum_ms += dt_ms
        else:
            self._shard_settled_accum_ms = 0

        if self._shard_settled_accum_ms >= _SHARD_SETTLE_HOLD_MS or elapsed > _SHARD_MAX_FALL_MS:
            self._finish_shard_fall()

    def _finish_shard_fall(self) -> None:
        if self._shard_fall_done:
            return
        self._shard_fall_done = True
        self._stop_shard_fall()
        if getattr(self, "_splash_aborted", False):
            self._clear_shards()
            return
        QTimer.singleShot(_SHARD_POST_FALL_MS, self._fade_out)

    def _bounce_arsenal_impact(self, home: QPointF) -> None:
        """ARSENAL отскакивает влево от удара и возвращается (OutBack)."""
        if getattr(self, "_splash_aborted", False):
            return
        peak = QPointF(home.x() + _ARSENAL_BOUNCE_DX, home.y())
        out = QVariantAnimation(self)
        out.setDuration(_ARSENAL_BOUNCE_OUT_MS)
        out.setStartValue(home)
        out.setEndValue(peak)
        out.setEasingCurve(QEasingCurve.OutQuad)
        back = QVariantAnimation(self)
        back.setDuration(_ARSENAL_BOUNCE_BACK_MS)
        back.setStartValue(peak)
        back.setEndValue(home)
        back.setEasingCurve(QEasingCurve.OutBack)

        def _apply_pos(v):
            self._arsenal.setPos(self._as_pointf(v))

        out.valueChanged.connect(_apply_pos)
        back.valueChanged.connect(_apply_pos)
        grp = QSequentialAnimationGroup(self)
        grp.addAnimation(out)
        grp.addAnimation(back)
        grp.start()
        self._keep_refs.append(grp)

    def _layout_final_phrase(self) -> None:
        gap = 14.0
        br_a = self._arsenal.boundingRect()
        br_c = self._connector.boundingRect()
        br_t = self._teplyakov.boundingRect()
        w = br_a.width() + gap + br_c.width() + gap + br_t.width()
        x = self._cx - w / 2.0
        self._arsenal.setPos(x, self._cy - br_a.height() / 2.0)
        x += br_a.width() + gap
        self._connector.setPos(x, self._cy - br_c.height() / 2.0)
        x += br_c.width() + gap
        self._teplyakov.setPos(x, self._cy - br_t.height() / 2.0)
        self._connector.setOpacity(1.0)
        self._connector.setScale(1.0)

    def _fade_out(self) -> None:
        if getattr(self, "_splash_aborted", False):
            return
        dur = 750
        targets: list = [self._arsenal, self._teplyakov, self._connector]
        if self._card_item is not None:
            targets.append(self._card_item)
        targets.extend(self._shard_items)

        def _fade_one(gitem, o0: float):
            anim = QVariantAnimation(self)
            anim.setDuration(dur)
            anim.setStartValue(o0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.InOutQuad)

            def _apply(v):
                gitem.setOpacity(float(v))

            anim.valueChanged.connect(_apply)
            anim.start()
            self._keep_refs.append(anim)

        for it in targets:
            _fade_one(it, it.opacity())
        QTimer.singleShot(dur + 1600, self._start_cycle)


def _minimal_splash(app):
    from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

    w = QWidget(None, Qt.FramelessWindowHint | Qt.Window | Qt.WindowStaysOnTopHint)
    w.setObjectName("ArsenalSplashRoot")
    w.setAttribute(Qt.WA_TranslucentBackground, True)
    w.setStyleSheet("QWidget#ArsenalSplashRoot { background: transparent; border: none; }")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lab = QLabel("ARSENAL")
    lab.setAlignment(Qt.AlignCenter)
    lab.setStyleSheet(
        "color: #ffffff; font-size: 40px; font-weight: 800; letter-spacing: 8px; "
        "background: transparent; border: none;"
    )
    lay.addWidget(lab)
    st = QLabel("", w)
    st.setAlignment(Qt.AlignCenter)
    st.setStyleSheet(
        "color: rgba(255,255,255,0.92); font-size: 13px; font-weight: 600; "
        "background: rgba(15,23,42,0.55); padding: 6px 14px; border-radius: 8px;"
    )
    st.hide()
    lay.addWidget(st, 0, Qt.AlignHCenter)

    def set_loading_phase(text: str) -> None:
        t = (text or "").strip()
        if not t:
            st.hide()
        else:
            st.setText(t)
            st.show()

    w.set_loading_phase = set_loading_phase  # type: ignore[attr-defined]

    screen = app.primaryScreen()
    if screen is not None:
        w.setGeometry(screen.geometry())
    else:
        w.resize(400, 120)
    return w


def create_arsenal_loading_splash(app):
    try:
        return ArsenalSplashWidget(app)
    except Exception:
        import traceback

        traceback.print_exc()
        return _minimal_splash(app)
