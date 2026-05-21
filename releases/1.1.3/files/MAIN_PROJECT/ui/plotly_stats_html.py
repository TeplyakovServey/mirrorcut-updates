# -*- coding: utf-8 -*-
"""Общие стили и скрипт ресайза Plotly для окон статистики (клиент / поставщик)."""
from __future__ import annotations

import html as html_module


PLOTLY_CDN = (
    '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>'
)

PLOTLY_STATS_CSS = """
  * { box-sizing: border-box; }
  body { margin:0; padding:14px; background: linear-gradient(180deg,#e3f2fd 0%,#bbdefb 100%);
    color:#0d47a1; font-family: Segoe UI, Roboto, 'Helvetica Neue', sans-serif; }
  .wrap { max-width: 1600px; margin: 0 auto; width: 100%; }
  .hero { background: linear-gradient(120deg,#1565c0 0%,#42a5f5 55%,#64b5f6 100%);
    color:#fff; padding: 18px 22px; border-radius: 14px; margin-bottom: 16px;
    box-shadow: 0 6px 24px rgba(21,101,192,.25); }
  .hero h1 { margin:0; font-size: 1.45rem; font-weight: 700; letter-spacing: .02em; }
  .hero p { margin: 8px 0 0; opacity: .95; font-size: .98rem; line-height: 1.45; }
  .kpi-row { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
  .kpi { flex: 1 1 140px; min-width: 120px; background: #fff; border-radius: 12px; padding: 12px 14px;
    border: 1px solid #90caf9; box-shadow: 0 2px 10px rgba(13,71,161,.08); }
  .kpi .v { font-size: 1.25rem; font-weight: 800; color: #0d47a1; }
  .kpi .l { font-size: 11px; color: #546e7a; text-transform: uppercase; letter-spacing: .04em; margin-top: 4px; }
  .section-title { margin: 18px 4px 10px; font-size: 14px; font-weight: 700; color: #1565c0; }
  .data-table-wrap { background: #fff; border-radius: 12px; border: 1px solid #90caf9;
    box-shadow: 0 2px 12px rgba(21,101,192,.1); overflow-x: auto; margin-bottom: 18px; }
  table.data-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  table.data-table th { background: #e3f2fd; color: #0d47a1; text-align: left; padding: 10px 8px;
    border-bottom: 1px solid #90caf9; white-space: nowrap; }
  table.data-table td { padding: 8px; border-bottom: 1px solid #e3f2fd; color: #263238; vertical-align: top; }
  table.data-table tr:nth-child(even) td { background: #fafdff; }
  .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; width: 100%; }
  @media (max-width: 960px) { .grid { grid-template-columns: minmax(0, 1fr); } }
  .card { background: #fff; border-radius: 12px; padding: 8px 8px 4px; border: 1px solid #90caf9;
    box-shadow: 0 3px 14px rgba(21,101,192,.1); min-height: 380px; min-width: 0; overflow: hidden; }
  .card h3 { margin: 8px 12px 4px; font-size: 13px; color: #1565c0; font-weight: 700; }
  .plot-box { width: 100%; max-width: 100%; min-width: 0; overflow: hidden; }
  .plot-box .plotly-graph-div { width: 100% !important; max-width: 100% !important; }
  .js-plotly-plot, .plotly-graph-div { max-width: 100% !important; }
  ::-webkit-scrollbar { width: 11px; height: 11px; margin: 0; }
  ::-webkit-scrollbar-track { background: #e3f2fd; border-radius: 8px; border: none; }
  ::-webkit-scrollbar-thumb {
    background: linear-gradient(180deg,#90caf9,#1565c0);
    border-radius: 8px; border: 2px solid #e3f2fd; min-height: 28px;
  }
  ::-webkit-scrollbar-thumb:hover { background: #0d47a1; }
  ::-webkit-scrollbar-corner { background: #e3f2fd; }
  ::-webkit-scrollbar:horizontal { height: 11px; margin: 0; border-radius: 8px; border: none; }
  ::-webkit-scrollbar-track:horizontal { background: #e3f2fd; border-radius: 8px; }
  ::-webkit-scrollbar-thumb:horizontal {
    background: linear-gradient(90deg,#90caf9,#1565c0);
    border-radius: 8px; border: 2px solid #e3f2fd; min-width: 28px;
  }
  ::-webkit-scrollbar-thumb:horizontal:hover { background: #0d47a1; }
  ::-webkit-scrollbar-add-line:vertical, ::-webkit-scrollbar-sub-line:vertical { height: 0; }
  ::-webkit-scrollbar-add-line:horizontal, ::-webkit-scrollbar-sub-line:horizontal { width: 0; }
"""

PLOTLY_HOVERLABEL = dict(
    bgcolor="#ffffff",
    bordercolor="#1565c0",
    font=dict(size=12, color="#0d47a1", family="Segoe UI, Roboto, 'Helvetica Neue', sans-serif"),
)


def apply_plotly_ru_layout(fig):
    """Подписи при наведении и легенда — на русском оформлении."""
    fig.update_layout(hoverlabel=PLOTLY_HOVERLABEL)
    fig.update_layout(
        legend=dict(font=dict(size=11, color="#0d47a1")),
        modebar=dict(bgcolor="rgba(255,255,255,0.8)", color="#1565c0", activecolor="#0d47a1"),
    )
    return fig

PLOTLY_RESIZE_JS = """
<script>
(function() {
  function resizeAllPlots() {
    if (typeof Plotly === 'undefined') return;
    var nodes = document.querySelectorAll('.plotly-graph-div');
    for (var i = 0; i < nodes.length; i++) {
      try { Plotly.Plots.resize(nodes[i]); } catch (e) {}
    }
  }
  window.__mirrorcutResizePlots = resizeAllPlots;
  window.addEventListener('resize', resizeAllPlots);
  window.addEventListener('load', function() {
    setTimeout(resizeAllPlots, 60);
    setTimeout(resizeAllPlots, 280);
    setTimeout(resizeAllPlots, 900);
  });
  if (typeof Plotly !== 'undefined') {
    var obs = new MutationObserver(function() { setTimeout(resizeAllPlots, 40); });
    obs.observe(document.body, { childList: true, subtree: true });
  }
})();
</script>
"""


def plotly_page_head(extra_css: str = "") -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/>"
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        "<style>%s%s</style>%s</head><body><div class=\"wrap\">"
        % (PLOTLY_STATS_CSS, extra_css or "", PLOTLY_CDN)
    )


def plotly_page_tail() -> str:
    return "</div>%s</body></html>" % PLOTLY_RESIZE_JS


def plotly_card(title: str, fig_html: str) -> str:
    return (
        '<div class="card"><h3>%s</h3><div class="plot-box">%s</div></div>'
        % (html_module.escape(title), fig_html)
    )


PLOTLY_CONFIG = {
    "displayModeBar": True,
    "responsive": True,
    "scrollZoom": True,
    "locale": "ru",
}
