"""Focused smoke tests for the read-only weekly-plan web API.

Usage: python3 web/test_web_plans.py
"""

import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException

WEB_DIR = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("meal_web_main", WEB_DIR / "main.py")
if spec is None or spec.loader is None:
    raise RuntimeError("could not load web/main.py")
web: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


def write_plan(directory: Path, week: str, payload) -> None:
    (directory / f"{week}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def valid_plan(week="2026-W30"):
    days: dict[str, dict] = {
        day: {"meals": []}
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    }
    days["mon"] = {
        "meals": [{"dish": "soup <script>", "portions": 4}],
        "note": "safe view <b>",
    }
    return {
        "week": week,
        "status": "draft",
        "prep": ["stock"],
        "days": days,
        "leftovers": {},
        "shopping": {
            "basis": "cooking_occurrences",
            "items": [{
                "ingredient": "carrot <b>",
                "required_uses": 2,
                "available_uses": 0,
                "to_buy": 2,
                "required_by": [{"kind": "dish", "name": "soup", "uses": 2}],
                "estimated_unit_price": 6.25,
                "estimated_cost": 12.5,
            }],
            "covered_by_fridge": [],
            "prep_to_make": [],
            "unresolved_prep_dependencies": [],
            "prep_capacity_warnings": [],
            "estimated_cost": 12.5,
            "weekly_limit": 150.0,
            "complete": True,
            "priced_items": 1,
            "total_items": 1,
            "unpriced_items": [],
            "weekly_budget_status": "within",
            "warning": None,
        },
    }


def main():
    assert web._valid_iso_week("2026-W01")
    assert web._valid_iso_week("2020-W53")
    assert not web._valid_iso_week("2026-W00")
    assert not web._valid_iso_week("2026-W54")
    assert not web._valid_iso_week("2026-W99")
    assert not web._valid_iso_week("../../etc/passwd")

    with tempfile.TemporaryDirectory() as tmp:
        web.PLANS_DIR = Path(tmp)
        write_plan(web.PLANS_DIR, "2026-W30", valid_plan())
        rows = web.list_week_plans()
        assert rows == [{
            "week": "2026-W30",
            "status": "draft",
            "meals_count": 1,
            "prep_count": 1,
        }]
        assert web.load_week_plan("2026-W30")["days"]["mon"]["meals"][0]["portions"] == 4

        unknown_day = valid_plan("2026-W31")
        unknown_day["days"]["holiday"] = {"meals": [{"dish": "hidden", "portions": 1}]}
        blank_dish = valid_plan("2026-W31")
        blank_dish["days"]["mon"]["meals"][0]["dish"] = "   "
        zero_portions = valid_plan("2026-W31")
        zero_portions["days"]["mon"]["meals"][0]["portions"] = 0
        empty_days = valid_plan("2026-W31")
        empty_days["days"] = {}
        null_meals = valid_plan("2026-W31")
        null_meals["days"]["mon"]["meals"] = None
        status_list = valid_plan("2026-W31")
        status_list["status"] = []
        status_dict = valid_plan("2026-W31")
        status_dict["status"] = {}
        shopping_list = valid_plan("2026-W31")
        shopping_list["shopping"] = []
        shopping_items_null = valid_plan("2026-W31")
        shopping_items_null["shopping"]["items"] = None
        shopping_nan = valid_plan("2026-W31")
        shopping_nan["shopping"]["estimated_cost"] = float("nan")
        shopping_bad_prep = valid_plan("2026-W31")
        shopping_bad_prep["shopping"]["prep_to_make"] = {}
        shopping_bad_trip_items = valid_plan("2026-W31")
        shopping_bad_trip_items["shopping"]["trips"] = [{"trip": 1, "items": None, "estimated_cost": 1.0, "limit": 100.0, "over_limit": False}]
        shopping_infinite_limit = valid_plan("2026-W31")
        shopping_infinite_limit["shopping"]["weekly_limit"] = float("inf")
        shopping_huge_integer = valid_plan("2026-W31")
        shopping_huge_integer["shopping"]["estimated_cost"] = 10 ** 1000
        shopping_contradictory_complete = valid_plan("2026-W31")
        shopping_contradictory_complete["shopping"]["weekly_budget_status"] = "unknown"
        shopping_contradictory_partial = valid_plan("2026-W31")
        shopping_contradictory_partial["shopping"]["complete"] = False
        shopping_complete_with_unpriced_item = valid_plan("2026-W31")
        shopping_complete_with_unpriced_item["shopping"]["items"][0].pop("estimated_unit_price")
        shopping_complete_with_unpriced_item["shopping"]["items"][0].pop("estimated_cost")
        shopping_empty_prep_name = valid_plan("2026-W31")
        shopping_empty_prep_name["shopping"]["prep_to_make"] = [{
            "prep_item": "", "required_uses": 1, "available_uses": 0,
            "projected_uses": 2, "planned_explicitly": True,
        }]
        shopping_bad_unresolved = valid_plan("2026-W31")
        shopping_bad_unresolved["shopping"]["unresolved_prep_dependencies"] = [{}]
        shopping_bad_counts = valid_plan("2026-W31")
        shopping_bad_counts["shopping"]["priced_items"] = 0

        def with_trip():
            payload = valid_plan("2026-W31")
            item = dict(payload["shopping"]["items"][0])
            payload["shopping"].update({
                "trips": [{
                    "trip": 1, "items": [item], "estimated_cost": 12.5,
                    "limit": 100.0, "over_limit": False,
                }],
                "trip_limit": 100.0,
                "unpriced_trip_items": [],
                "trip_warnings": [],
            })
            return payload

        shopping_trip_no_number = with_trip()
        shopping_trip_no_number["shopping"]["trips"][0].pop("trip")
        shopping_trip_wrong_total = with_trip()
        shopping_trip_wrong_total["shopping"]["trips"][0]["estimated_cost"] = 1.0
        shopping_trip_wrong_limit = with_trip()
        shopping_trip_wrong_limit["shopping"]["trips"][0]["limit"] = 99.0
        shopping_extra_empty_trip = with_trip()
        shopping_extra_empty_trip["shopping"]["trips"].append({
            "trip": 2, "items": [], "estimated_cost": 0.0,
            "limit": 100.0, "over_limit": False,
        })
        shopping_all_empty_trip = valid_plan("2026-W31")
        partial_shopping = shopping_all_empty_trip["shopping"]
        partial_shopping["items"][0].pop("estimated_unit_price")
        partial_shopping["items"][0].pop("estimated_cost")
        partial_shopping.update({
            "estimated_cost": 0.0, "complete": False,
            "priced_items": 0, "total_items": 1,
            "unpriced_items": ["carrot <b>"],
            "weekly_budget_status": "unknown",
            "warning": "Cost estimate is incomplete; weekly budget status is unknown.",
            "trips": [{
                "trip": 1, "items": [], "estimated_cost": 0.0,
                "limit": 100.0, "over_limit": False,
            }],
            "trip_limit": 100.0,
            "unpriced_trip_items": ["carrot <b>"],
            "trip_warnings": ["Unpriced items are not assigned to cost-limited trips."],
        })

        shopping_pricing_without_estimate = valid_plan("2026-W31")
        for key in (
            "estimated_cost", "complete", "priced_items", "total_items",
            "unpriced_items", "weekly_limit", "weekly_budget_status", "warning",
        ):
            shopping_pricing_without_estimate["shopping"].pop(key)
        capacity_detail = {
            "prep_item": "stock", "required_uses": 2, "available_uses": 0,
            "projected_uses": 1, "planned_explicitly": True,
        }
        shopping_missing_capacity = valid_plan("2026-W31")
        shopping_missing_capacity["shopping"]["prep_to_make"] = [capacity_detail]
        shopping_orphan_capacity = valid_plan("2026-W31")
        shopping_orphan_capacity["shopping"]["prep_capacity_warnings"] = [capacity_detail]
        shopping_priced_covered = valid_plan("2026-W31")
        shopping_priced_covered["shopping"]["covered_by_fridge"] = [{
            "ingredient": "water", "required_uses": 1, "available_uses": 1,
            "to_buy": 0,
            "required_by": [{"kind": "dish", "name": "soup", "uses": 1}],
            "estimated_unit_price": 1.0, "estimated_cost": 0.0,
        }]
        shopping_noncanonical_prep = valid_plan("2026-W31")
        shopping_noncanonical_prep["shopping"]["prep_to_make"] = [{
            "prep_item": " Stock ", "required_uses": 0, "available_uses": 0,
            "projected_uses": 1, "planned_explicitly": True,
        }]
        shopping_unresolved_wrong_schema = valid_plan("2026-W31")
        shopping_unresolved_wrong_schema["shopping"]["unresolved_prep_dependencies"] = [{
            "prep_item": "stock", "required_uses": 1,
            "reason": "other", "unexpected": True,
        }]

        write_plan(web.PLANS_DIR, "2026-W31", with_trip())
        assert web.load_week_plan("2026-W31")["shopping"]["trips"][0]["trip"] == 1

        malformed = [
            None,
            [],
            {"week": "2026-W31", "days": None},
            {"week": "2026-W31", "prep": None, "days": {}},
            empty_days,
            unknown_day,
            blank_dish,
            zero_portions,
            null_meals,
            status_list,
            status_dict,
            shopping_list,
            shopping_items_null,
            shopping_nan,
            shopping_bad_prep,
            shopping_bad_trip_items,
            shopping_infinite_limit,
            shopping_huge_integer,
            shopping_contradictory_complete,
            shopping_contradictory_partial,
            shopping_complete_with_unpriced_item,
            shopping_empty_prep_name,
            shopping_bad_unresolved,
            shopping_bad_counts,
            shopping_trip_no_number,
            shopping_trip_wrong_total,
            shopping_trip_wrong_limit,
            shopping_extra_empty_trip,
            shopping_all_empty_trip,
            shopping_pricing_without_estimate,
            shopping_missing_capacity,
            shopping_orphan_capacity,
            shopping_priced_covered,
            shopping_noncanonical_prep,
            shopping_unresolved_wrong_schema,
            valid_plan("2026-W32"),
        ]
        for payload in malformed:
            write_plan(web.PLANS_DIR, "2026-W31", payload)
            assert all(row["week"] != "2026-W31" for row in web.list_week_plans())
            try:
                web.load_week_plan("2026-W31")
                raise AssertionError(f"malformed plan accepted: {payload!r}")
            except HTTPException as exc:
                assert exc.status_code == 404

        (web.PLANS_DIR / "2026-W31.json").write_bytes(b"\xff\xfeinvalid-json")
        assert all(row["week"] != "2026-W31" for row in web.list_week_plans())
        try:
            web.load_week_plan("2026-W31")
            raise AssertionError("invalid UTF-8 plan accepted")
        except HTTPException as exc:
            assert exc.status_code == 404

        try:
            web.load_week_plan("2026-W99")
            raise AssertionError("impossible ISO week accepted")
        except HTTPException as exc:
            assert exc.status_code == 400

    plan_routes = {
        route.path: route.methods
        for route in web.app.routes
        if route.path.startswith("/api/plans")
    }
    assert plan_routes == {
        "/api/plans": {"GET"},
        "/api/plans/{week_id}": {"GET"},
    }

    html = (WEB_DIR / "static" / "index.html").read_text(encoding="utf-8")
    assert "function escapeHtml" in html
    assert "escapeHtml(m.dish)" in html
    assert "escapeHtml(day.note)" in html
    assert "escapeHtml(x)" in html
    assert "function renderPlanShopping" in html
    assert "escapeHtml(item.ingredient)" in html
    assert "weekly_budget_status" in html
    assert "unpriced_trip_items" in html
    print("web weekly-plan tests: PASS")


if __name__ == "__main__":
    main()
