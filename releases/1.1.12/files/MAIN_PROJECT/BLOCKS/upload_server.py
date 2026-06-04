# -*- coding: utf-8 -*-
"""
Мини-сервер загрузки макетов (как uploaded_figures в Streamlit).
Запуск: python upload_server.py
Переменные: MC_UPLOAD_DIR — каталог сохранения, MC_UPLOAD_PORT — порт (по умолчанию 5050).
Отдача файлов: GET /files/<name>
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

_UPLOAD_ROOT = os.environ.get("MC_UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploaded_figures"))
os.makedirs(_UPLOAD_ROOT, exist_ok=True)

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "empty filename"}), 400
    raw = f.read()
    if len(raw) > 2 * 1024 * 1024:
        return jsonify({"ok": False, "error": "file too large (max 2MB)"}), 400
    ext = os.path.splitext(f.filename)[1].lower() or ".bin"
    if ext not in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"):
        return jsonify({"ok": False, "error": "unsupported type"}), 400
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    safe_base = _SAFE_NAME.sub("_", os.path.splitext(f.filename)[0])[:80]
    stored = "%s_%s_%s%s" % (stamp, uuid.uuid4().hex[:8], safe_base, ext)
    path = os.path.join(_UPLOAD_ROOT, stored)
    with open(path, "wb") as out:
        out.write(raw)
    base = request.host_url.rstrip("/")
    url = "%s/files/%s" % (base, stored)
    return jsonify({"ok": True, "stored_name": stored, "url": url, "path": path})


@app.route("/files/<path:name>")
def serve_file(name):
    return send_from_directory(_UPLOAD_ROOT, name, as_attachment=False)


if __name__ == "__main__":
    port = int(os.environ.get("MC_UPLOAD_PORT", "5050"))
    app.run(host="0.0.0.0", port=port, threaded=True)
