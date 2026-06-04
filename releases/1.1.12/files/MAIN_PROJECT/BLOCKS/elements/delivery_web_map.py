# -*- coding: utf-8 -*-
"""Карта: Leaflet + OSM (без WebGL — совместимо с Qt WebEngine на Windows)."""
from __future__ import annotations

import json
from typing import List, Optional, Tuple

from PyQt5.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings

from calc.delivery_geo import RING


class _MapBridge(QObject):
    pointClicked = pyqtSignal(float, float)

    @pyqtSlot(float, float)
    def pickPoint(self, lat: float, lon: float):
        self.pointClicked.emit(float(lat), float(lon))


def _ring_to_geojson_polygon(ring: List[Tuple[float, float]]) -> dict:
    if len(ring) < 3:
        return {"type": "Feature", "properties": {}, "geometry": None}
    coords = [[float(lon), float(lat)] for lon, lat in ring]
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [coords]},
    }


def build_map_html(
    access_token: str,
    kad_feature: dict,
    initial_lat: Optional[float] = None,
    initial_lon: Optional[float] = None,
    route_coords: Optional[List[List[float]]] = None,
) -> str:
    # access_token оставлен в сигнатуре для совместимости вызовов; тайлы — OSM
    _ = access_token
    clat, clon = 59.94, 30.32
    zoom = 9
    if initial_lat is not None and initial_lon is not None:
        clat = float(initial_lat)
        clon = float(initial_lon)
        zoom = 12
    kad_json = json.dumps(kad_feature, ensure_ascii=False)
    route_json = json.dumps(route_coords or [], ensure_ascii=False)
    ilat_js = "null" if initial_lat is None else str(float(initial_lat))
    ilon_js = "null" if initial_lon is None else str(float(initial_lon))
    return """<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
html,body{margin:0;padding:0;height:100%;width:100%;overflow:hidden;font-family:Segoe UI,Arial,sans-serif;}
#map{height:100%;width:100%;background:#dfe8f2;}
</style>
</head>
<body>
<div id="map"></div>
<script>
let map = null;
let bridge = null;
let markerLayer = null;
let routeLayer = null;
const kadData = __KAD__;
const savedRoute = __ROUTE__;
const initLat = __ILAT__;
const initLon = __ILON__;

function setMarker(lat, lon) {
  if (!map) return;
  if (markerLayer) map.removeLayer(markerLayer);
  markerLayer = L.marker([lat, lon]).addTo(map);
}

function setRouteLine(coords) {
  if (!map || !coords || coords.length < 2) return;
  var latlngs = coords.map(function(c) { return [c[1], c[0]]; });
  if (routeLayer) map.removeLayer(routeLayer);
  routeLayer = L.polyline(latlngs, {color: '#1565c0', weight: 5, opacity: 0.9}).addTo(map);
  try {
    map.fitBounds(routeLayer.getBounds(), {padding: [48, 48], maxZoom: 13});
  } catch (e) {}
}

function initMap() {
  map = L.map('map', { zoomControl: true, attributionControl: false }).setView([__CLAT__, __CLON__], __ZOOM__);
  window.__map = map;
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: ''
  }).addTo(map);
  L.control.scale({imperial: false, metric: true}).addTo(map);
  if (kadData && kadData.geometry) {
    L.geoJSON(kadData, {
      style: { color: '#c62828', weight: 2, fillColor: '#e53935', fillOpacity: 0.2 }
    }).addTo(map);
  }
  if (initLat != null && initLon != null) setMarker(initLat, initLon);
  if (savedRoute && savedRoute.length > 1) setRouteLine(savedRoute);
  map.on('click', function(e) {
    var lat = e.latlng.lat, lon = e.latlng.lng;
    setMarker(lat, lon);
    if (bridge) bridge.pickPoint(lat, lon);
  });
  setTimeout(function() { if (map) map.invalidateSize(); }, 100);
  setTimeout(function() { if (map) map.invalidateSize(); }, 400);
}

document.addEventListener('DOMContentLoaded', function() {
  if (typeof qt !== 'undefined' && qt.webChannelTransport) {
    new QWebChannel(qt.webChannelTransport, function(channel) {
      bridge = channel.objects.pyBridge;
    });
  }
  initMap();
});
</script>
</body></html>""".replace("__KAD__", kad_json).replace("__ROUTE__", route_json).replace(
        "__ILAT__", ilat_js
    ).replace("__ILON__", ilon_js).replace("__CLAT__", str(clat)).replace(
        "__CLON__", str(clon)
    ).replace(
        "__ZOOM__", str(zoom)
    )


class DeliveryWebMap(QWebEngineView):
    pointClicked = pyqtSignal(float, float)

    def __init__(self, access_token: str, parent=None):
        super().__init__(parent)
        self._token = access_token
        self._bridge = _MapBridge(self)
        self._bridge.pointClicked.connect(self.pointClicked.emit)
        ch = QWebChannel(self.page())
        self.page().setWebChannel(ch)
        ch.registerObject("pyBridge", self._bridge)
        gs = QWebEngineSettings.globalSettings()
        for attr in (
            QWebEngineSettings.JavascriptEnabled,
            QWebEngineSettings.LocalContentCanAccessRemoteUrls,
            QWebEngineSettings.LocalContentCanAccessFileUrls,
            QWebEngineSettings.PluginsEnabled,
            QWebEngineSettings.Accelerated2dCanvasEnabled,
            QWebEngineSettings.WebGLEnabled,
        ):
            gs.setAttribute(attr, True)
        s = self.settings()
        for attr in (
            QWebEngineSettings.JavascriptEnabled,
            QWebEngineSettings.LocalContentCanAccessRemoteUrls,
            QWebEngineSettings.LocalContentCanAccessFileUrls,
            QWebEngineSettings.Accelerated2dCanvasEnabled,
            QWebEngineSettings.WebGLEnabled,
        ):
            s.setAttribute(attr, True)
        self.page().loadFinished.connect(self._on_load_finished)
        # OSM требует нормальный User-Agent, иначе тайлы могут не отдаваться
        self.page().profile().setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 MIRROR_CUT-Delivery/1.0"
        )
        self.setMinimumHeight(360)

    def _on_load_finished(self, ok: bool):
        if ok:
            self.page().runJavaScript(
                "setTimeout(function(){ if(window.__map){ window.__map.invalidateSize(); } }, 200);"
            )

    def reload_map(
        self,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        route_coords: Optional[List[List[float]]] = None,
    ):
        kad = _ring_to_geojson_polygon(list(RING))
        html = build_map_html(self._token, kad, lat, lon, route_coords)
        self.setHtml(html, QUrl("https://delivery.map.local/"))

    def js_set_marker(self, lat: float, lon: float):
        self.page().runJavaScript(
            "if(typeof setMarker==='function')setMarker(%s,%s);" % (lat, lon)
        )

    def js_set_route(self, coords: List[List[float]]):
        self.page().runJavaScript(
            "if(typeof setRouteLine==='function')setRouteLine(%s);" % json.dumps(coords)
        )
