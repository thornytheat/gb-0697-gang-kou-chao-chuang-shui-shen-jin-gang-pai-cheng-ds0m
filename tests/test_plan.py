from decimal import Decimal

import pytest

from app import store
from app.pipeline import run
from app.timeops import instant


def csv_text(rows):
    head = "id,kind,start,end,value,aux,version,unit,length_m,limit_kn"
    return "\n".join([head] + rows) + "\n"


def tides(heights, start="2026-11-19T18:00:00+00:00", minutes=30):
    rows = []
    base = instant(start)
    for index, height in enumerate(heights):
        moment = base + Decimal(minutes * 60 * index)
        from app.timeops import display_full
        rows.append(f"T{index:02d},tide,{display_full(moment)}+00:00,,{height},,1,m,,")
    return rows


def scenario(legs=None, ships=None, extra=()):
    heights = ["1.0", "1.1", "1.3", "1.6", "1.9", "2.2", "2.5", "2.7", "2.9", "3.0",
               "3.0", "2.9", "2.7", "2.4", "2.1", "1.8", "1.5", "1.2", "1.0", "0.9",
               "0.8", "0.9", "1.0", "1.2"]
    rows = tides(heights)
    rows += legs or [
        "LEG-OUT,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,11.0,,1,m,11112,14",
        "LEG-IN,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,9.0,,1,m,7408,8",
    ]
    rows += ships or [
        "SHIP-A,ship,2026-11-19T20:00:00+00:00,2026-11-20T09:00:00+00:00,9.0,45,1,m,,",
    ]
    rows += [
        "P1,pilot,2026-11-19T18:00:00+00:00,2026-11-20T10:00:00+00:00,0,,1,m,,",
    ]
    rows += list(extra)
    return csv_text(rows)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    return tmp_path


def test_segments_use_own_minimum_tide():
    result = run(scenario())
    plan = result["plans"][0]
    assert plan["status"] == "SCHEDULED"
    for step in plan["segments"]:
        assert Decimal(step["surplus_m"]) >= 0
        # 段内最低潮位不会高于进段时刻的潮位
        assert Decimal(step["min_tide_m"]) <= Decimal(step["available_m"]) - Decimal(step["chart_depth_m"]) + Decimal("0.0001")
    assert plan["min_surplus_m"] == min(step["surplus_m"] for step in plan["segments"])


def test_outer_leg_faster_than_inner():
    result = run(scenario())
    steps = {step["id"]: step for step in result["plans"][0]["segments"]}
    assert Decimal(steps["LEG-OUT"]["speed_kn"]) > Decimal(steps["LEG-IN"]["speed_kn"])


def test_squat_grows_with_speed():
    from app.plan import squat
    assert squat(Decimal("8")) > squat(Decimal("4"))
    assert squat(Decimal("0")) == 0


def test_greedy_window_front_loads_tide():
    """逐段取最快：外航道快、内航道慢，起航会被推到内航道仍够深的时刻。"""
    result = run(scenario())
    plan = result["plans"][0]
    assert plan["status"] == "SCHEDULED"
    inner = [step for step in plan["segments"] if step["id"] == "LEG-IN"][0]
    assert inner["speed_kn"] == inner["limit_kn"]


def test_naive_entry_only_rejected():
    """落潮段：进段够深、段末不够，只看进段潮位会误放。"""
    heights = ["3.0", "3.0", "2.9", "2.6", "2.2", "1.8", "1.4", "1.1", "0.9", "0.8",
               "0.8", "0.9", "1.0", "1.2", "1.5", "1.8", "2.1", "2.3", "2.5", "2.6",
               "2.7", "2.8", "2.9", "3.0"]
    rows = tides(heights)
    rows += [
        "LEG-OUT,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,11.0,,1,m,11112,14",
        "LEG-IN,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,9.0,,1,m,7408,8",
        "SHIP-A,ship,2026-11-19T18:30:00+00:00,2026-11-20T09:00:00+00:00,9.0,45,1,m,,",
        "P1,pilot,2026-11-19T18:00:00+00:00,2026-11-20T10:00:00+00:00,0,,1,m,,",
    ]
    result = run(csv_text(rows))
    plan = result["plans"][0]
    assert plan["status"] == "SCHEDULED"
    trap = next(alt for alt in plan["alternatives"] if alt["code"] == "ENTRY_ONLY")
    assert trap["segment"] == "LEG-IN"
    assert Decimal(trap["min_surplus_m"]) < 0
    # 采用方案本身逐段富余非负
    assert all(Decimal(step["surplus_m"]) >= 0 for step in plan["segments"])


def test_conservative_route_kept_as_alternative():
    result = run(scenario(legs=[
        "LEG-OUT,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,11.0,,1,m,11112,14",
        "LEG-IN,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,8.6,,1,m,7408,8",
    ]))
    plan = result["plans"][0]
    assert plan["status"] == "SCHEDULED"
    reference = next(alt for alt in plan["alternatives"] if alt["code"] == "GLOBAL_CONSERVATIVE")
    assert reference["feasible"] is False


def test_tide_gap_blocks_extrapolation():
    rows = tides(["3.0", "3.0"])
    text = csv_text(rows + [
        "T02,tide,2026-11-20T06:00:00+00:00,,3.0,,1,m,,",
        "LEG-OUT,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,11.0,,1,m,11112,14",
        "SHIP-A,ship,2026-11-19T20:00:00+00:00,2026-11-20T09:00:00+00:00,9.0,45,1,m,,",
        "P1,pilot,2026-11-19T18:00:00+00:00,2026-11-20T10:00:00+00:00,0,,1,m,,",
    ])
    result = run(text)
    assert result["plans"][0]["status"] != "SCHEDULED"


def test_channel_block_respected():
    result = run(scenario(extra=[
        "BL1,block,2026-11-19T18:00:00+00:00,2026-11-19T20:30:00+00:00,0,,1,m,,",
    ]))
    plan = result["plans"][0]
    if plan["status"] == "SCHEDULED":
        assert Decimal(plan["departure_s"]) >= instant("2026-11-19T20:30:00+00:00")


def test_replan_same_batch_single_reservation(fresh):
    text = scenario()
    first = store.plan(text)
    second = store.plan(text)
    assert first["id"] == second["id"]
    assert len(store.occupancy()) == 1


def test_depth_update_invalidates_old_surplus(fresh):
    text = scenario()
    old = store.plan(text)
    updated = text + "LEG-IN,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,8.8,,2,m,7408,8\n"
    new = store.plan(updated)
    assert new["id"] != old["id"]
    stale = store.get(old["id"])
    assert stale["superseded"] and stale["stale"]
    assert stale["superseded_reason"] == "DEPTH_UPDATED"
    occupancy = store.occupancy()
    assert len(occupancy) == 1


def test_resubmitting_stale_depth_does_not_take_over(fresh):
    text = scenario()
    old = store.plan(text)
    updated = text + "LEG-IN,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,8.8,,2,m,7408,8\n"
    new = store.plan(updated)
    replay = store.plan(text)
    assert replay["id"] == old["id"]
    assert replay["superseded"]
    assert store.get(new["id"])["superseded"] is False


def test_review_rejects_superseded_batch(fresh):
    from app.errors import BusinessError
    text = scenario()
    old = store.plan(text)
    store.plan(text + "LEG-IN,leg,2026-11-19T18:00:00+00:00,2026-11-20T13:00:00+00:00,8.8,,2,m,7408,8\n")
    with pytest.raises(BusinessError) as caught:
        store.review(old["id"], old["revision"], "通过")
    assert caught.value.status == 409
