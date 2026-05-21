# -*- coding: utf-8 -*-
"""Окно статистики клиента: Plotly Express, периоды, светло-голубая тема."""
from __future__ import annotations

import html as html_module
import sys
import os
from datetime import datetime, timedelta, date

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QComboBox,
    QDateEdit,
    QFrame,
    QMessageBox,
    QSizePolicy,
    QWidget,
    QScrollArea,
    QApplication,
)
from PyQt5.QtCore import Qt, QDate, QUrl

from db import models as db_models
from db_main import order_status_to_ru
from ui.plotly_stats_html import (
    PLOTLY_CONFIG,
    apply_plotly_ru_layout,
    plotly_card,
    plotly_page_head,
    plotly_page_tail,
)


def _parse_mirror_order_metrics(order_row):
    raw = order_row.get("blocks_calc_json")
    if not raw or not str(raw).strip():
        return None, 0, "—"
    try:
        from logic.blocks_bundle import parse_bundle, bundle_grand_total_rub

        _, products = parse_bundle(str(raw))
        if not products:
            return None, 0, "—"
        kinds = {str(p.get("kind") or "glass_mirror").strip() or "glass_mirror" for p in products}
        if len(kinds) == 1:
            only = list(kinds)[0]
            type_label = (
                "Стекло / зеркало" if only == "glass_mirror" else ("Фасады" if only == "facade" else only)
            )
        else:
            type_label = "Смешанный"
        return int(bundle_grand_total_rub(products) or 0), len(products), type_label
    except Exception:
        return None, 0, "—"


def _mirror_rows_to_records(rows):
    out = []
    for o in rows or []:
        tot, nprod, mix = _parse_mirror_order_metrics(o)
        ca = o.get("created_at")
        if isinstance(ca, datetime):
            dt = ca
        elif hasattr(ca, "timetuple"):
            try:
                dt = datetime.combine(ca, datetime.min.time())
            except Exception:
                dt = None
        else:
            dt = None
        if dt is None:
            continue
        st = order_status_to_ru(o.get("status"))
        out.append(
            {
                "created_at": dt,
                "order_id": int(o.get("id") or 0),
                "kind": "Заказ (стекло / фасады)",
                "total_rub": tot if tot is not None else 0,
                "products": int(nprod or 0),
                "mix": mix,
                "status": st or "—",
            }
        )
    return out


def _sales_rows_to_records(rows, counts_by_id):
    out = []
    for o in rows or []:
        ca = o.get("created_at")
        if isinstance(ca, datetime):
            dt = ca
        elif hasattr(ca, "timetuple"):
            try:
                dt = datetime.combine(ca, datetime.min.time())
            except Exception:
                dt = None
        else:
            dt = None
        if dt is None:
            continue
        oid = int(o.get("id") or 0)
        c = counts_by_id.get(oid) or {}
        nprod = int(c.get("items_count") or 0)
        try:
            tot = int(o.get("total_rub") or 0)
        except (TypeError, ValueError):
            tot = 0
        st = db_models.sales_status_to_ru(o.get("status"))
        out.append(
            {
                "created_at": dt,
                "order_id": oid,
                "kind": "Продажа",
                "total_rub": tot,
                "products": nprod,
                "mix": "Продажа",
                "status": st or "—",
            }
        )
    return out


def _period_bounds(mode, d_from: date | None, d_to_inclusive: date | None):
    """Возвращает (dt_from, dt_to_exclusive) или (None, None) для «всё время»."""
    now = datetime.now()
    if mode == "all":
        return None, None
    if mode == "month30":
        start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_ex = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, end_ex
    if mode == "calendar_month":
        start = datetime(now.year, now.month, 1)
        if now.month == 12:
            end_ex = datetime(now.year + 1, 1, 1)
        else:
            end_ex = datetime(now.year, now.month + 1, 1)
        return start, end_ex
    if mode == "custom" and d_from and d_to_inclusive:
        start = datetime.combine(d_from, datetime.min.time())
        end_ex = datetime.combine(d_to_inclusive + timedelta(days=1), datetime.min.time())
        return start, end_ex
    return None, None


def _period_label_ru(mode, d_from, d_to):
    if mode == "all":
        return "Всё время"
    if mode == "month30":
        return "Последние 30 дней"
    if mode == "calendar_month":
        return "Текущий календарный месяц"
    if mode == "custom" and d_from and d_to:
        return "%s — %s" % (d_from.strftime("%d.%m.%Y"), d_to.strftime("%d.%m.%Y"))
    return "—"


