# -*- coding: utf-8 -*-
"""Статистика поставщика: KPI, таблица последних поставок, 9 графиков Plotly."""
from __future__ import annotations

import html as html_module
import os
import sys
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
    QApplication,
)
from PyQt5.QtCore import Qt, QDate, QUrl, QThread, pyqtSignal

from db import models as db_models
from ui.plotly_stats_html import (
    PLOTLY_CONFIG,
    apply_plotly_ru_layout,
    plotly_card,
    plotly_dt_column,
    plotly_page_head,
    plotly_page_tail,
)


def _period_bounds(mode, d_from: date | None, d_to_inclusive: date | None):
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


def _arrival_datetime(ad):
    if ad is None:
        return None
    if isinstance(ad, datetime):
        return ad
    if hasattr(ad, "timetuple"):
        return datetime.combine(ad, datetime.min.time())
    return None


def _enrich_delivery(d: dict) -> dict:
    w = int(d.get("width_mm") or 0)
    h = int(d.get("height_mm") or 0)
    qty = max(1, int(d.get("quantity") or 1))
    sheet_m2 = (w * h) / 1_000_000.0
    area_m2 = sheet_m2 * qty
    cost_sheet = float(d.get("cost") or 0)
    purchase_rub = cost_sheet * qty
    rub_m2 = (cost_sheet / sheet_m2) if sheet_m2 > 0 else 0.0
    dt = _arrival_datetime(d.get("arrival_date"))
    return {
        "id": int(d.get("id") or 0),
        "date": dt,
        "date_label": dt.strftime("%d.%m.%Y") if dt else "—",
        "material": (d.get("name") or "—").strip() or "—",
        "width_mm": w,
        "height_mm": h,
        "size_label": "%d × %d" % (w, h) if w and h else "—",
        "thickness_mm": d.get("thickness_mm"),
        "sheets_qty": qty,
        "sheet_m2": sheet_m2,
        "area_m2": area_m2,
        "cost_per_sheet": cost_sheet,
        "purchase_rub": purchase_rub,
        "price_m2": rub_m2,
        "warehouse_number": (d.get("warehouse_number") or "—"),
    }


def _deliveries_for_period(all_rows, dt_from, dt_to_ex, include_undated: bool):
    out = []
    for raw in all_rows or []:
        w = int(raw.get("width_mm") or 0)
        h = int(raw.get("height_mm") or 0)
        if w <= 0 or h <= 0:
            continue
        ad = raw.get("arrival_date")
        if ad is None:
            if include_undated:
                out.append(_enrich_delivery(raw))
            continue
        dt = _arrival_datetime(ad)
        if dt is None:
            continue
        if dt_from is not None and dt < dt_from:
            continue
        if dt_to_ex is not None and dt >= dt_to_ex:
            continue
        out.append(_enrich_delivery(raw))
    out.sort(key=lambda r: (r.get("date") or datetime.min, r.get("id") or 0), reverse=True)
    return out


