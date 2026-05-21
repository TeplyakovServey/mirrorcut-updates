"""Generate QR code image for remnant URL. Размер и контраст подобраны для надёжного сканирования камерой."""
import os

import qrcode
import qrcode.constants
import io
import config


def make_qr_image(url, size_px=200):
    # error_correction=H for лучшего считывания при печати; box_size достаточный для длинного URL
    qr = qrcode.QRCode(version=None, box_size=8, border=2, error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img = img.resize((size_px, size_px))
    return img


def _qr_site_root():
    """
    Публичный корень приложения WEB_QR (URL, который открывается в браузере).

    Поддержка форматов QR_BASE_URL:
    - …/qr — уже корень Flask-приложения (часто WEB_SERVICE).
    - …/remnant — старый вид: отрезаем /remnant, получаем origin, дальше см. ниже.
    - пусто — http://185.43.5.8:5000 (публичный сервер; локально задайте QR_BASE_URL).

    Если задан WEB_QR_MOUNT_PATH (например /qr при монтировании в WEB_SERVICE) и база
    ещё не заканчивается на /qr — добавляем суффикс, чтобы /profile, /remnant, /facade
    попадали во Flask, а не в корень FastAPI.
    """
    base = str(getattr(config, "QR_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        base = "http://185.43.5.8:5000"
    low = base.lower()
    if low.endswith("/remnant"):
        base = base[:-len("/remnant")].rstrip("/")
        low = base.lower()
    if low.endswith("/qr"):
        return base
    mount = (os.environ.get("WEB_QR_MOUNT_PATH") or "").strip().rstrip("/")
    if mount and mount != "/" and not low.endswith("/qr"):
        base = (base + mount).rstrip("/")
    return base


def qr_web_app_public_base() -> str:
    """То же, что _qr_site_root: база для ссылок и QR в WEB_SERVICE / standalone web_qr."""
    return _qr_site_root()


def remnant_qr_url(unique_number):
    return "{}/remnant/{}".format(_qr_site_root(), unique_number)


def make_remnant_qr_image(unique_number, size_px=200):
    url = remnant_qr_url(unique_number)
    return make_qr_image(url, size_px)


def piece_k_scan_url(k_display) -> str:
    """Ссылка для QR на этикетке изделия (K…): главная WEB_QR с ?num=K5 — тот же сценарий, что ручной ввод."""
    k = str(k_display).strip().replace(" ", "")
    if not k:
        return _qr_site_root()
    ku = k.upper().replace("К", "K")
    if not ku.startswith("K"):
        ku = "K%s" % ku
    return "%s/?num=%s" % (_qr_site_root(), ku)


def make_piece_k_qr_image(k_display, size_px=200):
    return make_qr_image(piece_k_scan_url(k_display), size_px)


def profile_qr_url(unique_number):
    return "{}/profile/{}".format(_qr_site_root(), unique_number)


def facade_finished_qr_url(code):
    """Готовый фасад: сканирование ведёт на WEB_QR /facade/<код> (например GF5)."""
    c = str(code or "").strip().lstrip("/")
    return "{}/facade/{}".format(_qr_site_root(), c)


def make_profile_qr_image(unique_number, size_px=200):
    url = profile_qr_url(unique_number)
    return make_qr_image(url, size_px)


def make_facade_finished_qr_image(code, size_px=200):
    url = facade_finished_qr_url(code)
    return make_qr_image(url, size_px)
