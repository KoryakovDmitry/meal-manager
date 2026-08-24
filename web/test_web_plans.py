"""Focused tests for weekly-plan Web read and mutation contracts.

Usage: python3 web/test_web_plans.py
"""

import importlib.util
import json
import re
import tempfile
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


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted(
        (luminance(foreground), luminance(background)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


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
        original_current_week = getattr(web, "_current_week_id", None)
        original_fridge_path = web.FRIDGE_PATH
        original_dishes_path = web.DISHES_PATH
        original_prep_path = web.PREP_ITEMS_PATH
        original_shopping_requests_path = web.SHOPPING_REQUESTS_PATH
        web.FRIDGE_PATH = Path(tmp) / "fridge.json"
        web.DISHES_PATH = Path(tmp) / "dishes.json"
        web.PREP_ITEMS_PATH = Path(tmp) / "prep_items.json"
        web.SHOPPING_REQUESTS_PATH = Path(tmp) / "shopping_requests.json"
        web.FRIDGE_PATH.write_text(
            json.dumps({"schema_version": 4, "items": []}), encoding="utf-8"
        )
        web.DISHES_PATH.write_text(json.dumps({"dishes": [{
            "name": "soup <script>",
            "ingredients": {"carrot <b>": True},
        }]}), encoding="utf-8")
        (Path(tmp) / "prep_items.json").write_text(json.dumps({"prep_items": [{
            "name": "stock", "ingredients": {}, "yield": 1,
            "yield_unit": "portion", "storage": "freezer", "remaining": 1,
        }]}), encoding="utf-8")
        web._current_week_id = lambda: "2026-W30"
        try:
            shopping_view = web.get_shopping()
            web.FRIDGE_PATH.write_text(json.dumps({
                "schema_version": 5,
                "items": [{
                    "id": "inv_carrot", "name": "exact carrot product",
                    "aliases": ["carrot <b>"], "available": False,
                    "category": "product", "ever_stocked": True,
                    "quantity": None, "unit": None, "package_count": None,
                    "storage": None, "expires_on": None, "comment": None,
                    "created_at": "2026-07-15T00:00:00+00:00",
                    "updated_at": "2026-07-15T00:00:00+00:00",
                }],
            }), encoding="utf-8")
            known_missing_view = web.get_shopping()
            web.DISHES_PATH.write_text(json.dumps({"dishes": [{
                "name": "soup <script>",
                "ingredients": {"cabbage": True},
            }]}), encoding="utf-8")
            recipe_changed_view = web.get_shopping()
            web.FRIDGE_PATH.write_text(json.dumps({
                "schema_version": 5,
                "items": [{
                    "id": "inv_cabbage", "name": "exact cabbage product",
                    "aliases": ["cabbage"], "available": True,
                    "category": "product", "ever_stocked": True,
                    "quantity": None, "unit": None, "package_count": None,
                    "storage": None, "expires_on": None, "comment": None,
                    "created_at": "2026-07-15T00:00:00+00:00",
                    "updated_at": "2026-07-15T00:00:00+00:00",
                }],
            }), encoding="utf-8")
            inventory_changed_view = web.get_shopping()
            web.DISHES_PATH.write_text("{broken", encoding="utf-8")
            failed_projection = web.get_shopping()
        finally:
            if original_current_week is None:
                del web._current_week_id
            else:
                web._current_week_id = original_current_week
            web.FRIDGE_PATH = original_fridge_path
            web.DISHES_PATH = original_dishes_path
            web.PREP_ITEMS_PATH = original_prep_path
            web.SHOPPING_REQUESTS_PATH = original_shopping_requests_path
        assert shopping_view["week"] == "2026-W30"
        assert shopping_view["source"] == "weekly_plan"
        assert [item["ingredient"] for item in shopping_view["items"]] == ["carrot <b>"]
        assert shopping_view["items"][0]["kind"] == "abstract_request"
        assert known_missing_view["items"][0]["kind"] == "known_missing"
        assert known_missing_view["items"][0]["product_id"] == "inv_carrot"
        assert shopping_view["items"][0]["id"].startswith("shop_")
        assert "unlocks" not in shopping_view["items"][0]
        assert shopping_view["persisted_stale"] is True
        assert [item["ingredient"] for item in recipe_changed_view["items"]] == ["cabbage"]
        assert recipe_changed_view["persisted_stale"] is True
        assert inventory_changed_view["items"] == []
        assert inventory_changed_view["persisted_stale"] is True
        assert failed_projection["persisted_stale"] is True
        assert failed_projection["items"] == []
        assert "invalid dish catalog" in failed_projection["projection_error"]
        rows = web.list_week_plans()
        assert rows == [{
            "week": "2026-W30",
            "status": "draft",
            "meals_count": 1,
            "prep_count": 1,
        }]
        assert web.load_week_plan("2026-W30")["days"]["mon"]["meals"][0]["portions_planned"] == 4

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
        manual_snapshot = valid_plan("2026-W31")
        manual_snapshot["shopping"]["items"][0]["required_by"] = [
            {"kind": "manual", "name": "carrot", "uses": 2}
        ]
        write_plan(web.PLANS_DIR, "2026-W31", manual_snapshot)
        assert web.load_week_plan("2026-W31")["shopping"]["items"][0]["required_by"][0]["kind"] == "manual"

        for payload in malformed:
            write_plan(web.PLANS_DIR, "2026-W31", payload)
            assert all(row["week"] != "2026-W31" for row in web.list_week_plans())
            try:
                web.load_week_plan("2026-W31")
                raise AssertionError(f"malformed plan accepted: {payload!r}")
            except HTTPException as exc:
                assert exc.status_code == 503

        (web.PLANS_DIR / "2026-W31.json").write_bytes(b"\xff\xfeinvalid-json")
        assert all(row["week"] != "2026-W31" for row in web.list_week_plans())
        try:
            web.load_week_plan("2026-W31")
            raise AssertionError("invalid UTF-8 plan accepted")
        except HTTPException as exc:
            assert exc.status_code == 503

        try:
            web.load_week_plan("2026-W99")
            raise AssertionError("impossible ISO week accepted")
        except HTTPException as exc:
            assert exc.status_code == 400

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        original_paths = (
            web.DISHES_PATH, web.FRIDGE_PATH, web.HISTORY_PATH, web.PLANS_DIR,
        )
        web.DISHES_PATH = data_dir / "dishes.json"
        web.FRIDGE_PATH = data_dir / "fridge.json"
        web.HISTORY_PATH = data_dir / "history.json"
        web.PLANS_DIR = data_dir / "plans"
        web.PLANS_DIR.mkdir()
        web.DISHES_PATH.write_text(json.dumps({"dishes": [
            {"name": "суп", "ingredients": {"лук": True, "морковь": True}},
            {"name": "паста", "ingredients": {"лук": True, "томаты": True}},
        ]}, ensure_ascii=False), encoding="utf-8")
        web.FRIDGE_PATH.write_text(
            json.dumps(["лук", "морковь", "банан"], ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            original_dishes_payload = json.loads(
                web.DISHES_PATH.read_text(encoding="utf-8")
            )
            history_only_payload = json.loads(json.dumps(original_dishes_payload))
            history_only_payload["dishes"].append({
                "name": "history-only dish",
                "ingredients": {},
            })
            web.DISHES_PATH.write_text(
                json.dumps(history_only_payload, ensure_ascii=False), encoding="utf-8"
            )
            cooked = web.add_history(web.CookedMeal(
                dish="history-only dish",
                date="2026-07-15T12:00:00+00:00",
                actual_portions=2,
                actual_yield_portions=3,
            ))
            cook_id = cooked["entry"]["id"]
            assert cook_id.startswith("cook_")
            assert cooked["entry"]["dish"] == "history-only dish"
            assert cooked["entry"]["actual_yield_portions"] == 3
            assert web.get_history()["history"][0]["id"] == cook_id
            retracted = web.delete_history_entry(cook_id)
            assert retracted["entry"]["id"] == cook_id
            assert retracted["entry"]["status"] == "retracted"
            assert len(web.get_history()["history"]) == 1

            occurrence_plan = valid_plan("2026-W31")
            for day in occurrence_plan["days"].values():
                day["meals"] = []
            write_plan(web.PLANS_DIR, "2026-W31", occurrence_plan)
            occurrence_view = web.get_week_plan_view("2026-W31")
            occurrence_added = web.add_plan_meal(
                "2026-W31",
                "wed",
                web.PlanMealCreate(
                    dish="history-only dish",
                    portions=2,
                    expected_version=occurrence_view["version"],
                ),
            )
            occurrence = occurrence_added["plan"]["days"]["wed"]["meals"][0]
            stale_before = {
                path: path.read_bytes()
                for path in (
                    web.HISTORY_PATH,
                    web.FRIDGE_PATH,
                    web.PLANS_DIR / "2026-W31.json",
                )
            }
            try:
                web.add_history(web.CookedMeal(
                    dish="history-only dish",
                    occurrence_id=occurrence["occurrence_id"],
                    expected_revision=occurrence["revision"] + 1,
                ))
                raise AssertionError("stale Web cook revision succeeded")
            except HTTPException as exc:
                assert exc.status_code == 400
            assert all(
                path.read_bytes() == payload for path, payload in stale_before.items()
            )

            legacy_cooked = web.add_history(web.CookedMeal(
                dish="history-only dish",
                date="2026-07-16",
            ))
            legacy_retracted = web.delete_history_entry("1")
            assert legacy_retracted["entry"]["id"] == legacy_cooked["entry"]["id"]
            assert legacy_retracted["entry"]["status"] == "retracted"
            web.DISHES_PATH.write_text(
                json.dumps(original_dishes_payload, ensure_ascii=False), encoding="utf-8"
            )

            initial_recipe_version = web.get_dishes()["version"]
            created_recipe = web.add_dish(web.DishCreate(
                name="Web recipe QA",
                ingredients={"web qa ingredient": True},
                instructions="  Chop onion.\nCook for five minutes.  ",
                expected_version=initial_recipe_version,
            ))
            assert created_recipe["dish"]["instructions"] == "Chop onion.\nCook for five minutes."
            listed_recipe = next(
                dish for dish in web.get_dishes()["dishes"]
                if dish["name"] == "web recipe qa"
            )
            assert listed_recipe["instructions"] == "Chop onion.\nCook for five minutes."
            created_version = created_recipe["version"]
            preserved_recipe = web.update_dish(
                "web recipe qa",
                web.DishUpdate(expected_version=created_version),
            )
            assert preserved_recipe["dish"]["instructions"] == "Chop onion.\nCook for five minutes."
            boundary_recipe = web.update_dish(
                "web recipe qa",
                web.DishUpdate(
                    instructions="\u2003" + "😀" * 20_000 + "\u2003",
                    expected_version=preserved_recipe["version"],
                ),
            )
            assert boundary_recipe["dish"]["instructions"] == "😀" * 20_000
            oversized_baseline = web.DISHES_PATH.read_bytes()
            try:
                web.update_dish(
                    "web recipe qa",
                    web.DishUpdate(
                        instructions="😀" * 20_001,
                        expected_version=boundary_recipe["version"],
                    ),
                )
                raise AssertionError("oversized Unicode instructions accepted")
            except HTTPException as exc:
                assert exc.status_code == 400
            assert web.DISHES_PATH.read_bytes() == oversized_baseline
            null_recipe = web.update_dish(
                "web recipe qa",
                web.DishUpdate(
                    instructions=None,
                    expected_version=boundary_recipe["version"],
                ),
            )
            assert "instructions" not in null_recipe["dish"]
            whitespace_recipe = web.update_dish(
                "web recipe qa",
                web.DishUpdate(
                    instructions=" " * 20_001,
                    expected_version=null_recipe["version"],
                ),
            )
            assert "instructions" not in whitespace_recipe["dish"]
            edited_recipe = web.update_dish(
                "web recipe qa",
                web.DishUpdate(
                    instructions="Bake instead.",
                    expected_version=whitespace_recipe["version"],
                ),
            )
            assert edited_recipe["dish"]["instructions"] == "Bake instead."
            cleared_recipe = web.update_dish(
                "web recipe qa",
                web.DishUpdate(
                    instructions="   ",
                    expected_version=edited_recipe["version"],
                ),
            )
            assert "instructions" not in cleared_recipe["dish"]
            assert "instructions" not in next(
                dish for dish in web.get_dishes()["dishes"]
                if dish["name"] == "web recipe qa"
            )
            try:
                web.update_dish(
                    "web recipe qa",
                    web.DishUpdate(
                        instructions="stale overwrite",
                        expected_version=created_version,
                    ),
                )
                raise AssertionError("stale recipe update accepted")
            except HTTPException as exc:
                assert exc.status_code == 409
                assert exc.detail["code"] == "dish_catalog_conflict"
                assert exc.detail["current_version"] == cleared_recipe["version"]
            stale_baseline = web.DISHES_PATH.read_bytes()
            try:
                web.add_dish(web.DishCreate(
                    name="stale create",
                    ingredients={"water": True},
                    instructions="must not persist",
                    expected_version=created_version,
                ))
                raise AssertionError("stale recipe create accepted")
            except HTTPException as exc:
                assert exc.status_code == 409
                assert exc.detail["code"] == "dish_catalog_conflict"
            assert web.DISHES_PATH.read_bytes() == stale_baseline
            try:
                web.delete_dish("web recipe qa", created_version)
                raise AssertionError("stale recipe delete accepted")
            except HTTPException as exc:
                assert exc.status_code == 409
                assert exc.detail["code"] == "dish_catalog_conflict"
            assert web.DISHES_PATH.read_bytes() == stale_baseline
            assert "instructions" not in next(
                dish for dish in web.get_dishes()["dishes"]
                if dish["name"] == "web recipe qa"
            )

            stats = web.get_stats()
            try:
                rename_payload = web.FridgeRename(
                    old_ingredient=" БАНАН ",
                    new_ingredient="Жёлтый банан",
                )
                renamed = web.rename_fridge_item(rename_payload)
            except AttributeError as exc:
                raise AssertionError("web fridge rename API is missing") from exc
            assert renamed["renamed"] == {
                "old_ingredient": "банан",
                "new_ingredient": "жёлтый банан",
            }
            assert web.load_fridge() == ["лук", "морковь", "жёлтый банан"]

            no_op = web.rename_fridge_item(web.FridgeRename(
                old_ingredient=" ЖЁЛТЫЙ БАНАН ",
                new_ingredient="жёлтый банан",
            ))
            assert no_op["changed"] is False
            baseline_fridge = web.load_fridge()
            for payload, expected_status in (
                (web.FridgeRename(old_ingredient="жёлтый банан", new_ingredient="лук"), 409),
                (web.FridgeRename(old_ingredient="нет такого", new_ingredient="замена"), 404),
                (web.FridgeRename(old_ingredient="жёлтый банан", new_ingredient="   "), 400),
            ):
                try:
                    web.rename_fridge_item(payload)
                    raise AssertionError(f"rename edge unexpectedly succeeded: {payload}")
                except HTTPException as exc:
                    assert exc.status_code == expected_status
                assert web.load_fridge() == baseline_fridge

            try:
                created_item = web.add_inventory_item(web.InventoryItemCreate(
                    name="Молоко QA",
                    category="prep",
                    quantity="2.0",
                    unit="l",
                    package_count=2,
                    storage="fridge",
                    expires_on="2026-07-17",
                    comment="тест",
                ))
            except AttributeError as exc:
                raise AssertionError("structured inventory web API is missing") from exc
            structured_id = created_item["item"]["id"]
            structured_version = created_item["item"]["updated_at"]
            assert created_item["item"]["quantity"] == "2"
            assert created_item["item"]["category"] == "prep"
            assert any(
                item["id"] == structured_id
                for item in web.list_inventory_items()["items"]
            )
            updated_item = web.edit_inventory_item(
                structured_id,
                web.InventoryItemPatch(
                    comment=None,
                    quantity="1.5",
                    unit="l",
                    category="ready_meal",
                    expected_updated_at=structured_version,
                ),
            )
            assert updated_item["item"]["comment"] is None
            assert updated_item["item"]["quantity"] == "1.5"
            assert updated_item["item"]["category"] == "ready_meal"
            updated_version = updated_item["item"]["updated_at"]
            bytes_before_stale = web.FRIDGE_PATH.read_bytes()
            try:
                web.edit_inventory_item(
                    structured_id,
                    web.InventoryItemPatch(
                        storage="pantry",
                        expected_updated_at=structured_version,
                    ),
                )
                raise AssertionError("stale Web inventory edit succeeded")
            except HTTPException as exc:
                assert exc.status_code == 409
                detail: Any = exc.detail
                assert isinstance(detail, dict)
                assert detail["code"] == "inventory_conflict"
                assert detail["current_item"]["updated_at"] == updated_version
            assert web.FRIDGE_PATH.read_bytes() == bytes_before_stale
            try:
                web.InventoryItemCreate(name="x" * 201)
                raise AssertionError("web create accepted an overlong inventory name")
            except Exception as exc:
                assert exc.__class__.__name__ == "ValidationError"
            try:
                web.InventoryItemCreate(name="strict package", package_count=True)
                raise AssertionError("web create coerced boolean package_count")
            except Exception as exc:
                assert exc.__class__.__name__ == "ValidationError"
            try:
                web.edit_inventory_item(
                    structured_id,
                    web.InventoryItemPatch(
                        name=None,
                        expected_updated_at=updated_version,
                    ),
                )
                raise AssertionError("web edit accepted a null inventory name")
            except HTTPException as exc:
                assert exc.status_code == 400
            try:
                web.remove_inventory_item(
                    structured_id,
                    expected_updated_at=structured_version,
                )
                raise AssertionError("stale Web inventory delete succeeded")
            except HTTPException as exc:
                assert exc.status_code == 409
                detail: Any = exc.detail
                assert isinstance(detail, dict)
                assert detail["current_item"]["updated_at"] == updated_version
            assert web.FRIDGE_PATH.read_bytes() == bytes_before_stale
            deleted_item = web.remove_inventory_item(
                structured_id,
                expected_updated_at=updated_version,
            )
            assert deleted_item["item"]["id"] == structured_id
            assert "молоко qa" not in web.load_fridge()
            try:
                web.edit_inventory_item(
                    structured_id,
                    web.InventoryItemPatch(
                        comment="stale after delete",
                        expected_updated_at=updated_version,
                    ),
                )
                raise AssertionError("stale Web edit after delete succeeded")
            except HTTPException as exc:
                assert exc.status_code == 409
                detail: Any = exc.detail
                assert detail["current_item"]["available"] is False

            catalog_rows = web.list_product_catalog(
                status="out_of_stock", category="ready_meal", query="МОЛОКО",
            )["items"]
            assert [row["name"] for row in catalog_rows] == ["молоко qa"]
            assert catalog_rows[0]["id"] == structured_id
            recategorized_stock = web.set_product_category(web.ProductCategoryPatch(
                product_id=structured_id,
                category="prep",
                expected_updated_at=catalog_rows[0]["updated_at"],
            ))
            bytes_before_name_aba = web.FRIDGE_PATH.read_bytes()
            for stale_call in (
                lambda: web.set_product_category(web.ProductCategoryPatch(
                    name="молоко qa",
                    category="ready_meal",
                    expected_updated_at=recategorized_stock["item"]["updated_at"],
                )),
                lambda: web.replenish_product(web.ProductReplenish(
                    name="молоко qa",
                    expected_updated_at=recategorized_stock["item"]["updated_at"],
                )),
            ):
                try:
                    stale_call()
                    raise AssertionError("persisted Web identity accepted a name-keyed OCC mutation")
                except HTTPException as exc:
                    assert exc.status_code == 400
            assert web.FRIDGE_PATH.read_bytes() == bytes_before_name_aba
            bytes_before_stale_replenish = web.FRIDGE_PATH.read_bytes()
            try:
                web.replenish_product(web.ProductReplenish(
                    product_id=structured_id,
                    category="ready_meal",
                    expected_updated_at=catalog_rows[0]["updated_at"],
                ))
                raise AssertionError("stale Web replenish overwrote a newer category")
            except HTTPException as exc:
                assert exc.status_code == 409
            assert web.FRIDGE_PATH.read_bytes() == bytes_before_stale_replenish
            try:
                web.list_product_catalog(status="all", query="x" * 201)
                raise AssertionError("catalog accepted an overlong query")
            except HTTPException as exc:
                assert exc.status_code == 400
            recipe_only = web.list_product_catalog(status="recipe_only", query="томаты")["items"]
            assert [row["name"] for row in recipe_only] == ["томаты"]
            categorized_recipe = web.set_product_category(web.ProductCategoryPatch(
                name="томаты",
                category="prep",
                expected_updated_at=None,
            ))
            assert categorized_recipe["item"]["status"] == "recipe_only"
            assert categorized_recipe["item"]["category"] == "prep"
            assert categorized_recipe["item"]["id"] is not None
            bytes_before_recipe_conflict = web.FRIDGE_PATH.read_bytes()
            try:
                web.set_product_category(web.ProductCategoryPatch(
                    name="томаты",
                    category="ready_meal",
                    expected_updated_at=None,
                ))
                raise AssertionError("stale recipe-only category edit succeeded")
            except HTTPException as exc:
                assert exc.status_code == 409
            assert web.FRIDGE_PATH.read_bytes() == bytes_before_recipe_conflict

            restored_item = web.replenish_product(web.ProductReplenish(
                product_id=structured_id,
                quantity="1",
                unit="l",
                storage="fridge",
                expected_updated_at=recategorized_stock["item"]["updated_at"],
            ))
            assert restored_item["item"]["id"] == structured_id
            assert restored_item["item"]["expires_on"] is None
            assert restored_item["item"]["comment"] is None
            assert restored_item["item"]["category"] == "prep"
            assert "молоко qa" in web.load_fridge()
            try:
                web.replenish_product(web.ProductReplenish(
                    product_id=structured_id,
                    expected_updated_at=restored_item["item"]["updated_at"],
                ))
                raise AssertionError("replenishment accepted an already stocked product")
            except HTTPException as exc:
                assert exc.status_code == 409
            web.remove_inventory_item(
                structured_id,
                expected_updated_at=restored_item["item"]["updated_at"],
            )

            promoted_item = web.replenish_product(web.ProductReplenish(
                product_id=categorized_recipe["item"]["id"], storage="pantry",
                expected_updated_at=categorized_recipe["item"]["updated_at"],
            ))
            assert promoted_item["item"]["name"] == "томаты"
            assert promoted_item["item"]["category"] == "prep"
            web.remove_inventory_item(
                promoted_item["item"]["id"],
                expected_updated_at=promoted_item["item"]["updated_at"],
            )

            inventory_before_history_failure = web._fridge_repository().load_catalog_items()
            history_before_failure = web.load_history()

            def fail_prepared_history(stage):
                if stage == "after_prepare":
                    raise OSError("simulated history transaction failure")

            try:
                web._web_audit_manager._fault_injector = fail_prepared_history
                web.add_history(web.CookedMeal(dish="суп"))
                raise AssertionError("history transaction failure was not surfaced")
            except HTTPException as exc:
                assert exc.status_code == 503
            finally:
                web._web_audit_manager._fault_injector = None
            assert web.load_history() == history_before_failure
            assert [item.to_dict() for item in web._fridge_repository().load_catalog_items()] == [
                item.to_dict() for item in inventory_before_history_failure
            ]

            valid_history_bytes = web.HISTORY_PATH.read_bytes()
            web.HISTORY_PATH.write_text("{broken", encoding="utf-8")
            for call in (web.get_history, web.get_suggestions, web.get_stats):
                try:
                    call()
                    raise AssertionError("corrupt history was treated as empty")
                except HTTPException as exc:
                    assert exc.status_code == 503
                    assert "decoder" not in exc.detail.lower()
            web.HISTORY_PATH.write_bytes(valid_history_bytes)
            web.HISTORY_PATH.write_text(json.dumps({
                "schema_version": 2,
                "entries": "not-a-list",
            }), encoding="utf-8")
            for call in (web.get_history, web.get_suggestions, web.get_stats):
                try:
                    call()
                    raise AssertionError("semantic history corruption was accepted")
                except HTTPException as exc:
                    assert exc.status_code == 503
                    assert "entries" not in exc.detail.lower()
            with TestClient(web.app) as client:
                for path in ("/api/history", "/api/suggestions", "/api/stats"):
                    response = client.get(path)
                    assert response.status_code == 503
                    assert "entries" not in response.text.lower()
            web.HISTORY_PATH.write_bytes(valid_history_bytes)

            valid_inventory_before_bad_request = web.load_fridge()
            invalid_legacy_calls = [
                lambda: web.set_fridge(web.FridgeUpdate(ingredients=[" "])),
                lambda: web.set_fridge(web.FridgeUpdate(ingredients=["x" * 201])),
                lambda: web.add_to_fridge(web.FridgeAddRemove(ingredient=" ")),
                lambda: web.add_to_fridge(web.FridgeAddRemove(ingredient="x" * 201)),
                lambda: web.remove_from_fridge(web.FridgeAddRemove(ingredient=" ")),
            ]
            for call in invalid_legacy_calls:
                try:
                    call()
                    raise AssertionError("invalid legacy inventory name was accepted")
                except HTTPException as exc:
                    assert exc.status_code == 400
            assert web.load_fridge() == valid_inventory_before_bad_request

            # Regression: stale name/version pairs cannot cross an ABA rename
            # boundary and mutate another stable identity reusing the old name.
            repo = web._fridge_repository()
            original_now = repo._now
            fixed = "2026-07-14T23:59:00+00:00"
            repo._now = lambda: fixed
            try:
                aba_a = web.add_inventory_item(web.InventoryItemCreate(name="aba old"))["item"]
                stale_category_version = aba_a["updated_at"]
                renamed_a = web.edit_inventory_item(
                    aba_a["id"],
                    web.InventoryItemPatch(
                        name="aba current",
                        expected_updated_at=stale_category_version,
                    ),
                )["item"]
                aba_b = web.add_inventory_item(web.InventoryItemCreate(name="aba old"))["item"]
                assert aba_b["updated_at"] == stale_category_version

                before_category_aba = web.FRIDGE_PATH.read_bytes()
                try:
                    web.set_product_category(web.ProductCategoryPatch(
                        product_id=aba_a["id"], category="prep",
                        expected_updated_at=stale_category_version,
                    ))
                    raise AssertionError("stale category crossed an ABA boundary")
                except HTTPException as exc:
                    assert exc.status_code == 409
                for payload in (
                    web.ProductCategoryPatch(
                        name="aba old", category="prep",
                        expected_updated_at=stale_category_version,
                    ),
                    web.ProductCategoryPatch(
                        name="aba old", category="prep", expected_updated_at=None,
                    ),
                ):
                    try:
                        web.set_product_category(payload)
                        raise AssertionError("name-keyed category crossed an ABA boundary")
                    except HTTPException as exc:
                        assert exc.status_code in (400, 409)
                assert web.FRIDGE_PATH.read_bytes() == before_category_aba

                removed_a = web.remove_inventory_item(
                    aba_a["id"], expected_updated_at=renamed_a["updated_at"],
                )["item"]
                stale_replenish_version = removed_a["updated_at"]
                restored_a = web.replenish_product(web.ProductReplenish(
                    product_id=aba_a["id"],
                    expected_updated_at=stale_replenish_version,
                ))["item"]
                renamed_again = web.edit_inventory_item(
                    aba_a["id"],
                    web.InventoryItemPatch(
                        name="aba current 2",
                        expected_updated_at=restored_a["updated_at"],
                    ),
                )["item"]
                web.remove_inventory_item(
                    aba_a["id"], expected_updated_at=renamed_again["updated_at"],
                )
                repo._now = lambda: stale_replenish_version
                removed_b = web.remove_inventory_item(
                    aba_b["id"], expected_updated_at=aba_b["updated_at"],
                )["item"]
                assert removed_b["updated_at"] == stale_replenish_version

                before_replenish_aba = web.FRIDGE_PATH.read_bytes()
                try:
                    web.replenish_product(web.ProductReplenish(
                        product_id=aba_a["id"],
                        expected_updated_at=stale_replenish_version,
                    ))
                    raise AssertionError("stale replenish crossed an ABA boundary")
                except HTTPException as exc:
                    assert exc.status_code == 409
                for expected, status in ((stale_replenish_version, 400), (None, 409)):
                    try:
                        web.replenish_product(web.ProductReplenish(
                            name="aba old", expected_updated_at=expected,
                        ))
                        raise AssertionError("name-keyed replenish crossed an ABA boundary")
                    except HTTPException as exc:
                        assert exc.status_code == status
                assert web.FRIDGE_PATH.read_bytes() == before_replenish_aba
            finally:
                repo._now = original_now

            web.FRIDGE_PATH.write_text("{broken", encoding="utf-8")
            corrupt_calls = [
                lambda: web.get_fridge(),
                lambda: web.set_fridge(web.FridgeUpdate(ingredients=["лук"])),
                lambda: web.add_to_fridge(web.FridgeAddRemove(ingredient="лук")),
                lambda: web.remove_from_fridge(web.FridgeAddRemove(ingredient="лук")),
                lambda: web.rename_fridge_item(web.FridgeRename(old_ingredient="лук", new_ingredient="лук 2")),
                lambda: web.clear_fridge(),
                lambda: web.get_suggestions(),
                lambda: web.get_shopping(),
                lambda: web.get_stats(),
                lambda: web.add_history(web.CookedMeal(dish="суп")),
                lambda: web.list_inventory_items(),
            ]
            for call in corrupt_calls:
                try:
                    call()
                    raise AssertionError("corrupt inventory did not return storage error")
                except HTTPException as exc:
                    assert exc.status_code == 503
                    assert exc.detail == "Inventory storage is temporarily unavailable"
                    assert str(web.FRIDGE_PATH) not in exc.detail
            assert web.load_history() == history_before_failure
            web.FRIDGE_PATH.write_bytes(b"\xff")
            try:
                web.list_inventory_items()
                raise AssertionError("invalid UTF-8 did not return storage error")
            except HTTPException as exc:
                assert exc.status_code == 503
                assert "codec" not in exc.detail.lower()
        finally:
            (
                web.DISHES_PATH, web.FRIDGE_PATH, web.HISTORY_PATH, web.PLANS_DIR,
            ) = original_paths
        assert stats["fridge_utility"] == {"лук": 2, "морковь": 1, "банан": 0}
        assert stats["unused_fridge_items"] == ["банан"]

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        original_plan_paths = (web.PLANS_DIR, web.DISHES_PATH)
        web.PLANS_DIR = data_dir / "plans"
        web.PLANS_DIR.mkdir()
        web.DISHES_PATH = data_dir / "dishes.json"
        web.DISHES_PATH.write_text(json.dumps({"dishes": [
            {"name": "паста", "ingredients": {}},
            {"name": "суп", "ingredients": {}},
        ]}, ensure_ascii=False), encoding="utf-8")
        try:
            write_plan(web.PLANS_DIR, "2026-W30", valid_plan())
            view = web.get_week_plan_view("2026-W30")
            version = view["version"]
            assert version.startswith("sha256:")

            before_stale = (web.PLANS_DIR / "2026-W30.json").read_bytes()
            try:
                web.add_plan_meal(
                    "2026-W30", "wed",
                    web.PlanMealCreate(
                        dish="паста", portions=3, expected_version="sha256:stale",
                    ),
                )
                raise AssertionError("stale plan edit succeeded")
            except HTTPException as exc:
                assert exc.status_code == 409
            assert (web.PLANS_DIR / "2026-W30.json").read_bytes() == before_stale

            added = web.add_plan_meal(
                "2026-W30", "wed",
                web.PlanMealCreate(
                    dish="паста", portions=3, expected_version=version,
                ),
            )
            assert added["meal_index"] == 0
            added_meal = added["plan"]["days"]["wed"]["meals"][0]
            occurrence_id = added_meal["occurrence_id"]
            assert occurrence_id.startswith("mealocc_")
            assert added_meal["dish"] == "паста"
            assert added_meal["portions_planned"] == 3
            assert added_meal["status"] == "planned"
            assert added_meal["planned_for"] == "2026-07-22"
            assert added["plan"]["shopping"] == {}

            edited = web.edit_plan_meal_occurrence(
                "2026-W30", occurrence_id,
                web.PlanMealStableEdit(
                    dish="суп", portions=2, expected_version=added["version"],
                    expected_revision=added_meal["revision"],
                ),
            )
            edited_meal = edited["plan"]["days"]["wed"]["meals"][0]
            assert edited_meal["occurrence_id"] == occurrence_id
            assert edited_meal["dish"] == "суп"
            assert edited_meal["portions_planned"] == 2
            assert edited_meal["revision"] == added_meal["revision"] + 1
            try:
                web.edit_plan_meal_occurrence(
                    "2026-W30", occurrence_id,
                    web.PlanMealStableEdit(
                        dish="паста", portions=1, expected_version=edited["version"],
                        expected_revision=added_meal["revision"],
                    ),
                )
                raise AssertionError("stale occurrence revision succeeded")
            except HTTPException as exc:
                assert exc.status_code == 409

            removed = web.cancel_plan_meal(
                "2026-W30", occurrence_id,
                expected_version=edited["version"],
                expected_revision=edited_meal["revision"],
            )
            cancelled_meal = removed["plan"]["days"]["wed"]["meals"][0]
            assert removed["cancelled"]["occurrence_id"] == occurrence_id
            assert cancelled_meal["occurrence_id"] == occurrence_id
            assert cancelled_meal["status"] == "cancelled"

            orphan_edited = web.edit_plan_meal(
                "2026-W30", "mon", 0,
                web.PlanMealEdit(
                    dish="soup <script>", portions=5,
                    expected_version=removed["version"],
                ),
            )
            orphan_meal = orphan_edited["plan"]["days"]["mon"]["meals"][0]
            assert orphan_meal["dish"] == "soup <script>"
            assert orphan_meal["portions_planned"] == 5
            assert orphan_meal["status"] == "planned"
            assert orphan_meal["occurrence_id"].startswith("mealocc_")

            approved = orphan_edited["plan"]
            approved["status"] = "approved"
            write_plan(web.PLANS_DIR, "2026-W30", approved)
            approved_view = web.get_week_plan_view("2026-W30")
            try:
                web.add_plan_meal(
                    "2026-W30", "thu",
                    web.PlanMealCreate(
                        dish="паста", portions=2,
                        expected_version=approved_view["version"],
                    ),
                )
                raise AssertionError("non-draft plan edit succeeded")
            except HTTPException as exc:
                assert exc.status_code == 409

            before_delete_conflict = (web.PLANS_DIR / "2026-W30.json").read_bytes()
            try:
                web.delete_week_plan("2026-W30", expected_version="sha256:stale")
                raise AssertionError("stale plan delete succeeded")
            except HTTPException as exc:
                assert exc.status_code == 409
            assert (web.PLANS_DIR / "2026-W30.json").read_bytes() == before_delete_conflict

            deleted = web.delete_week_plan(
                "2026-W30", expected_version=approved_view["version"],
            )
            assert deleted == {"status": "ok", "week": "2026-W30"}
            try:
                web.delete_week_plan("2026-W30", expected_version=approved_view["version"])
                raise AssertionError("missing plan delete succeeded")
            except HTTPException as exc:
                assert exc.status_code == 404
        finally:
            web.PLANS_DIR, web.DISHES_PATH = original_plan_paths

    plan_routes = {}
    for route in web.app.routes:
        if route.path.startswith("/api/plans"):
            plan_routes.setdefault(route.path, set()).update(route.methods)
    assert plan_routes == {
        "/api/plans": {"GET"},
        "/api/plans/{week_id}": {"GET", "DELETE"},
        "/api/plans/{week_id}/days/{day}/meals": {"POST"},
        "/api/plans/{week_id}/days/{day}/meals/{meal_index}": {"PATCH", "DELETE"},
        "/api/plans/{week_id}/meals/{occurrence_id}": {"PATCH", "DELETE"},
    }
    stable_cancel = web.app.openapi()["paths"][
        "/api/plans/{week_id}/meals/{occurrence_id}"
    ]["delete"]
    cancel_parameters = {
        item["name"]: item for item in stable_cancel["parameters"]
    }
    assert cancel_parameters["expected_version"]["required"] is True
    assert cancel_parameters["expected_revision"]["required"] is True
    assert cancel_parameters["expected_revision"]["schema"]["minimum"] == 1
    product_routes = {
        route.path: route.methods
        for route in web.app.routes
        if route.path.startswith("/api/products")
    }
    assert product_routes == {
        "/api/products": {"GET"},
        "/api/products/category": {"PATCH"},
        "/api/products/replenish": {"POST"},
    }

    html = (WEB_DIR / "static" / "index.html").read_text(encoding="utf-8")
    assert "function escapeHtml" in html
    assert "escapeHtml(meal.dish)" in html
    assert "escapeHtml(day.note)" in html
    assert "escapeHtml(x)" in html
    assert "function renderPlanShopping" in html
    assert "escapeHtml(item.ingredient)" in html
    assert "weekly_budget_status" in html
    assert "unpriced_trip_items" in html
    assert "Редактировать блюда можно у draft-планов" in html
    assert "function showPlanMealModal" in html
    assert "function deleteWeekPlan" in html
    assert "function deletePlanMeal" in html
    assert "expected_version" in html
    assert "Удалить недельный план" in html

    # UI/UX navigation contract: local favicon, vertical collapsible sidebar,
    # persisted desktop state, and dismissible mobile drawer.
    assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in html
    assert (WEB_DIR / "static" / "favicon.svg").is_file()
    assert any(route.path == "/static" for route in web.app.routes)
    assert '<aside class="sidebar" id="app-sidebar"' in html
    assert 'id="sidebar-toggle"' in html
    assert 'aria-controls="app-sidebar"' in html
    assert "Развернуть подписи меню" in html
    assert 'id="mobile-menu-toggle"' in html
    assert 'id="mobile-menu-close"' in html
    assert 'id="sidebar-backdrop"' in html
    assert '<div class="sidebar-backdrop" id="sidebar-backdrop" aria-hidden="true"></div>' in html
    assert "function trapMobileNavigationFocus(event)" in html
    assert "event.shiftKey" in html
    assert ".fridge-item .remove," in html
    assert ".ing-editor-row .toggle-ess," in html
    assert ".ing-editor-row .del-ing { min-width: 44px; min-height: 44px" in html
    assert ".icon-action { min-width: 44px; min-height: 44px" in html
    assert 'data-action="delete" data-value="${dishName}" aria-label="Удалить рецепт ${dishName}"' in html
    assert 'data-action="edit" data-value="${suggestionName}" aria-label="Изменить рецепт ${suggestionName}"' in html
    assert 'aria-label="Исправить запись истории ${dishName}"' in html
    assert 'aria-label="Удалить продукт ${safeItem}"' in html
    assert 'aria-label="Редактировать продукт ${safeItem}"' in html
    assert 'data-action="edit" data-value="${safeItem}"' in html
    assert 'id="fridge-add-quantity"' in html
    assert 'id="fridge-add-category"' in html
    assert 'id="fridge-category-filter"' in html
    assert 'id="fridge-add-storage"' in html
    assert 'id="fridge-edit-category"' in html
    assert 'id="fridge-edit-expiry"' in html
    assert 'id="product-catalog-category"' in html
    assert 'id="replenish-category"' in html
    assert "const PRODUCT_CATEGORY_LABELS" in html
    assert "function showProductCategoryModal" in html
    assert "function saveProductCategory" in html
    assert "'/api/products/category', 'PATCH'" in html
    assert 'category-badge' in html
    assert "metadata.push('⚠ Просрочено')" in html
    assert "metadata.push('⚠ Скоро истекает срок')" in html
    assert "'/api/inventory/items', 'POST'" in html
    assert "'PATCH', payload" in html
    assert "payload.expected_updated_at = record.updated_at" in html
    assert "expected_updated_at=${encodeURIComponent(record.updated_at)}" in html
    assert "e.status === 409" in html
    assert "inventory_conflict" in html
    assert "current.available === false" in html
    assert "Hermes увидит актуальный запас на следующем ходе Meal Planning" in html
    assert "function startFridgeItemEdit" in html
    assert "function saveFridgeItemEdit" in html
    assert "function cancelFridgeItemEdit" in html
    assert 'data-tab="products" data-title="Каталог продуктов"' in html
    assert 'id="page-products"' in html
    assert 'id="product-catalog-search"' in html
    assert 'id="product-catalog-status"' in html
    assert "function renderProductCatalog" in html
    assert "function showReplenishProductModal" in html
    assert "function saveReplenishedProduct" in html
    assert "'/api/products/replenish', 'POST'" in html
    assert "['каталог продуктов', '/api/products']" in html
    assert "'/api/fridge/item', 'PUT'" in html
    assert 'class="fridge-edit-input wide"' in html
    assert 'data-edit-action="save"' in html
    assert 'data-edit-action="cancel"' in html
    assert 'aria-label="Удалить ингредиент ${safeName}"' in html
    assert "bindDataActions" in html
    unsafe_inline_data = re.compile(
        r'on(?:click|keydown|change)="[^"]*\$\{(?:d\.name|s\.name|h\.dish|item|name)'
    )
    assert unsafe_inline_data.search(html) is None
    assert 'role="dialog" aria-modal="true" aria-labelledby="dish-modal-title"' in html
    assert "function trapModalFocus(event)" in html
    assert "modalReturnFocus" in html
    assert "document.querySelector('.app').setAttribute('inert', '')" in html
    assert '<button class="plan-row ' in html
    assert ".toast.error { background: var(--primary); color: white; }" in html
    assert '<div class="tabs">' not in html
    assert "meal-sidebar-collapsed" in html
    assert "aria-current" in html
    assert "event.key === 'Escape'" in html
    assert "sidebar.setAttribute('inert', '')" in html
    assert "sidebar.removeAttribute('inert')" in html
    assert "mobileMenuClose.focus()" in html
    assert "returnTarget.focus()" in html
    assert ".sidebar-toggle { width: 44px; height: 44px" in html
    assert "--text-muted: #78808d;" in html
    assert _contrast_ratio("#78808d", "#0e1015") >= 4.5
    assert _contrast_ratio("#78808d", "#12141a") >= 4.5
    assert ".ingredient-tag.optional { color: var(--text-muted);" in html
    assert ".fridge-item.unused { color: var(--text-muted);" in html
    assert ".dish-card.recent { border-style: dashed; }" in html
    assert ".ingredient-tag.optional { opacity:" not in html
    assert ".fridge-item.unused { opacity:" not in html
    assert ".dish-card.recent { opacity:" not in html
    assert "--primary: #cf3b55;" in html
    assert "--primary-hover: #c4314d;" in html
    assert _contrast_ratio("#ffffff", "#cf3b55") >= 4.5
    assert _contrast_ratio("#ffffff", "#c4314d") >= 4.5
    assert "@media (max-width: 768px)" in html
    print("web weekly-plan tests: PASS")


if __name__ == "__main__":
    main()
