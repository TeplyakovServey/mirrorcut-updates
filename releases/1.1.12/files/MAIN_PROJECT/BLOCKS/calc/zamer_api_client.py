# -*- coding: utf-8 -*-
"""HTTP-клиент к Django-порталу замеров.

Приоритет настроек:
1) переменные окружения MC_ZAMER_API_URL, MC_ZAMER_API_KEY, MC_ZAMER_API_TOKEN;
2) секция [zamer_api] в app.cfg (рядом с run.py): api_url, api_key, api_token.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse


def _cfg_zamer(option: str) -> str:
    try:
        from cfg_loader import app_cfg, get_cfg_string

        return get_cfg_string(app_cfg(), "zamer_api", option, "")
    except Exception:
        return ""


def _base_url() -> str:
    u = (os.environ.get("MC_ZAMER_API_URL", "") or "").strip().rstrip("/")
    if u:
        return u
    return _cfg_zamer("api_url").strip().rstrip("/")


def _headers_auth() -> Dict[str, str]:
    h: Dict[str, str] = {}
    tok = (os.environ.get("MC_ZAMER_API_TOKEN", "") or "").strip()
    if not tok:
        tok = _cfg_zamer("api_token").strip()
    if tok:
        h["Authorization"] = "Bearer %s" % tok
    key = (os.environ.get("MC_ZAMER_API_KEY", "") or "").strip()
    if not key:
        key = _cfg_zamer("api_key").strip()
    if key:
        h["X-Api-Key"] = key
    return h


def _headers_json() -> Dict[str, str]:
    h = _headers_auth()
    h["Content-Type"] = "application/json; charset=utf-8"
    return h


def api_enabled() -> bool:
    """True, если можно реально дернуть API (нужны URL и X-Api-Key или Bearer)."""
    return bool(_base_url() and _headers_auth())


def _normalize_hostname(host: str) -> str:
    h = (host or "").strip().lower()
    if h == "localhost":
        return "127.0.0.1"
    return h


def _portal_media_url_needs_api_auth(media_url: str, api_root: str) -> bool:
    """
    Для медиа под тем же порталом, что и api_url, нужны X-Api-Key / Bearer — иначе Django отдаёт HTML входа.
    Сравнение по startswith(api_root) ломается, если в конфиге localhost, а в file_url — 127.0.0.1 (и наоборот).
    """
    p = (media_url or "").strip()
    r = (api_root or "").strip().rstrip("/")
    if not p or not r:
        return False
    if p.startswith(r):
        return True
    try:
        pu, pr = urlparse(p), urlparse(r)
    except Exception:
        return False
    if (pu.scheme or "").lower() != (pr.scheme or "").lower():
        return False
    if not pu.hostname or not pr.hostname:
        return False
    if _normalize_hostname(pu.hostname) != _normalize_hostname(pr.hostname):
        return False
    p_port = pu.port or (443 if (pu.scheme or "").lower() == "https" else 80)
    r_port = pr.port or (443 if (pr.scheme or "").lower() == "https" else 80)
    if p_port != r_port:
        return False
    up = pu.path or "/"
    rp = (pr.path or "").rstrip("/")
    if not rp:
        return True
    return up.startswith(rp + "/") or up == rp


def _web_service_origin_from_api_root(api_root: str) -> str:
    """http://host:port/montazh → http://host:port (корень процесса WEB_SERVICE)."""
    r = (api_root or "").strip().rstrip("/")
    if r.lower().endswith("/montazh"):
        return r[: -len("/montazh")]
    return r


