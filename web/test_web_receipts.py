"""Focused tests for the receipts Web read surface (RECEIPT-2).

Usage: python3 web/test_web_receipts.py
"""

import importlib.util
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.testclient import TestClient

WEB_DIR = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("meal_web_main", WEB_DIR / "main.py")
if spec is None or spec.loader is None:
    raise RuntimeError("could not load web/main.py")
web: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)

_passed = 0
_failed = 0


def check(label: str, condition, detail="") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}  -- {detail}")


def set_data_root(root: Path) -> None:
    web.DATA_DIR = root
    web.DISHES_PATH = root / "dishes.json"
    web.HISTORY_PATH = root / "history.json"
    web.RECEIPTS_PATH = root / "receipts.json"
    _receipt_repo.path = root / "receipts.json"


def seed_receipt(
    repository,
    receipt_mod,
    commands_mod,
    *,
    merchant: str,
    merchant_normalized: str,
    purchased_at: str,
    lines,
    total_cents,
    evidence_hash: str,
    status: str = "confirmed",
) -> str:
    result = commands_mod.record_receipt(
        {
            "merchant_name_raw": merchant,
            "merchant_name_normalized": merchant_normalized,
            "purchased_at": purchased_at,
            "time_precision": "date" if "T" not in purchased_at else "datetime",
            "currency": "EUR",
            "lines": lines,
            "total_cents": total_cents,
            "evidence": {
                "source_kind": "manual",
                "content_sha256": evidence_hash,
            },
        },
        status=status,
        repository=repository,
        manager=web._audit_transaction_manager(),
    )
    return result["receipt_id"]


