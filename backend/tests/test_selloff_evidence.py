"""'What the evidence says' — one research registry feeds the evidence panel AND every tell's grade chip."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services import market_internals_service as mi
from app.services import selloff_evidence as ev
from app.services import selloff_monitor_service as sm

from test_market_internals import _panel
from test_selloff_monitor import _data_dict, _grid_with, _levels


def test_registry_integrity():
    ids = [e["id"] for e in ev.EVIDENCE]
    assert len(ids) == len(set(ids))
    for e in ev.EVIDENCE:
        assert e["grade"] in ev.GRADES and e["signal"] and e["finding"] and e["on_page"]
        if e["grade"] != "descriptive":
            assert e["sources"] and all(s["url"].startswith("https://") and s["label"] for s in e["sources"])
    assert set(ev.TELL_TO_EVIDENCE.values()) <= set(ev.BY_ID)
    panel_grades = {e["grade"] for e in ev.EVIDENCE if e["grade"] != "descriptive"}
    assert panel_grades == {"strong", "moderate", "weak", "folklore"}


def _all_tells():
    g = _grid_with(spy_z=-2.0, oas_bp=7, ratio=0.97, jpy=-0.3)
    g["credit_model"] = {"excess_z": 0.5, "excess_bp": 3.0, "actual_bp": 7.0, "expected_bp": 4.0, "as_of": "2026-10-15"}
    t1 = sm.build_tells(_levels(), g, None, {"available": False})
    c, v, _ = _panel()
    t2 = mi.build_internal_tells(mi.build_breadth(c, v))
    return t1 + t2


def test_every_tell_maps_to_the_registry_and_matches_it():
    tells = _all_tells()
    assert {t["id"] for t in tells} <= set(ev.TELL_TO_EVIDENCE)          # no tell without an evidence entry
    for t in tells:
        e = ev.BY_ID[ev.TELL_TO_EVIDENCE[t["id"]]]
        assert t["evidence"] == {"grade": e["grade"], "note": e["finding"], "ref": e["id"]}


@pytest.mark.parametrize("tell_id,grade", [
    ("break_low", "descriptive"), ("reclaim", "descriptive"), ("trend", "moderate"), ("credit_wide", "moderate"),
    ("vix_curve", "weak"), ("yen", "weak"), ("breadth", "weak"), ("nl_gt_nh", "weak"),
    ("zweig", "folklore"), ("down90", "folklore"), ("washout", "folklore"),
])
def test_tell_grades_follow_the_report(tell_id, grade):
    assert ev.tell_evidence(tell_id)["grade"] == grade


def test_evidence_summary_with_and_without_track_record():
    cal = {"available": True, "evaluated_on": "2013–2026",
           "skill": {"h5": {"badge": "no_skill", "stress": {"bss_raw": -0.005, "lo": -0.08, "hi": 0.053, "n": 240}}},
           "coverage": {"h5": {"stress": {"analog_p10_p90": 63.8, "vix_cone_p10_p90": 89.9}}}}
    s = ev.evidence_summary(cal)
    assert s["own_test"] == {"badge": "no_skill", "bss": -0.005, "lo": -0.08, "hi": 0.053, "n": 240,
                             "evaluated_on": "2013–2026", "analog_fan_coverage": 63.8, "vix_cone_coverage": 89.9}
    assert all(i["grade"] != "descriptive" for i in s["items"]) and s["bottom_line"].startswith("After a sharp selloff")
    assert ev.evidence_summary(None)["own_test"] is None
    assert ev.evidence_summary({"available": False})["own_test"] is None
    assert s["report"]["available"] is True


def test_report_ships_with_the_app():
    md = ev.report_markdown()
    assert md and md.startswith("# " + ev.REPORT_TITLE)
    assert ev.REPORT_FILE.parent.name == "research" and ev.REPORT_FILE.parent.parent.name == "app"
    for e in ev.EVIDENCE:                                                 # every cited source is in the report
        for s in e["sources"]:
            assert s["url"] in md, s["url"]


def test_monitor_payload_carries_the_panel():
    res = sm.build_selloff_monitor(_data_dict(), None, None)
    json.dumps(res, allow_nan=False)
    assert res["evidence"]["items"] and "grades" in res["evidence"]
    cal = res["analogs"]["calibration"]
    if cal and cal.get("available") and cal["skill"]["h5"]["stress"]["bss_raw"] is not None:
        assert res["evidence"]["own_test"]["bss"] == cal["skill"]["h5"]["stress"]["bss_raw"]


def test_report_endpoint(monkeypatch):
    from fastapi import HTTPException
    from app.routers import market_router as mr
    out = asyncio.run(mr.selloff_evidence_report(user=SimpleNamespace(id=1)))
    assert out["title"] == ev.REPORT_TITLE and out["markdown"].startswith("# ") and out["words"] > 1000
    monkeypatch.setattr(ev, "report_markdown", lambda: None)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(mr.selloff_evidence_report(user=SimpleNamespace(id=1)))
    assert ei.value.status_code == 404
