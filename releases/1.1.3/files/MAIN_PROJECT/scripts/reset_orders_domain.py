# -*- coding: utf-8 -*-
"""Full reset for orders/cutting/labels domain with dry-run."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from db.connection import get_connection

TABLES = [
    "mirror_cut_archive_detail",
    "mirror_cut_archive",
    "mirror_cut_results",
    "mirror_remnant_history",
    "mirror_deleted_remnants",
    "mirror_remnants",
    "mirror_order_items",
    "mirror_orders",
    "mirror_label_counter",
    "mirror_k_counter",
]


def _count(cur, table):
    cur.execute("SELECT COUNT(*) AS c FROM %s" % table)
    return int((cur.fetchone() or {}).get("c") or 0)


def run_reset(dry_run=True):
    report = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            for t in TABLES:
                try:
                    report[t] = _count(cur, t)
                except Exception:
                    report[t] = None
            if dry_run:
                return report
            for t in TABLES:
                cur.execute("TRUNCATE TABLE %s RESTART IDENTITY CASCADE" % t)
            cur.execute("INSERT INTO mirror_label_counter (value) VALUES (0)")
            cur.execute("INSERT INTO mirror_k_counter (value) VALUES (0)")
    return report


def main():
    print("Dry-run reset report:")
    rep = run_reset(dry_run=True)
    for k in TABLES:
        print("  %s: %s" % (k, rep.get(k)))
    ans = input("Proceed full reset? (yes/no): ").strip().lower()
    if ans not in ("yes", "y"):
        print("Cancelled.")
        return
    run_reset(dry_run=False)
    print("Full reset completed.")


if __name__ == "__main__":
    main()