def main() -> None:
    receipt_mod = importlib.import_module(
        f"{web.PLUGIN_ROOT.name}.src.receipt"
    )
    commands_mod = importlib.import_module(
        f"{web.PLUGIN_ROOT.name}.src.receipt_commands"
    )
    global _receipt_repo
    _receipt_repo = web._receipt_repository()

    client = TestClient(web.app)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        set_data_root(root)

        lines_a = [
            {"description_raw": "ELVEA BASILIC 690G", "kind": "product",
             "quantity": "1", "unit": "pack", "line_total_cents": 405},
            {"description_raw": "GRILLWURSCHT X10", "kind": "product",
             "quantity": "1.016", "unit": "kg", "line_total_cents": 1015},
        ]
        lines_b = [
            {"description_raw": "BIO MILCH 1L", "kind": "product",
             "quantity": "1", "unit": "pack", "line_total_cents": 129},
        ]

        id_a = seed_receipt(
            _receipt_repo, receipt_mod, commands_mod,
            merchant="Auchan Kirchberg",
            merchant_normalized="auchan kirchberg",
            purchased_at="2026-08-13T19:48:04+02:00",
            lines=lines_a, total_cents=1420,
            evidence_hash="a" * 64,
        )
        id_b = seed_receipt(
            _receipt_repo, receipt_mod, commands_mod,
            merchant="Les Traditions",
            merchant_normalized="les traditions",
            purchased_at="2026-09-05",
            lines=lines_b, total_cents=129,
            evidence_hash="b" * 64,
            status="needs_review",
        )
        id_c = seed_receipt(
            _receipt_repo, receipt_mod, commands_mod,
            merchant="Auchan Kirchberg",
            merchant_normalized="auchan kirchberg",
            purchased_at="2026-09-06",
            lines=lines_b, total_cents=129,
            evidence_hash="c" * 64,
        )

        # ─── list endpoint ───
        print("\n-- GET /api/receipts --")
        response = client.get("/api/receipts")
        check("list returns 200", response.status_code == 200, response.text)
        payload = response.json()
        ids = [item["receipt_id"] for item in payload["receipts"]]
        check(
            "retracted-free default excludes needs_review? no — shows all non-retracted",
            set(ids) == {id_a, id_b, id_c},
            str(ids),
        )
        check(
            "list newest first",
            ids[0] == id_c,
            str([(item["receipt_id"], item.get("purchased_on")) for item in payload["receipts"]]),
        )
        check(
            "summaries carry canonical fields",
            all(
                {"receipt_id", "status", "merchant_name_raw", "total_cents",
                 "line_count", "purchased_on"} <= set(item)
                for item in payload["receipts"]
            ),
            str(payload["receipts"][0]),
        )

        response = client.get("/api/receipts", params={"status": "needs_review"})
        ids = [item["receipt_id"] for item in response.json()["receipts"]]
        check("status filter", ids == [id_b], str(ids))

        response = client.get(
            "/api/receipts", params={"merchant_name": "auchan kirchberg"}
        )
        ids = [item["receipt_id"] for item in response.json()["receipts"]]
        check("merchant filter", set(ids) == {id_a, id_c}, str(ids))

        response = client.get(
            "/api/receipts",
            params={"from_date": "2026-09-01", "to_date": "2026-09-30"},
        )
        ids = [item["receipt_id"] for item in response.json()["receipts"]]
        check("period filter", set(ids) == {id_b, id_c}, str(ids))

        response = client.get("/api/receipts", params={"limit": 1})
        check("limit respected", len(response.json()["receipts"]) == 1)

        response = client.get("/api/receipts", params={"limit": 0})
        check("limit=0 rejected", response.status_code in (400, 422), str(response.status_code))

        response = client.get("/api/receipts", params={"status": "bogus"})
        check("unknown status rejected", response.status_code == 400, str(response.status_code))

        response = client.get("/api/receipts", params={"from_date": "not-a-date"})
        check("bad date rejected", response.status_code == 400, str(response.status_code))

        # ─── detail endpoint ───
        print("\n-- GET /api/receipts/{id} --")
        response = client.get(f"/api/receipts/{id_a}")
        check("detail returns 200", response.status_code == 200, response.text)
        detail = response.json()
        check("detail is canonical receipt", detail["receipt_id"] == id_a)
        check("detail exposes lines", len(detail["lines"]) == 2)
        check(
            "detail hides revisions by default",
            "revisions" not in detail,
            str(sorted(detail)),
        )

        response = client.get(
            f"/api/receipts/{id_a}", params={"include_revisions": "true"}
        )
        detail = response.json()
        check(
            "include_revisions appends history",
            len(detail.get("revisions", [])) == 1,
            str(sorted(detail)),
        )

        response = client.get("/api/receipts/receipt_" + "0" * 32)
        check("missing receipt is 404", response.status_code == 404, str(response.status_code))

        response = client.get("/api/receipts/../../etc/passwd")
        check("path traversal rejected", response.status_code in (400, 404), str(response.status_code))

        # ─── analytics endpoint ───
        print("\n-- GET /api/purchase-analytics --")
        response = client.get("/api/purchase-analytics")
        check("analytics returns 200", response.status_code == 200, response.text)
        analytics = response.json()
        check(
            "needs_review excluded from default analytics",
            analytics["receipt_count"] == 2,
            json.dumps(analytics)[:300],
        )
        check(
            "needs_review counted in excluded_by_status",
            analytics["excluded_by_status"].get("needs_review") == 1,
            str(analytics.get("excluded_by_status")),
        )

        response = client.get(
            "/api/purchase-analytics", params={"include_needs_review": "true"}
        )
        check(
            "include_needs_review adds the third receipt",
            response.json()["receipt_count"] == 3,
            response.text[:200],
        )

        response = client.get(
            "/api/purchase-analytics", params={"currency": "eur"}
        )
        check(
            "currency filter normalizes case",
            response.json()["receipt_count"] == 2,
            response.text[:200],
        )

        response = client.get(
            "/api/purchase-analytics",
            params={"from_date": "2026-09-01", "to_date": "2026-09-30"},
        )
        check(
            "period filter for analytics",
            response.json()["receipt_count"] == 1,
            response.text[:200],
        )

        response = client.get(
            "/api/purchase-analytics", params={"merchant_name": "   "}
        )
        check("blank merchant rejected", response.status_code == 400, str(response.status_code))

        # ─── page + storage failure semantics ───
        print("\n-- page & failure semantics --")
        response = client.get("/#/receipts")
        check("SPA serves shell for receipts view", response.status_code == 200)

        receipts_payload = client.get("/api/receipts").json()["receipts"]
        serialized = json.dumps(receipts_payload, ensure_ascii=False)
        check(
            "summaries never leak privacy-gated text unescaped (raw JSON is data)",
            "<script>" not in serialized,
            serialized[:200],
        )

        def broken_read_target(raw_path):
            raise web.AuditConflictError("simulated conflict")

        original_read_target = type(_receipt_repo and web._audit_transaction_manager()).read_target
        manager = web._audit_transaction_manager()
        manager.read_target = broken_read_target
        try:
            response = client.get("/api/receipts")
            check(
                "storage conflict is sanitized 503",
                response.status_code == 503 and "temporarily unavailable" in response.text,
                f"{response.status_code} {response.text[:200]}",
            )
        finally:
            manager.read_target = original_read_target

    print(f"\nweb receipts tests: {'PASS' if _failed == 0 else 'FAIL'}"
          f" ({_passed} passed, {_failed} failed)")
    raise SystemExit(1 if _failed else 0)


if __name__ == "__main__":
    main()