def _media_served_at_web_service_root() -> bool:
    """
    У FastAPI WEB_SERVICE файлы замера отдаются маршрутом GET /media/... на корне сервиса (см. app.py),
    а не под /montazh/media/... — последний путь часто отдаёт HTML-оболочку Django.

    MC_ZAMER_MEDIA_AT_PORTAL_ROOT=0 — склеивать /media/ с api_url как раньше (чистый Django под префиксом).
    """
    raw = (os.environ.get("MC_ZAMER_MEDIA_AT_PORTAL_ROOT") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    b = _base_url()
    return bool(b and b.rstrip("/").lower().endswith("/montazh"))


def _fix_montazh_media_absolute_url(url: str) -> str:
    """.../montazh/media/zamer/x.png → .../media/zamer/x.png (тот же хост)."""
    p = (url or "").strip()
    if "/montazh/media/" not in p:
        return p
    try:
        u = urlparse(p)
    except Exception:
        return p
    path = u.path or ""
    if "/montazh/media/" in path:
        path = path.replace("/montazh/media/", "/media/", 1)
        return urlunparse((u.scheme, u.netloc, path, u.params, u.query, u.fragment))
    return p


def resolve_zamer_file_url(file_url: str) -> str:
    """Полный URL для ссылок вида /media/... относительно MC_ZAMER_API_URL."""
    p = (file_url or "").strip()
    if not p:
        return ""
    if p.startswith("http://") or p.startswith("https://"):
        if _media_served_at_web_service_root():
            return _fix_montazh_media_absolute_url(p)
        return p
    b = _base_url()
    if not b:
        return p
    if p.startswith("/"):
        if p.startswith("/media/") and _media_served_at_web_service_root():
            return _web_service_origin_from_api_root(b) + p
        return b + p
    return "%s/%s" % (b.rstrip("/"), p.lstrip("/"))


def zamer_create(payload: Dict[str, Any], timeout: int = 25) -> Optional[Dict[str, Any]]:
    root = _base_url()
    if not root:
        return None
    url = root + "/api/zamery/"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in _headers_json().items():
        req.add_header(k, v)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if body.strip().startswith("{"):
                return json.loads(body)
    except Exception:
        return None
    return None


def _coerce_zamer_detail_dict(parsed: Any) -> Optional[Dict[str, Any]]:
    """Привести ответ GET /api/zamery/<id>/ к одному dict (обёртки прокси / нестандартный JSON)."""
    if isinstance(parsed, dict):
        if "id" in parsed:
            return parsed
        for k in ("data", "zamer", "result"):
            inner = parsed.get(k)
            if isinstance(inner, dict) and "id" in inner:
                return inner
        if parsed.get("ok") is True and isinstance(parsed.get("data"), dict) and "id" in parsed["data"]:
            return parsed["data"]
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict) and "id" in item:
                return item
        if len(parsed) == 1 and isinstance(parsed[0], dict) and "id" in parsed[0]:
            return parsed[0]
    return None


def zamer_get_with_error(zamer_id: int, timeout: int = 25) -> tuple[Optional[Dict[str, Any]], str]:
    """GET /api/zamery/<id>/ — (данные, пустая строка) или (None, текст ошибки для UI)."""
    root = _base_url()
    if not root:
        return None, "Не задан URL портала (app.cfg [zamer_api] api_url или MC_ZAMER_API_URL)."
    if not _headers_auth():
        return None, "Не задан ключ API (api_key / api_token или MC_ZAMER_*)."
    url = "%s/api/zamery/%s/" % (root, int(zamer_id))
    req = urllib.request.Request(url, method="GET")
    for k, v in _headers_auth().items():
        req.add_header(k, v)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            b = e.read().decode("utf-8", errors="replace")
        except Exception:
            b = ""
        b = (b or "").strip()
        if b.startswith("{"):
            try:
                d = json.loads(b)
                if e.code >= 400:
                    det = (d.get("detail") or d) if isinstance(d, dict) else str(d)
                    return None, "HTTP %s: %s" % (e.code, det)
            except json.JSONDecodeError:
                pass
        return None, "HTTP %s при запросе %s. Ответ: %s" % (
            e.code,
            url,
            (b[:300] + "…") if len(b) > 300 else (b or "(пусто)"),
        )
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return None, "Нет соединения с %s (%s). Портал запущен? Порт и api_url верные?" % (root, reason)
    except Exception as e:
        return None, str(e)
    body = (body or "").strip()
    if body.startswith("\ufeff"):
        body = body[1:].lstrip()
    if not body:
        return None, "Пустой ответ от %s" % url
    if not body.startswith("{") and not body.startswith("["):
        low = body[:800].lower()
        htmlish = "<!doctype" in low or "<html" in low
        hint = ""
        if htmlish:
            hint = (
                "\n\nСкорее всего вместо API открылась HTML-страница (вход или ошибка прокси). "
                "Проверьте: api_url = http://…:порт/montazh (если портал внутри WEB_SERVICE); "
                "перезапустите WEB_SERVICE; в app.cfg [zamer_api] тот же api_key, что подхватывает сервис "
                "(или задайте MC_ZAMER_API_KEY для процесса uvicorn)."
            )
        return None, "Ожидался JSON, получено (фрагмент): %s%s" % (body[:220], hint)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        return None, "Некорректный JSON: %s" % e
    row = _coerce_zamer_detail_dict(parsed)
    if row is None:
        return None, (
            "Ответ портала не похож на карточку заявки (ожидался JSON-объект с полем id). "
            "Получено: %s. Фрагмент: %s"
            % (type(parsed).__name__, str(parsed)[:180])
        )
    return row, ""