def _build_plotly_html(df, client_name: str, period_label: str) -> str:
    import pandas as pd
    import plotly.express as px

    accent = "#1565c0"
    grid = "#cfe8fc"

    def style(fig, h=360):
        fig.update_layout(
            template="plotly_white",
            paper_bgcolor="#f5fbff",
            plot_bgcolor="#fafdff",
            font=dict(color="#0d47a1", family="Segoe UI, Roboto, 'Helvetica Neue', sans-serif", size=12),
            title=dict(font=dict(size=14, color="#0d47a1")),
            margin=dict(l=48, r=28, t=48, b=44),
            height=h,
            autosize=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        fig.update_xaxes(showgrid=True, gridcolor=grid, zeroline=False)
        fig.update_yaxes(showgrid=True, gridcolor=grid, zeroline=False)
        return apply_plotly_ru_layout(fig)

    blocks = [plotly_page_head()]
    esc_name = html_module.escape(client_name or "—")
    esc_per = html_module.escape(period_label or "—")
    blocks.append(
        '<div class="hero"><h1>Статистика клиента · %s</h1><p>Период: <b>%s</b> · '
        "Графики интерактивны: колесо масштаб, перетаскивание области, двойной клик — сброс.</p></div>"
        % (esc_name, esc_per)
    )

    if df is None or len(df) == 0:
        blocks.append(
            '<div class="card"><h3>Нет данных</h3><p style="padding:16px;color:#546e7a;">'
            "За выбранный период нет заказов или продаж с датой создания.</p></div>"
        )
        blocks.append(plotly_page_tail())
        return "".join(blocks)

    df = df.copy()
    df["date"] = df["created_at"].dt.normalize()
    df["week_start"] = df["created_at"].dt.to_period("W").apply(lambda p: str(p.start_time.date()))
    df["weekday_short"] = df["created_at"].dt.dayofweek.map(
        {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}
    )

    n_orders = len(df)
    sum_rub = int(df["total_rub"].sum())
    avg_rub = int(df["total_rub"].mean()) if n_orders else 0
    avg_prod = round(df["products"].mean(), 2) if n_orders else 0
    med_days = "—"
    if n_orders > 1:
        s = df.sort_values("created_at")["created_at"]
        gaps = s.diff().dt.days.dropna()
        if len(gaps):
            med_days = str(int(gaps.median()))

    blocks.append('<div class="kpi-row">')
    kpis = [
        ("Заказов / продаж", str(n_orders)),
        ("Сумма, ₽", "%s" % f"{sum_rub:,}".replace(",", " ")),
        ("Средний чек, ₽", "%s" % f"{avg_rub:,}".replace(",", " ")),
        ("Изделий в ср.", str(avg_prod)),
        ("Медиана дней между заказами", med_days),
    ]
    for lab, val in kpis:
        blocks.append(
            '<div class="kpi"><div class="v">%s</div><div class="l">%s</div></div>'
            % (html_module.escape(str(val)), html_module.escape(lab))
        )
    blocks.append("</div><div class=\"grid\">")

    def card(title, fig, div_id):
        fig = style(fig)
        inner = fig.to_html(
            include_plotlyjs=False, full_html=False, div_id=div_id, config=PLOTLY_CONFIG
        )
        blocks.append(plotly_card(title, inner))

    # 1) Распределение сумм
    fig1 = px.histogram(
        df,
        x="total_rub",
        nbins=min(24, max(8, n_orders // 2)),
        color="kind",
        title="Распределение сумм заказов (₽)",
        labels={"total_rub": "Сумма, ₽", "count": "Кол-во", "kind": "Тип"},
    )
    fig1.update_traces(
        marker_line_width=1,
        marker_line_color="white",
        hovertemplate="Сумма: %{x:,.0f} ₽<br>Тип: %{legendgroup}<br>Заказов в интервале: %{y}<extra></extra>",
    )
    card("Гистограмма сумм", fig1, "c1")

    # 2) Изделий в заказе
    fig2 = px.histogram(
        df,
        x="products",
        nbins=min(20, max(5, int(df["products"].max() or 5))),
        color="kind",
        title="Сколько изделий обычно в заказе",
        labels={"products": "Число изделий", "kind": "Тип"},
    )
    fig2.update_traces(
        hovertemplate="Изделий: %{x}<br>Тип: %{legendgroup}<br>Заказов: %{y}<extra></extra>"
    )
    card("Изделий в заказе", fig2, "c2")

    # 3) Статусы
    st_counts = df.groupby(["status", "kind"]).size().reset_index(name="n")
    fig3 = px.bar(
        st_counts,
        x="status",
        y="n",
        color="kind",
        title="Заказы по статусам",
        labels={"status": "Статус", "n": "Количество", "kind": "Тип"},
    )
    fig3.update_xaxes(tickangle=-28)
    fig3.update_traces(
        hovertemplate="Статус: %{x}<br>Тип: %{fullData.name}<br>Количество: %{y}<extra></extra>"
    )
    card("Статусы", fig3, "c3")

    # 4) Тип состава (стекло/фасады/смеш.)
    mix_counts = (
        df[df["kind"].str.contains("Заказ", na=False)].groupby("mix").size().reset_index(name="n")
    )
    if len(mix_counts):
        fig4 = px.pie(
            mix_counts,
            names="mix",
            values="n",
            title="Что чаще заказывает (по составу)",
            labels={"mix": "Состав", "n": "Заказов"},
        )
        fig4.update_traces(
            hovertemplate="Состав: %{label}<br>Заказов: %{value}<br>Доля: %{percent:.1%}<extra></extra>"
        )
        card("Состав заказов", fig4, "c4")
    else:
        fig4 = px.bar(pd.DataFrame({"x": ["—"], "y": [0]}), x="x", y="y", title="Состав заказов (нет данных)")
        card("Состав заказов", fig4, "c4")

    # 5) Динамика по неделям
    wk = df.groupby("week_start", as_index=False).agg(orders=("order_id", "count"), revenue=("total_rub", "sum"))
    fig5 = px.bar(
        wk,
        x="week_start",
        y="orders",
        title="Активность: число заказов по неделям",
        labels={"week_start": "Неделя (старт)", "orders": "Заказов"},
    )
    fig5.update_traces(
        marker_color=accent,
        marker_line_color="#0d47a1",
        marker_line_width=0.5,
        hovertemplate="Неделя: %{x}<br>Заказов: %{y}<br>Выручка: %{customdata[0]:,.0f} ₽<extra></extra>",
        customdata=wk[["revenue"]].values,
    )
    card("Частота по неделям", fig5, "c5")

    # 6) Кумулятивная выручка
    dfd = df.sort_values("created_at")
    dfd = dfd.assign(cumsum=dfd["total_rub"].cumsum())
    fig6 = px.line(
        dfd,
        x="created_at",
        y="cumsum",
        color="kind",
        title="Накопленная сумма по времени (₽)",
        labels={"created_at": "Дата", "cumsum": "Накоплено, ₽", "kind": "Тип"},
        markers=True,
    )
    fig6.update_traces(
        hovertemplate="Дата: %{x|%d.%m.%Y %H:%M}<br>Накоплено: %{y:,.0f} ₽<br>Тип: %{fullData.name}<extra></extra>"
    )
    card("Кумулятивная сумма", fig6, "c6")

    # 7) День недели
    wd_ord = df.groupby("weekday_short", as_index=False).agg(n=("order_id", "count"))
    order_map = {"Пн": 0, "Вт": 1, "Ср": 2, "Чт": 3, "Пт": 4, "Сб": 5, "Вс": 6}
    wd_ord["_o"] = wd_ord["weekday_short"].map(order_map).fillna(99)
    wd_ord = wd_ord.sort_values("_o")
    fig7 = px.bar(
        wd_ord,
        x="weekday_short",
        y="n",
        title="В какой день недели чаще оформляет",
        labels={"weekday_short": "День", "n": "Заказов"},
    )
    fig7.update_traces(
        marker_color="#1976d2",
        hovertemplate="День: %{x}<br>Заказов: %{y}<extra></extra>",
    )
    card("День недели", fig7, "c7")

    # 8) Scatter сумма vs изделия
    import plotly.graph_objects as go

    fig8 = go.Figure()
    for kind_name, part in df.groupby("kind"):
        pt_sizes = (part["total_rub"].clip(lower=1) ** 0.45) * 2.5 + 8.0
        fig8.add_trace(
            go.Scatter(
                x=part["products"],
                y=part["total_rub"],
                mode="markers",
                name=str(kind_name),
                marker=dict(size=pt_sizes, sizemode="diameter", opacity=0.82),
                customdata=part[["order_id", "status", "mix"]].values,
                hovertemplate=(
                    "Тип: %{fullData.name}<br>"
                    "Изделий: %{x}<br>"
                    "Сумма: %{y:,.0f} ₽<br>"
                    "Заказ № %{customdata[0]}<br>"
                    "Статус: %{customdata[1]}<br>"
                    "Состав: %{customdata[2]}"
                    "<extra></extra>"
                ),
            )
        )
    fig8.update_layout(
        title="Сумма заказа vs число изделий",
        xaxis_title="Число изделий",
        yaxis_title="Сумма, ₽",
    )
    card("Сумма и объём", fig8, "c8")

    # 9) Столбцы по месяцам (заказов)
    df["month"] = df["created_at"].dt.to_period("M").astype(str)
    mo = df.groupby("month", as_index=False).agg(n=("order_id", "count"), rub=("total_rub", "sum"))
    fig10 = px.line(
        mo,
        x="month",
        y="rub",
        markers=True,
        title="Сумма по месяцам (₽)",
        labels={"month": "Месяц", "rub": "Сумма, ₽"},
    )
    fig10.update_traces(
        line_color="#1565c0",
        marker=dict(size=9),
        hovertemplate="Месяц: %{x}<br>Сумма: %{y:,.0f} ₽<br>Заказов: %{customdata[0]}<extra></extra>",
        customdata=mo[["n"]].values,
    )
    card("Динамика по месяцам", fig10, "c9")

    blocks.append("</div>")
    blocks.append(plotly_page_tail())
    return "".join(blocks)


class ClientStatisticsDialog(QDialog):
    """Интерактивные графики Plotly в QWebEngineView."""

    def __init__(self, client_id: int, client_name: str, parent=None):
        super().__init__(parent)
        self._client_id = int(client_id)
        self._client_name = client_name or "—"
        self.setWindowTitle("Статистика клиента · %s" % self._client_name)
        self._web = None
        self._build_ui()
        self._reload()

    def _build_ui(self):
        self.setStyleSheet(
            """
            ClientStatisticsDialog { background: #e8f4fc; }
            QLabel { color: #0d47a1; }
            QComboBox {
                background: #f5fbff;
                border: 1px solid #64b5f6;
                border-radius: 8px;
                padding: 6px 30px 6px 10px;
                min-height: 26px;
                color: #0d47a1;
                font-size: 13px;
            }
            QComboBox:hover { border-color: #1565c0; background: #ffffff; }
            QComboBox:focus { border-color: #1565c0; }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                border: none;
                border-left: 1px solid #90caf9;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #e3f2fd, stop:1 #64b5f6);
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #0d47a1;
                width: 0;
                height: 0;
                margin-right: 2px;
            }
            QComboBox QAbstractItemView {
                background: #f5fbff;
                color: #0d47a1;
                selection-background-color: #1565c0;
                selection-color: #ffffff;
                outline: none;
                border: 1px solid #64b5f6;
                border-radius: 8px;
                padding: 4px;
            }
            QComboBox QAbstractItemView::item { min-height: 24px; border-radius: 6px; padding: 4px 8px; }
            QComboBox QAbstractItemView::item:hover { background: #bbdefb; color: #0d47a1; }
            QDateEdit {
                background: #f5fbff;
                border: 1px solid #64b5f6;
                border-radius: 8px;
                padding: 4px 8px;
                min-height: 26px;
                color: #0d47a1;
            }
            QDateEdit:focus { border-color: #1565c0; }
            QDateEdit::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 24px;
                border: none;
                border-left: 1px solid #90caf9;
                border-top-right-radius: 8px;
                border-bottom-right-radius: 8px;
                background: #bbdefb;
            }
            QDateEdit::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #0d47a1;
                width: 0;
                height: 0;
            }
            QScrollBar:vertical {
                background: #e3f2fd;
                width: 12px;
                margin: 0;
                border-radius: 6px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #90caf9, stop:1 #1565c0);
                min-height: 36px;
                border-radius: 6px;
                margin: 2px;
            }
            QScrollBar::handle:vertical:hover { background: #0d47a1; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; subcontrol-origin: margin; }
            QScrollBar:horizontal {
                background: #e3f2fd;
                height: 12px;
                margin: 0;
                border-radius: 6px;
                border: none;
            }
            QScrollBar::handle:horizontal {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #90caf9, stop:1 #1565c0);
                min-width: 36px;
                border-radius: 6px;
                margin: 2px;
            }
            QScrollBar::handle:horizontal:hover { background: #0d47a1; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            """
        )
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        top = QFrame()
        top.setStyleSheet(
            "QFrame { background: #e3f2fd; border: 1px solid #90caf9; border-radius: 10px; padding: 8px; }"
        )
        hl = QHBoxLayout(top)
        hl.setSpacing(10)
        hl.addWidget(QLabel("Период:"))
        self._combo = QComboBox()
        self._combo.addItem("Последние 30 дней", "month30")
        self._combo.addItem("Текущий календарный месяц", "calendar_month")
        self._combo.addItem("Всё время", "all")
        self._combo.addItem("Свой период…", "custom")
        self._combo.setCurrentIndex(0)
        self._combo.currentIndexChanged.connect(self._on_period_kind_changed)
        hl.addWidget(self._combo, 1)
        hl.addWidget(QLabel("с:"))
        self._d1 = QDateEdit()
        self._d1.setCalendarPopup(True)
        self._d1.setDisplayFormat("dd.MM.yyyy")
        self._d1.setDate(QDate.currentDate().addMonths(-1))
        self._d1.setEnabled(False)
        hl.addWidget(self._d1)
        hl.addWidget(QLabel("по:"))
        self._d2 = QDateEdit()
        self._d2.setCalendarPopup(True)
        self._d2.setDisplayFormat("dd.MM.yyyy")
        self._d2.setDate(QDate.currentDate())
        self._d2.setEnabled(False)
        hl.addWidget(self._d2)
        btn = QPushButton("Обновить")
        btn.setStyleSheet(
            "QPushButton { background:#1565c0; color:#fff; font-weight:600; padding:8px 18px; "
            "border-radius:8px; border:1px solid #0d47a1; } QPushButton:hover { background:#1976d2; }"
        )
        btn.clicked.connect(self._reload)
        hl.addWidget(btn)
        lay.addWidget(top)

        try:
            from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings

            self._web = QWebEngineView()
            gs = QWebEngineSettings.globalSettings()
            for attr in (
                QWebEngineSettings.JavascriptEnabled,
                QWebEngineSettings.LocalContentCanAccessRemoteUrls,
                QWebEngineSettings.PluginsEnabled,
            ):
                try:
                    gs.setAttribute(attr, True)
                except Exception:
                    pass
            try:
                self._web.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, True)
                self._web.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
            except Exception:
                pass
            self._web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            lay.addWidget(self._web, 1)
        except Exception:
            self._web = None
            lay.addWidget(
                QLabel(
                    "Для графиков установите PyQtWebEngine:\n"
                    "pip install PyQtWebEngine plotly pandas"
                ),
                1,
            )

    def _on_period_kind_changed(self):
        mode = self._combo.currentData()
        custom = mode == "custom"
        self._d1.setEnabled(custom)
        self._d2.setEnabled(custom)
        if not custom:
            self._reload()

    def _apply_window_size_to_screen(self):
        """90% ширины и высоты доступной области экрана, по центру."""
        geo = None
        try:
            scr = self.screen()
            if scr is not None:
                geo = scr.availableGeometry()
        except Exception:
            scr = None
        if geo is None:
            desk = QApplication.desktop()
            if desk is not None:
                geo = desk.availableGeometry(self)
        if geo is None:
            return
        w = max(640, int(geo.width() * 0.9))
        h = max(480, int(geo.height() * 0.9))
        self.resize(w, h)
        fg = self.frameGeometry()
        fg.moveCenter(geo.center())
        self.move(fg.topLeft())

    def showEvent(self, ev):  # noqa: N802
        super().showEvent(ev)
        self._apply_window_size_to_screen()

    def _reload(self):
        if self._web is None:
            return
        mode = self._combo.currentData() or "month30"
        d1 = self._d1.date().toPyDate() if self._d1.isEnabled() else None
        d2 = self._d2.date().toPyDate() if self._d2.isEnabled() else None
        if mode == "custom" and d1 and d2 and d1 > d2:
            QMessageBox.warning(self, "Период", "Дата «с» не может быть позже «по».")
            return
        dt_from, dt_to_ex = _period_bounds(mode, d1, d2)
        try:
            mir = db_models.get_orders_by_client_id_in_range(self._client_id, dt_from, dt_to_ex)
            sal = db_models.list_sales_orders_for_client_id(self._client_id, dt_from, dt_to_ex)
        except Exception as ex:
            QMessageBox.warning(self, "Статистика", "Не удалось загрузить данные: %s" % ex)
            return
        sids = [int(r.get("id") or 0) for r in (sal or []) if r.get("id")]
        counts = db_models.list_sales_items_counts_bulk(sids) if sids else {}

        rec = _mirror_rows_to_records(mir) + _sales_rows_to_records(sal, counts)
        try:
            import pandas as pd

            df = pd.DataFrame(rec) if rec else pd.DataFrame()
            if len(df):
                df["created_at"] = pd.to_datetime(df["created_at"])
        except Exception as ex:
            QMessageBox.critical(self, "Статистика", "Нужны библиотеки pandas/plotly:\n%s" % ex)
            return

        label = _period_label_ru(mode, d1, d2)
        try:
            html = _build_plotly_html(df, self._client_name, label)
        except Exception as ex:
            QMessageBox.warning(self, "Статистика", "Не удалось построить графики: %s" % ex)
            return
        self._web.setHtml(html, QUrl("about:blank"))
