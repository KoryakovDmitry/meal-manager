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
    print("web weekly-plan tests: PASS")


if __name__ == "__main__":
    main()