def zamer_get(zamer_id: int, timeout: int = 25) -> Optional[Dict[str, Any]]:
    data, _ = zamer_get_with_error(zamer_id, timeout=timeout)
    return data


def zamer_patch_json(
    zamer_id: int, payload: Dict[str, Any], timeout: int = 25
) -> tuple[Optional[Dict[str, Any]], str]:
    """PATCH /api/zamery/<id>/ — частичное обновление (флаги услуг, статус и т.д.)."""
    root = _base_url()
    if not root:
        return None, "Не задан URL портала."
    if not _headers_auth():
        return None, "Не задан ключ API."
    url = "%s/api/zamery/%s/" % (root, int(zamer_id))
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ctx = ssl.create_default_context()
    last_err = ""
    for use_tunnel in (False, True):
        if use_tunnel:
            req = urllib.request.Request(url, data=raw, method="POST")
            req.add_header("X-HTTP-Method-Override", "PATCH")
        else:
            req = urllib.request.Request(url, data=raw, method="PATCH")
        for k, v in _headers_json().items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                b = e.read().decode("utf-8", errors="replace")
            except Exception:
                b = ""
            last_err = "HTTP %s: %s" % (e.code, (b or "")[:400])
            if int(e.code) == 405 and not use_tunnel:
                continue
            return None, last_err
        except Exception as e:
            return None, str(e)
        if not (body or "").strip().startswith("{"):
            return None, "Ожидался JSON-ответ"
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            return None, str(e)
        row = _coerce_zamer_detail_dict(parsed)
        if row is None:
            return None, "Ответ PATCH не содержит карточку заявки"
        return row, ""
    return None, last_err or "HTTP 405"


def _zamer_delete_http_success(code: int) -> bool:
    """204/200 и редиректы после DELETE (Django часто отдаёт 302/303 вместо пустого 204)."""
    c = int(code or 0)
    if 200 <= c < 300:
        return True
    if c in (301, 302, 303, 307, 308):
        return True
    return False


