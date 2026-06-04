# -*- coding: utf-8 -*-
"""Полигон КАД (как в Streamlit) без импорта cords.py."""
from __future__ import annotations

import json
import math
import os
from typing import List, Optional, Tuple

_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_DIR, "..", "..", "..", ".."))


def _kad_json_candidates() -> List[str]:
    return [
        os.path.join(_DIR, "..", "data", "kad_ring.json"),
        os.path.join(_REPO_ROOT, "FINAL_WINDOW", "MAIN_PROJECT", "BLOCKS", "data", "kad_ring.json"),
        os.path.join(_REPO_ROOT, "BLOCKS", "data", "kad_ring.json"),
        os.path.join(_REPO_ROOT, "data", "kad_ring.json"),
    ]


def _load_ring() -> List[Tuple[float, float]]:
    for path in _kad_json_candidates():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            out = []
            for p in raw:
                if len(p) >= 2:
                    out.append((float(p[0]), float(p[1])))
            if out:
                return out
        except Exception:
            continue
    return []


RING = _load_ring()


def point_in_polygon(lon: float, lat: float, ring: List[Tuple[float, float]]) -> bool:
    """Ray casting; ring: (lon, lat) как в Shapely."""
    n = len(ring)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def kad_contains(lon: float, lat: float) -> bool:
    if not RING:
        return False
    return point_in_polygon(lon, lat, RING)


def kad_centroid(ring: List[Tuple[float, float]]) -> Tuple[float, float]:
    if not ring:
        return 30.3, 59.95
    sx = sy = 0.0
    for lon, lat in ring:
        sx += lon
        sy += lat
    n = len(ring)
    return sx / n, sy / n


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def route_length_m(coords: List[List[float]]) -> float:
    d = 0.0
    for i in range(1, len(coords)):
        lon1, lat1 = coords[i - 1]
        lon2, lat2 = coords[i]
        d += haversine_m(lat1, lon1, lat2, lon2)
    return d


def _segment_boundary_hit(
    lon1: float, lat1: float, lon2: float, lat2: float, ring: List[Tuple[float, float]]
) -> Optional[Tuple[float, float]]:
    """Первое пересечение отрезка с ребром полигона (упрощённо)."""
    best = None
    best_t = 2.0
    n = len(ring)
    for k in range(n):
        x3, y3 = ring[k]
        x4, y4 = ring[(k + 1) % n]
        den = (lon1 - lon2) * (y3 - y4) - (lat1 - lat2) * (x3 - x4)
        if abs(den) < 1e-18:
            continue
        t = ((lon1 - x3) * (y3 - y4) - (lat1 - y3) * (x3 - x4)) / den
        u = -((lon1 - lon2) * (lat1 - y3) - (lat1 - lat2) * (lon1 - x3)) / den
        if 0 <= t <= 1 and 0 <= u <= 1 and t < best_t:
            best_t = t
            best = (lon1 + t * (lon2 - lon1), lat1 + t * (lat2 - lat1))
    return best


def optimize_route_to_kad(
    start_lat: float, start_lon: float, mapbox_token: str, ring: Optional[List[Tuple[float, float]]] = None
) -> Optional[Tuple[float, List[List[float]]]]:
    """
    Как get_optimized_route в x.py: маршрут от точки до центроида КАД, обрезка у границы.
    Возвращает (distance_m, coords_lonlat) или None.
    """
    ring = ring or RING
    if not mapbox_token:
        return None
    if not ring:
        # Нет kad_ring.json — приближённый маршрут до центра СПб (чтобы км и тариф считались).
        cx, cy = 30.315868, 59.939099
        coords = [[start_lon, start_lat], [cx, cy]]
        return route_length_m(coords), coords
    try:
        import requests
    except ImportError:
        return None
    cx, cy = kad_centroid(ring)
    url = (
        "https://api.mapbox.com/directions/v5/mapbox/driving/"
        f"{start_lon},{start_lat};{cx},{cy}"
    )
    r = requests.get(
        url,
        params={"access_token": mapbox_token, "geometries": "geojson", "overview": "full"},
        timeout=(5, 12),
    )
    if r.status_code != 200:
        return None
    data = r.json()
    routes = data.get("routes") or []
    if not routes:
        return None
    full_coords = routes[0]["geometry"]["coordinates"]
    hit = None
    best_i = len(full_coords)
    try:
        import importlib  # динамический импорт — меньше лишних зависимостей при PyInstaller

        geom = importlib.import_module("shapely.geometry")
        LineString, Point, Polygon = geom.LineString, geom.Point, geom.Polygon

        poly = Polygon(ring).buffer(0.002)
        for i in range(1, len(full_coords)):
            seg = LineString([full_coords[i - 1], full_coords[i]])
            if not seg.intersects(poly.boundary):
                continue
            inter = seg.intersection(poly.boundary)
            if inter.is_empty:
                continue
            if inter.geom_type == "Point":
                p = inter
            elif inter.geom_type == "MultiPoint":
                p = min(inter.geoms, key=lambda q: seg.project(q))
            elif inter.geom_type == "LineString":
                p = Point(inter.coords[0][0], inter.coords[0][1])
            else:
                continue
            hit = (p.x, p.y)
            best_i = i
            break
    except Exception:
        for i in range(1, len(full_coords)):
            a = full_coords[i - 1]
            b = full_coords[i]
            h = _segment_boundary_hit(a[0], a[1], b[0], b[1], ring)
            if h is not None:
                hit = h
                best_i = i
                break
    if hit is None:
        return None
    optimized = full_coords[:best_i]
    optimized.append([hit[0], hit[1]])
    dist = route_length_m(optimized)
    return dist, optimized


def geocode_mapbox(query: str, token: str) -> Optional[Tuple[float, float, str]]:
    """(lat, lon, place_name) или None."""
    if not query.strip() or not token:
        return None
    try:
        import urllib.parse
        import requests

        q = urllib.parse.quote(query.strip())
        url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{q}.json"
        r = requests.get(
            url,
            params={
                "access_token": token,
                "types": "address",
                "limit": 1,
                "bbox": "27.8,58.5,35.5,61.3",
            },
            timeout=(5, 12),
        )
        if r.status_code != 200:
            return None
        feats = (r.json() or {}).get("features") or []
        if not feats:
            return None
        lon, lat = feats[0]["center"]
        name = feats[0].get("place_name") or query
        return float(lat), float(lon), name
    except Exception:
        return None


def geocode_by_token(query: str, token: str) -> Optional[Tuple[float, float, str]]:
    return geocode_mapbox(query, token)
