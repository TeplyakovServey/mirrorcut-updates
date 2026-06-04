# -*- coding: utf-8 -*-
"""Загрузка макета на сервер (multipart POST)."""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_UPLOAD_URL = os.environ.get("MC_UPLOAD_URL", "http://185.43.5.8:5050/upload").strip()


def upload_sketch_file(
    file_path: str,
    base_url: Optional[str] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Отправляет файл на сервер. Ожидается JSON: {"ok": true, "url": "...", "stored_name": "..."}.
    """
    root = (base_url or DEFAULT_UPLOAD_URL).rstrip("/")
    if root.endswith("/upload"):
        url = root
    else:
        url = root + "/upload"
    boundary = "----PyQtBoundary7MA4YWxkTrZu0gW"
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        raw = f.read()
    crlf = b"\r\n"
    parts = [
        ("--" + boundary).encode("ascii"),
        ('Content-Disposition: form-data; name="file"; filename="%s"' % filename.replace('"', "")).encode("utf-8"),
        b"Content-Type: application/octet-stream",
        b"",
        raw,
        ("--" + boundary + "--").encode("ascii"),
    ]
    payload = crlf.join(parts)
    ctx = ssl.create_default_context()
    last_http: Optional[urllib.error.HTTPError] = None
    for attempt in range(4):
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                data = resp.read().decode("utf-8", errors="replace")
                if data.strip().startswith("{"):
                    return json.loads(data)
                return {"ok": True, "raw": data}
        except urllib.error.HTTPError as e:
            last_http = e
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            code = getattr(e, "code", None)
            if code in (429, 502, 503) and attempt < 3:
                time.sleep(0.4 * (attempt + 1))
                continue
            err_txt = (body or str(code or e)).strip()
            if len(err_txt) > 200:
                err_txt = err_txt[:200] + "…"
            return {"ok": False, "error": err_txt or str(e)}
        except Exception as e:
            if attempt < 3:
                time.sleep(0.3 * (attempt + 1))
                continue
            return {"ok": False, "error": str(e)}
    if last_http is not None:
        return {"ok": False, "error": str(getattr(last_http, "code", last_http))}
    return {"ok": False, "error": "upload failed"}