def zamer_delete(zamer_id: int, timeout: int = 25) -> tuple[bool, str]:
    """DELETE /api/zamery/<id>/ — удалить заявку целиком.

    Некоторые прокси/Django отдают 405 на «голый» DELETE; пробуем варианты URL (со слэшем и без)
    и туннель POST + X-HTTP-Method-Override с пустым JSON-телом и JSON-заголовками (как у PATCH).
    """
    root = _base_url().rstrip("/")
    if not root:
        return False, "Не задан URL портала."
    if not _headers_auth():
        return False, "Не задан ключ API."
    zid = int(zamer_id)
    urls = (
        "%s/api/zamery/%s/" % (root, zid),
        "%s/api/zamery/%s" % (root, zid),
    )
    ctx = ssl.create_default_context()
    last_err = ""
    empty_json = b"{}"
    for url in urls:
        # 1) DELETE (явное тело — стабильнее для шлюзов, чем отсутствие Content-Length)
        try:
            req = urllib.request.Request(url, data=b"", method="DELETE")
            for k, v in _headers_auth().items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                if _zamer_delete_http_success(int(resp.getcode() or 0)):
                    return True, ""
                return False, "HTTP %s" % resp.getcode()
        except urllib.error.HTTPError as e:
            try:
                b = e.read().decode("utf-8", errors="replace")
            except Exception:
                b = ""
            code = int(e.code)
            if _zamer_delete_http_success(code):
                return True, ""
            last_err = "HTTP %s: %s" % (e.code, (b or "")[:400])
            if code != 405:
                return False, last_err
        except Exception as e:
            last_err = str(e)
            return False, last_err
        # 2) POST + override + JSON (часть стеков не принимает DELETE, но принимает «туннель»)
        try:
            req = urllib.request.Request(url, data=empty_json, method="POST")
            req.add_header("X-HTTP-Method-Override", "DELETE")
            for k, v in _headers_json().items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                if _zamer_delete_http_success(int(resp.getcode() or 0)):
                    return True, ""
                return False, "HTTP %s" % resp.getcode()
        except urllib.error.HTTPError as e:
            try:
                b = e.read().decode("utf-8", errors="replace")
            except Exception:
                b = ""
            code = int(e.code)
            if _zamer_delete_http_success(code):
                return True, ""
            last_err = "HTTP %s: %s" % (e.code, (b or "")[:400])
            if code != 405:
                return False, last_err
        except Exception as e:
            last_err = str(e)
            return False, last_err
    return False, last_err or "HTTP 405"


def zamer_upload_file(
    zamer_id: int,
    file_path: str,
    timeout: int = 60,
    *,
    file_kind: str = "measure",
    mark_complete: bool = False,
) -> Dict[str, Any]:
    root = _base_url()
    if not root:
        return {"ok": False, "error": "MC_ZAMER_API_URL not set"}
    url = "%s/api/zamery/%s/files/" % (root, int(zamer_id))
    boundary = "----ZamerBoundary7MA4YWxkTrZu0gW"
    import os as _os

    fn = _os.path.basename(file_path)
    with open(file_path, "rb") as f:
        raw = f.read()
    crlf = b"\r\n"
    fk = (file_kind or "measure").strip().lower()
    if fk not in ("measure", "delivery", "install"):
        fk = "measure"
    mc = "1" if mark_complete else "0"

    def _field(name: str, value: str) -> list:
        return [
            ("--" + boundary).encode("ascii"),
            ('Content-Disposition: form-data; name="%s"' % name).encode("utf-8"),
            b"",
            value.encode("utf-8"),
        ]

    parts: list = []
    for name, val in (
        ("file_kind", fk),
        ("mark_complete", mc),
        ("by", "desktop"),
    ):
        parts.extend(_field(name, val))
    parts.extend(
        [
            ("--" + boundary).encode("ascii"),
            ('Content-Disposition: form-data; name="file"; filename="%s"' % fn.replace('"', "")).encode(
                "utf-8"
            ),
            b"Content-Type: application/octet-stream",
            b"",
            raw,
            ("--" + boundary + "--").encode("ascii"),
        ]
    )
    payload = crlf.join(parts)
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
    for k, v in _headers_auth().items():
        req.add_header(k, v)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            if data.strip().startswith("{"):
                return json.loads(data)
            return {"ok": True, "raw": data}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e.code)
        return {"ok": False, "error": body[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def zamer_list_local_files_from_api(zamer_id: int) -> List[Dict[str, Any]]:
    d = zamer_get(zamer_id)
    if not d:
        return []
    files = d.get("files") or d.get("Файлы") or []
    if isinstance(files, list):
        return [x for x in files if isinstance(x, dict)]
    return []


def portal_fetch_url_bytes(url: str, timeout: int = 25) -> bytes:
    """Скачивание файла с портала: SSL как у API; для URL под тем же api_url — заголовки авторизации."""
    p = (url or "").strip()
    if not p:
        return b""
    req = urllib.request.Request(p, method="GET")
    req.add_header("User-Agent", "MirrorCutDesktop/1.0 (zamer-media)")
    root = _base_url()
    if root and _portal_media_url_needs_api_auth(p, root):
        for k, v in _headers_auth().items():
            req.add_header(k, v)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()