def _stats_from_rows(rows: list) -> dict:
    if not rows:
        return {
            "deliveries_count": 0,
            "sheets_qty": 0,
            "area_m2": 0.0,
            "purchase_rub": 0.0,
            "price_per_m2": 0.0,
            "materials_count": 0,
            "avg_sheet_m2": 0.0,
            "last_arrival": None,
            "days_since_last": None,
            "top_material": None,
            "top_material_share_pct": 0.0,
        }
    area = sum(float(r["area_m2"]) for r in rows)
    purchase = sum(float(r["purchase_rub"]) for r in rows)
    sheets = sum(int(r["sheets_qty"]) for r in rows)
    by_mat = {}
    for r in rows:
        m = r["material"]
        by_mat[m] = by_mat.get(m, 0.0) + float(r["area_m2"])
    top_mat = max(by_mat.keys(), key=lambda k: by_mat[k]) if by_mat else None
    top_area = by_mat.get(top_mat) or 0.0
    dated = [r for r in rows if r.get("date")]
    last_dt = max((r["date"] for r in dated), default=None) if dated else None
    days_since = None
    if last_dt is not None:
        days_since = (date.today() - last_dt.date()).days
    sheet_areas = [float(r["sheet_m2"]) for r in rows if r["sheet_m2"] > 0]
    avg_sheet = (sum(sheet_areas) / len(sheet_areas)) if sheet_areas else 0.0
    return {
        "deliveries_count": len(rows),
        "sheets_qty": sheets,
        "area_m2": area,
        "purchase_rub": purchase,
        "price_per_m2": (purchase / area) if area > 0 else 0.0,
        "materials_count": len(by_mat),
        "avg_sheet_m2": avg_sheet,
        "last_arrival": last_dt,
        "days_since_last": days_since,
        "top_material": top_mat,
        "top_material_share_pct": (100.0 * top_area / area) if area > 0 and top_mat else 0.0,
    }


def _delivery_hover_text(r: dict) -> str:
    """Многострочная подсказка Plotly (HTML) для одной поставки."""
    th = r.get("thickness_mm")
    th_s = "%s" % th if th is not None else "—"
    return (
        "<b>Поставка</b><br>"
        "Дата: %s<br>"
        "Материал: %s<br>"
        "Размер: %s мм<br>"
        "Толщина: %s мм<br>"
        "Кол-во листов: %s<br>"
        "Площадь: %.2f м²<br>"
        "Цена за лист: %.2f ₽<br>"
        "Цена за м²: %.0f ₽<br>"
        "Итого: %.2f ₽<br>"
        "Накладная: %s"
    ) % (
        r.get("date_label") or "—",
        r.get("material") or "—",
        r.get("size_label") or "—",
        th_s,
        r.get("sheets_qty") or 0,
        float(r.get("area_m2") or 0),
        float(r.get("cost_per_sheet") or 0),
        float(r.get("price_m2") or 0),
        float(r.get("purchase_rub") or 0),
        r.get("warehouse_number") or "—",
    )


def _last_deliveries_table_html(rows: list, limit: int = 5) -> str:
    recent = list(rows[:limit])
    if not recent:
        return (
            '<p class="section-title">Последние поставки</p>'
            '<div class="data-table-wrap"><p style="padding:14px;color:#546e7a;">'
            "Нет поставок с указанной датой и размером за выбранный период.</p></div>"
        )
    hdr = [
        "Дата",
        "Материал",
        "Размер, мм",
        "мм",
        "Кол-во",
        "₽/лист",
        "₽/м²",
        "Итого, ₽",
        "Накладная",
    ]
    lines = [
        '<p class="section-title">Последние %d поставок (за период)</p>' % len(recent),
        '<div class="data-table-wrap"><table class="data-table"><thead><tr>',
    ]
    lines.append("".join("<th>%s</th>" % html_module.escape(h) for h in hdr))
    lines.append("</tr></thead><tbody>")
    for r in recent:
        th = r.get("thickness_mm")
        lines.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                html_module.escape(r.get("date_label") or "—"),
                html_module.escape(r.get("material") or "—"),
                html_module.escape(r.get("size_label") or "—"),
                html_module.escape(str(th) if th is not None else "—"),
                html_module.escape(str(r.get("sheets_qty") or 0)),
                html_module.escape("%.2f" % float(r.get("cost_per_sheet") or 0)),
                html_module.escape("%.0f" % float(r.get("price_m2") or 0)),
                html_module.escape("%.2f" % float(r.get("purchase_rub") or 0)),
                html_module.escape(str(r.get("warehouse_number") or "—")),
            )
        )
    lines.append("</tbody></table></div>")
    return "".join(lines)


