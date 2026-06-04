# -*- coding: utf-8 -*-
"""Добавление изделия в существующий заказ (стекло, фасады, …) — один вход для главного окна и обзора заказа."""


def run_product_creation_flow(parent, order_id: int, type_key: str) -> None:
    """
    order_id — mirror_orders.id.
    type_key — значение из NewOrderModal (glass, facades, doors, …).
    """
    oid = int(order_id)
    key = (type_key or "").strip()

    if key == "glass":
        from ui.glass_mirror_calc_dialog import GlassMirrorCalcDialog

        GlassMirrorCalcDialog(parent, oid, append_new=True).exec_()
        return

    if key == "facades":
        from ui.facade_order_dialog import FacadeOrderDialog

        FacadeOrderDialog(parent, linked_order_id=oid, append_new=True).exec_()
        return

    # Остальные типы — без диалога (сообщение показывает вызывающий код при необходимости)