def _build_supplier_plotly_html(stats, rows, supplier_name, period_label):
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go

    accent = "#1565c0"
    grid = "#cfe8fc"

    def style(fig, h=360):
        fig.update_layout(
            template="plotly_white",
            paper_bgcolor="#f5fbff",
            plot_bgcolor="#fafdff",
            font=dict(color="#0d47a1", family="Segoe UI, Roboto, sans-serif", size=12),
            title=dict(font=dict(size=13, color="#0d47a1")),
            margin=dict(l=48, r=24, t=52, b=44),
            height=h,
            autosize=True,
        )
        fig.update_xaxes(showgrid=True, gridcolor=grid, zeroline=False)
        fig.update_yaxes(showgrid=True, gridcolor=grid, zeroline=False)
        return apply_plotly_ru_layout(fig)

    blocks = [
        plotly_page_head(),
        '<div class="hero"><h1>Статистика поставщика · %s</h1>'
        '<p>Период: <b>%s</b> · Ниже — последние поставки и аналитика по складу листов.</p></div>'
        % (html_module.escape(supplier_name or "—"), html_module.escape(period_label or "—")),
    ]

    last_str = "—"
    la = stats.get("last_arrival")
    if la and hasattr(la, "strftime"):
        last_str = la.strftime("%d.%m.%Y")
    days_str = str(stats.get("days_since_last")) if stats.get("days_since_last") is not None else "—"
    top_share = float(stats.get("top_material_share_pct") or 0)
    top_name = stats.get("top_material") or "—"
    top_kpi = "%s (%.0f%%)" % (top_name, top_share) if top_share > 0 else (top_name if stats.get("deliveries_count") else "—")

    kpis = [
        ("Поступлений", str(stats.get("deliveries_count", 0))),
        ("Листов", str(stats.get("sheets_qty", 0))),
        ("Площадь, м²", "%.2f" % float(stats.get("area_m2") or 0)),
        ("Закупка, ₽", "%.0f" % float(stats.get("purchase_rub") or 0)),
        ("Средняя ₽/м²", "%.0f" % float(stats.get("price_per_m2") or 0)),
        ("Материалов", str(stats.get("materials_count", 0))),
        ("Ср. лист, м²", "%.3f" % float(stats.get("avg_sheet_m2") or 0)),
        ("Дней с поставки", days_str),
        ("Топ по площади", top_kpi),
    ]
    blocks.append('<div class="kpi-row">')
    for lab, val in kpis:
        blocks.append(
            '<div class="kpi"><div class="v">%s</div><div class="l">%s</div></div>'
            % (html_module.escape(str(val)), html_module.escape(lab))
        )
    blocks.append("</div>")
    blocks.append(
        '<p style="margin:0 0 8px 12px;font-size:12px;color:#546e7a;">'
        "Последняя поставка с датой: <b>%s</b></p>" % html_module.escape(last_str)
    )
    blocks.append(_last_deliveries_table_html(rows, 5))

    chart_rows = [r for r in rows if r.get("date")]
    if not chart_rows:
        blocks.append(
            '<div class="card"><h3>Нет данных для графиков</h3>'
            '<p style="padding:16px;color:#546e7a;">Укажите дату прихода у поставок — без неё строятся только сводные цифры.</p></div>'
        )
        blocks.append(plotly_page_tail())
        return "".join(blocks)

    df = pd.DataFrame(chart_rows)
    df["date"] = pd.to_datetime(df["date"])
    df["week"] = df["date"].dt.to_period("W").astype(str)
    df["month"] = df["date"].dt.to_period("M").astype(str)
    df = plotly_dt_column(df, "date")

    def card(title, fig, div_id):
        fig = style(fig)
        inner = fig.to_html(
            include_plotlyjs=False, full_html=False, div_id=div_id, config=PLOTLY_CONFIG
        )
        blocks.append(plotly_card(title, inner))

    blocks.append('<div class="grid">')

    mat_sum = (
        df.groupby("material", as_index=False)
        .agg(purchase_rub=("purchase_rub", "sum"), area_m2=("area_m2", "sum"))
        .sort_values("purchase_rub", ascending=True)
        .tail(8)
    )
    fig1 = px.bar(
        mat_sum,
        x="purchase_rub",
        y="material",
        orientation="h",
        title="Топ материалов по сумме закупки (₽)",
        labels={"purchase_rub": "₽", "material": ""},
        color="purchase_rub",
        color_continuous_scale="Blues",
    )
    fig1.update_layout(coloraxis_showscale=False)
    fig1.update_traces(
        hovertemplate="Материал: %{y}<br>Сумма закупки: %{x:,.0f} ₽<extra></extra>"
    )
    card("Топ материалов по деньгам", fig1, "s1")

    dfd = df.sort_values("date")
    dfd = dfd.assign(cum_rub=dfd["purchase_rub"].cumsum())
    fig2 = px.area(
        dfd,
        x="date",
        y="cum_rub",
        title="Накопленная закупка за период (₽)",
        labels={"date": "Дата", "cum_rub": "Накоплено, ₽"},
    )
    fig2.update_traces(
        line_color=accent,
        fillcolor="rgba(21,101,192,0.15)",
        hovertemplate="Дата: %{x|%d.%m.%Y}<br>Накоплено: %{y:,.0f} ₽<extra></extra>",
    )
    card("Накопленная сумма", fig2, "s2")

    top_m = mat_sum["material"].tolist()[-5:] if len(mat_sum) else []
    df_top = df[df["material"].isin(top_m)] if top_m else df
    if len(df_top):
        wk = df_top.groupby(["week", "material"], as_index=False)["purchase_rub"].sum()
        fig3 = px.bar(
            wk,
            x="week",
            y="purchase_rub",
            color="material",
            title="Поставки по неделям (топ-5 материалов)",
            labels={"purchase_rub": "Сумма, ₽", "week": "Неделя", "material": "Материал"},
            barmode="stack",
        )
        fig3.update_traces(
            hovertemplate="Неделя: %{x}<br>Материал: %{fullData.name}<br>Сумма: %{y:,.0f} ₽<extra></extra>"
        )
    else:
        fig3 = px.bar(title="Поставки по неделям")
    card("Недели × материалы", fig3, "s3")

    priced = df[df["price_m2"] > 0]
    if len(priced):
        fig4 = px.histogram(
            priced,
            x="price_m2",
            nbins=min(20, max(6, len(priced) // 3)),
            color="material",
            title="Распределение закупочной цены за м²",
            labels={"price_m2": "Цена за м², ₽", "count": "Поставок", "material": "Материал"},
        )
        fig4.update_traces(
            hovertemplate="Цена за м²: %{x:,.0f} ₽<br>Число поставок: %{y}<extra></extra>"
        )
    else:
        fig4 = px.bar(title="Цена за м² (нет данных)")
    card("Цена за м²", fig4, "s4")

    mat_area = df.groupby("material", as_index=False)["area_m2"].sum()
    if mat_area["area_m2"].sum() > 0:
        fig5 = px.treemap(
            mat_area,
            path=["material"],
            values="area_m2",
            title="Структура поставок по площади (м²)",
            labels={"area_m2": "Площадь, м²", "material": "Материал"},
        )
        fig5.update_traces(
            hovertemplate="Материал: %{label}<br>Площадь: %{value:.2f} м²<br>Доля: %{percentParent:.1%}<extra></extra>"
        )
    else:
        fig5 = px.bar(title="Площадь по материалам")
    card("Дерево площадей", fig5, "s5")

    avg_m2 = (
        df.groupby("material", as_index=False)
        .agg(avg_price_m2=("price_m2", "mean"), n=("id", "count"))
        .query("n > 0")
        .sort_values("avg_price_m2", ascending=True)
        .tail(8)
    )
    if len(avg_m2):
        fig6 = px.bar(
            avg_m2,
            x="avg_price_m2",
            y="material",
            orientation="h",
            title="Средняя закупочная цена за м² по материалу",
            labels={"avg_price_m2": "₽/м²"},
            text="avg_price_m2",
        )
        fig6.update_traces(
            texttemplate="%{text:.0f}",
            textposition="outside",
            hovertemplate="Материал: %{y}<br>Средняя цена за м²: %{x:,.0f} ₽<extra></extra>",
        )
    else:
        fig6 = px.bar(title="Средняя ₽/м²")
    card("Средняя ₽/м²", fig6, "s6")

    df_pts = df.sort_values("date").copy()
    df_pts["hover_txt"] = df_pts.apply(lambda s: _delivery_hover_text(s.to_dict()), axis=1)
    fig7 = go.Figure()
    fig7.add_trace(
        go.Scatter(
            x=df_pts["date"],
            y=df_pts["purchase_rub"],
            mode="markers",
            name="Поставки",
            marker=dict(
                size=12,
                color=accent,
                line=dict(width=1.5, color="#ffffff"),
                opacity=0.92,
            ),
            text=df_pts["hover_txt"],
            hovertemplate="%{text}<extra></extra>",
        )
    )
    fig7.update_layout(
        title="Поставка по дням",
        xaxis_title="Дата",
        yaxis_title="Сумма поставки, ₽",
    )
    fig7.update_xaxes(tickformat="%d.%m.%Y")
    card("Поставка по дням", fig7, "s7")

    th = df.groupby("thickness_mm", as_index=False).agg(
        sheets=("sheets_qty", "sum"), rub=("purchase_rub", "sum")
    )
    fig8 = px.bar(
        th,
        x="thickness_mm",
        y="sheets",
        title="Листов по толщине (мм)",
        labels={"thickness_mm": "мм", "sheets": "Листов"},
        text="sheets",
    )
    fig8.update_traces(
        marker_color="#42a5f5",
        textposition="outside",
        hovertemplate="Толщина: %{x} мм<br>Листов: %{y}<br>Сумма: %{customdata[0]:,.0f} ₽<extra></extra>",
        customdata=th[["rub"]].values,
    )
    card("Толщины", fig8, "s8")

    df_sc = df.copy()
    df_sc["hover_txt"] = df_sc.apply(lambda s: _delivery_hover_text(s.to_dict()), axis=1)
    fig9 = go.Figure()
    sizes = (df_sc["purchase_rub"].clip(lower=1) ** 0.45) * 3.0
    fig9.add_trace(
        go.Scatter(
            x=df_sc["sheet_m2"],
            y=df_sc["price_m2"],
            mode="markers",
            marker=dict(
                size=sizes,
                sizemode="diameter",
                sizeref=2.0 * max(sizes.max(), 1) / (36.0**2),
                color=accent,
                line=dict(width=1, color="#fff"),
                opacity=0.85,
            ),
            text=df_sc["hover_txt"],
            hovertemplate="%{text}<extra></extra>",
            name="Поставки",
        )
    )
    fig9.update_layout(
        title="Размер листа и цена за м²",
        xaxis_title="Площадь одного листа, м²",
        yaxis_title="Цена за м², ₽",
    )
    card("Размер и цена", fig9, "s9")

    blocks.append("</div>")
    blocks.append(plotly_page_tail())
    return "".join(blocks)


class _SupplierStatsLoadWorker(QThread):
    finished_ok = pyqtSignal(dict, list, str)
    failed = pyqtSignal(str)

    def __init__(self, supplier_id, dt_from, dt_to_ex, include_undated, label, parent=None):
        super().__init__(parent)
        self._supplier_id = int(supplier_id)
        self._dt_from = dt_from
        self._dt_to_ex = dt_to_ex
        self._include_undated = include_undated
        self._label = label

    def run(self):
        try:
            agg = db_models.get_supplier_stats_aggregate(
                self._supplier_id, self._dt_from, self._dt_to_ex
            )
            deliveries = db_models.get_supplier_deliveries(self._supplier_id, limit=800)
            rows = _deliveries_for_period(
                deliveries, self._dt_from, self._dt_to_ex, self._include_undated
            )
            stats = _stats_from_rows(rows)
            if agg:
                for k in (
                    "deliveries_count",
                    "sheets_qty",
                    "area_m2",
                    "purchase_rub",
                    "price_per_m2",
                    "materials_count",
                    "avg_sheet_m2",
                    "last_arrival",
                    "days_since_last",
                    "top_material",
                    "top_material_share_pct",
                ):
                    if agg.get(k) is not None:
                        stats[k] = agg[k]
            self.finished_ok.emit(stats, rows, self._label)
        except Exception as ex:
            self.failed.emit(str(ex))


class SupplierStatisticsDialog(QDialog):
    def __init__(self, supplier_id: int, supplier_name: str, parent=None):
        super().__init__(parent)
        self._supplier_id = int(supplier_id)
        self._supplier_name = supplier_name or "—"
        self._loader = None
        self.setWindowTitle("Статистика поставщика · %s" % self._supplier_name)
        self._web = None
        self._build_ui()
        self._reload()

    def _build_ui(self):
        self.setStyleSheet(
            """
            SupplierStatisticsDialog { background: #e8f4fc; }
            QLabel { color: #0d47a1; }
            QComboBox {
                background: #f5fbff; border: 1px solid #64b5f6; border-radius: 8px;
                padding: 6px 30px 6px 10px; min-height: 26px; color: #0d47a1; font-size: 13px;
            }
            QDateEdit {
                background: #f5fbff; border: 1px solid #64b5f6; border-radius: 8px;
                padding: 4px 8px; min-height: 26px; color: #0d47a1;
            }
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
            ):
                try:
                    gs.setAttribute(attr, True)
                except Exception:
                    pass
            self._web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            lay.addWidget(self._web, 1)
        except Exception:
            self._web = None
            lay.addWidget(QLabel("Установите PyQtWebEngine, plotly, pandas."), 1)

        self._loading_lbl = QLabel("Загрузка…")
        self._loading_lbl.setAlignment(Qt.AlignCenter)
        self._loading_lbl.setStyleSheet(
            "font-size: 16px; font-weight: 600; color: #1565c0; padding: 24px;"
        )
        lay.addWidget(self._loading_lbl)

    def _on_period_kind_changed(self):
        custom = self._combo.currentData() == "custom"
        self._d1.setEnabled(custom)
        self._d2.setEnabled(custom)
        if not custom:
            self._reload()

    def _apply_window_size_to_screen(self):
        geo = None
        try:
            scr = self.screen()
            if scr is not None:
                geo = scr.availableGeometry()
        except Exception:
            pass
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

    def showEvent(self, ev):
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
        include_undated = mode == "all"
        label = _period_label_ru(mode, d1, d2)
        if self._loader is not None and self._loader.isRunning():
            return
        self._loading_lbl.setVisible(True)
        self._loader = _SupplierStatsLoadWorker(
            self._supplier_id, dt_from, dt_to_ex, include_undated, label, self
        )
        self._loader.finished_ok.connect(self._on_stats_loaded)
        self._loader.failed.connect(self._on_stats_failed)
        self._loader.start()

    def _on_stats_loaded(self, stats, rows, label):
        self._loading_lbl.setVisible(False)
        try:
            html = _build_supplier_plotly_html(stats, rows, self._supplier_name, label)
        except Exception as ex:
            QMessageBox.warning(self, "Статистика", "Не удалось построить отчёт: %s" % ex)
            return
        self._web.setHtml(html, QUrl("about:blank"))

    def _on_stats_failed(self, msg):
        self._loading_lbl.setVisible(False)
        QMessageBox.warning(self, "Статистика", "Не удалось загрузить данные: %s" % msg)
