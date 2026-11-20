"""分段进港方案引擎。

一艘船依次通过若干净空航段（先外航道、后内航道）。每段有自己的海图水深、
长度与限速；船体下沉随航速平方增长，所以外航道限速高不等于可以全速通过。

核算口径
--------
* 潮位按时间线性插值。相邻有效潮位超过 3 小时视为缺口，缺口内不推算；
  同一时刻的冲突潮位是插值断点，任何跨过该时刻的推算都不成立。
* 每一段只看船真正位于这一段的那段时间里的*最低*潮位。既不是整程用最浅段 +
  最大下沉量一刀切，也不是只看进段那一刻的潮位。
* 需求水深 = 吃水 + 下沉 + 安全余量(1m)；可用水深 = 该段海图水深 + 该段最低潮位。
  富余 = 可用 - 需求，段内任意时刻不得为负。报告里的“实际富余”就是该段最紧时刻的值。
* 每段在限速内取仍满足富余 >= 0 的最大航速；起航时刻取申请窗、引航窗、封航、
  测深有效期与已占道约束下的最早可行时刻。

被否决的候选（整程保守口径、只看进段潮位、更早起航）全部留档，供工作台与导出核对。
"""
from bisect import bisect_left, bisect_right
from decimal import Decimal

from app.config import (MIN_SPEED_KN, PLAN_COARSE_S, PLAN_STEP_S, SPEED_STEP_KN,
                        SQUAT_COEF, TIDE_MAX_GAP_S, UNDER_KEEL_M)
from app.modules.m3_windows.rules import safe_segment

EPS = Decimal("0.0001")
SECONDS_PER_HOUR = Decimal(3600)


def squat(speed_kn):
    return SQUAT_COEF * Decimal(speed_kn) * Decimal(speed_kn)


def speeds_within(limit_kn):
    """限速内从高到低的候选航速（0.5 节步长，最低 3 节）。"""
    limit = Decimal(limit_kn)
    floor = min(MIN_SPEED_KN, limit)
    out, speed = [], limit
    while speed > floor:
        out.append(speed)
        speed -= SPEED_STEP_KN
    out.append(max(floor, Decimal(0)))
    unique = []
    for value in out:
        if value > 0 and value not in unique:
            unique.append(value)
    return unique


def transit_seconds(length_m, speed_kn):
    return Decimal(length_m) / (Decimal(speed_kn) * Decimal("0.514444"))


class TideSeries:
    """UTC 秒为键的潮位折线，带缺口/冲突断点保护与区间最小值查询。"""

    def __init__(self, points, breaks=()):
        merged, conflicts = {}, set()
        for time, height in points:
            time, height = Decimal(time), Decimal(height)
            if time in merged and merged[time] != height:
                conflicts.add(time)
            merged[time] = height
        self.times = sorted(merged)
        self.heights = [merged[t] for t in self.times]
        self.breaks = sorted({Decimal(b) for b in breaks} | conflicts)
        count = len(self.times)
        self.invalid = [False] * max(count - 1, 0)
        for index in range(count - 1):
            if self.times[index + 1] - self.times[index] > TIDE_MAX_GAP_S:
                self.invalid[index] = True
        for point in self.breaks:
            index = bisect_left(self.times, point)
            if index < count and self.times[index] == point:
                if index > 0:
                    self.invalid[index - 1] = True
                if index < count - 1:
                    self.invalid[index] = True
            elif 0 < index < count and self.times[index - 1] < point < self.times[index]:
                self.invalid[index - 1] = True
        prefix = [0]
        for flag in self.invalid:
            prefix.append(prefix[-1] + (1 if flag else 0))
        self._invalid_prefix = prefix
        self._levels = self._build_table()

    def _build_table(self):
        count = len(self.heights)
        if not count:
            return []
        log = [0] * (count + 1)
        for value in range(2, count + 1):
            log[value] = log[value // 2] + 1
        levels = [list(range(count))]
        step = 1
        while step * 2 <= count:
            previous = levels[-1]
            row = []
            for start in range(count - step * 2 + 1):
                left, right = previous[start], previous[start + step]
                row.append(left if self.heights[left] <= self.heights[right] else right)
            levels.append(row)
            step *= 2
        return levels

    def __bool__(self):
        return bool(self.times)

    @property
    def first(self):
        return self.times[0] if self.times else None

    @property
    def last(self):
        return self.times[-1] if self.times else None

    def covers(self, moment):
        return bool(self.times) and self.times[0] <= Decimal(moment) <= self.times[-1]

    def _index(self, moment):
        return min(max(bisect_right(self.times, moment) - 1, 0), len(self.times) - 2)

    def _any_invalid(self, left, right):
        return right >= left and self._invalid_prefix[right + 1] - self._invalid_prefix[left] > 0

    def _argmin(self, left, right):
        length = right - left + 1
        levels = self._levels
        level = length.bit_length() - 1
        row = levels[level]
        first = row[left]
        second = row[right - (1 << level) + 1]
        return first if self.heights[first] <= self.heights[second] else second

    def height_at(self, moment):
        moment = Decimal(moment)
        if not self.covers(moment):
            return None
        index = bisect_left(self.times, moment)
        if index < len(self.times) and self.times[index] == moment:
            if moment in self.breaks:
                return None
            return self.heights[index]
        left = index - 1
        if self.invalid[left]:
            return None
        t0, t1 = self.times[left], self.times[index]
        h0, h1 = self.heights[left], self.heights[index]
        return h0 + (h1 - h0) * (moment - t0) / (t1 - t0)

    def min_over(self, start, end):
        """[start, end] 内的最低潮位；任一点无法推算则返回 None。"""
        start, end = Decimal(start), Decimal(end)
        if end < start:
            return None
        low, high = self.height_at(start), self.height_at(end)
        if low is None or high is None:
            return None
        first, last = self._index(start), self._index(end)
        if self._any_invalid(first, last):
            return None
        best, at = low, start
        if high < best:
            best, at = high, end
        left = bisect_left(self.times, start)
        right = bisect_right(self.times, end) - 1
        if left <= right:
            index = self._argmin(left, right)
            if self.heights[index] < best:
                best, at = self.heights[index], self.times[index]
        return {"height": best, "at": at}

    def windows_above(self, threshold):
        """连续高于阈值的区间；缺口与冲突断点处截断。"""
        threshold = Decimal(threshold)
        out = []
        for index in range(len(self.times) - 1):
            if self.invalid[index]:
                continue
            hit = safe_segment(self.times[index], self.times[index + 1],
                               self.heights[index], self.heights[index + 1], threshold)
            if not hit or hit[0] >= hit[1]:
                continue
            if out and out[-1][1] == hit[0]:
                out[-1] = (out[-1][0], hit[1])
            else:
                out.append(hit)
        return out


def overlaps(start, end, intervals):
    return any(Decimal(x) < Decimal(end) and Decimal(start) < Decimal(y) for x, y in intervals)


def within_pilot(pilots, start, end):
    return any(Decimal(p) <= Decimal(start) and Decimal(end) <= Decimal(q) for p, q in pilots)


def need_tide(leg, ship, speed_kn):
    """该段在该航速下所需的潮高。"""
    return (Decimal(ship["draft"]) + squat(speed_kn) + UNDER_KEEL_M
            - Decimal(leg["depth_m"]))


def run_leg(leg, ship, series, enter):
    """进入一段后取限速内最大可行航速。返回 (step, failure)。"""
    enter = Decimal(enter)
    speeds = leg.get("_speeds") or speeds_within(leg["limit_kn"])
    leg["_speeds"] = speeds
    attempts = []
    for speed in speeds:
        exit_at = enter + transit_seconds(leg["length_m"], speed)
        if enter < Decimal(leg["open_s"]) or exit_at > Decimal(leg["close_s"]):
            attempts.append({"speed": speed, "code": "LEG_WINDOW", "exit": exit_at})
            continue
        tide = series.min_over(enter, exit_at)
        if tide is None:
            attempts.append({"speed": speed, "code": "TIDE_UNKNOWN", "exit": exit_at})
            continue
        available = Decimal(leg["depth_m"]) + tide["height"]
        required = Decimal(ship["draft"]) + squat(speed) + UNDER_KEEL_M
        surplus = available - required
        if surplus >= -EPS:
            return {
                "id": leg["id"],
                "seq": leg["seq"],
                "limit_kn": leg["limit_kn"],
                "speed_kn": speed,
                "length_m": leg["length_m"],
                "chart_depth_m": leg["depth_m"],
                "depth_version": leg.get("version"),
                "enter_s": enter,
                "exit_s": exit_at,
                "minutes": (exit_at - enter) / Decimal(60),
                "squat_m": squat(speed),
                "under_keel_m": UNDER_KEEL_M,
                "required_m": required,
                "min_tide_m": tide["height"],
                "min_tide_at_s": tide["at"],
                "available_m": available,
                "surplus_m": surplus,
            }, None
        attempts.append({"speed": speed, "code": "DEPTH_SHORT", "exit": exit_at,
                         "available": available, "required": required, "surplus": surplus})
    last = attempts[-1] if attempts else {}
    return None, {
        "code": last.get("code", "TIDE_UNKNOWN"),
        "segment": leg["id"],
        "enter_s": enter,
        "speed_kn": last.get("speed"),
        "exit_s": last.get("exit"),
        "available_m": last.get("available"),
        "required_m": last.get("required"),
        "surplus_m": last.get("surplus"),
    }


def simulate(ship, legs, series, pilots, blocks, occupied, departure):
    """按分段规则推演一次完整进港。"""
    departure = Decimal(departure)
    moment, steps = departure, []
    for leg in legs:
        step, failure = run_leg(leg, ship, series, moment)
        if failure:
            failure["departure_s"] = departure
            failure["reached_s"] = moment
            return None, failure
        steps.append(step)
        moment = step["exit_s"]
    arrival = moment
    if arrival > Decimal(ship["end_s"]):
        return None, {"code": "ARRIVAL_DEADLINE", "departure_s": departure,
                      "arrival_s": arrival, "deadline_s": ship["end_s"]}
    if not within_pilot(pilots, departure, arrival):
        return None, {"code": "PILOT_WINDOW", "departure_s": departure,
                      "arrival_s": arrival}
    if overlaps(departure, arrival, blocks):
        return None, {"code": "CHANNEL_BLOCKED", "departure_s": departure,
                      "arrival_s": arrival}
    if overlaps(departure, arrival, occupied):
        return None, {"code": "CHANNEL_OCCUPIED", "departure_s": departure,
                      "arrival_s": arrival}
    return {"departure_s": departure, "arrival_s": arrival, "steps": steps}, None


def _grid(low, high, step):
    value = Decimal(low)
    while value <= Decimal(high):
        yield value
        value += Decimal(step)


def entry_only(ship, legs, series, departure):
    """朴素做法：每段只用“进段那一刻”的潮位选速，段内不再复核。"""
    moment = Decimal(departure)
    steps = []
    for leg in legs:
        chosen = None
        for speed in speeds_within(leg["limit_kn"]):
            tide = series.height_at(moment)
            if tide is None:
                continue
            if Decimal(leg["depth_m"]) + tide - (Decimal(ship["draft"])
                                                 + squat(speed) + UNDER_KEEL_M) >= -EPS:
                exit_at = moment + transit_seconds(leg["length_m"], speed)
                chosen = {"id": leg["id"], "speed_kn": speed, "enter_s": moment,
                          "exit_s": exit_at, "entry_tide_m": tide}
                break
        if chosen is None:
            return None
        steps.append(chosen)
        moment = chosen["exit_s"]
    if moment > Decimal(ship["end_s"]):
        return None
    return {"departure_s": Decimal(departure), "arrival_s": moment, "steps": steps}


def measure(ship, legs, series, departure, speeds):
    """给定每段航速，核算各段段内最低富余；用于复核未采用方案。"""
    moment = Decimal(departure)
    rows = []
    for leg, speed in zip(legs, speeds):
        exit_at = moment + transit_seconds(leg["length_m"], speed)
        tide = series.min_over(moment, exit_at)
        base = {"id": leg["id"], "speed_kn": speed, "enter_s": moment, "exit_s": exit_at}
        if tide is None:
            rows.append(dict(base, surplus_m=None, min_tide_m=None))
        else:
            available = Decimal(leg["depth_m"]) + tide["height"]
            required = Decimal(ship["draft"]) + squat(speed) + UNDER_KEEL_M
            rows.append(dict(base, surplus_m=available - required,
                             min_tide_m=tide["height"], min_tide_at_s=tide["at"]))
        moment = exit_at
    known = [row for row in rows if row["surplus_m"] is not None]
    worst = min(known, key=lambda row: row["surplus_m"]) if known else rows[0]
    return rows, worst, moment


def conservative_reference(ship, legs, series, pilots, blocks, occupied, low, high):
    """整程按最浅段 + 限速内最大下沉量核算；作为未采用方案留档。"""
    min_depth = min(Decimal(leg["depth_m"]) for leg in legs)
    max_squat = max(squat(leg["limit_kn"]) for leg in legs)
    threshold = Decimal(ship["draft"]) + UNDER_KEEL_M + max_squat - min_depth
    total = sum(Decimal(leg["length_m"]) for leg in legs)
    slowest = min(Decimal(leg["limit_kn"]) for leg in legs)
    transit = transit_seconds(total, slowest)
    reference = {
        "min_depth_m": min_depth,
        "max_squat_m": max_squat,
        "required_tide_m": threshold,
        "reference_speed_kn": slowest,
        "transit_minutes": transit / Decimal(60),
        "max_tide_m": max(series.heights) if series.heights else None,
    }
    earliest = None
    for window_start, window_end in series.windows_above(threshold):
        start = max(Decimal(window_start), Decimal(low))
        end = Decimal(window_end)
        for departure in _grid(start, end - transit, PLAN_COARSE_S):
            arrival = departure + transit
            if arrival > Decimal(ship["end_s"]) or arrival > Decimal(high):
                break
            if not within_pilot(pilots, departure, arrival):
                continue
            if overlaps(departure, arrival, blocks) or overlaps(departure, arrival, occupied):
                continue
            earliest = (departure, arrival, window_start, window_end)
            break
        if earliest:
            break
    if earliest is None:
        reference["feasible"] = False
        reference["note"] = "整程按最浅段与最大下沉量核算，没有能容下整程的潮窗，会错过实际可用窗口"
        return reference
    reference.update({
        "feasible": True,
        "departure_s": earliest[0],
        "arrival_s": earliest[1],
        "window_s": [earliest[2], earliest[3]],
    })
    reference["note"] = "整程一刀切虽有窗，但起航更晚、富余更小"
    return reference


def _earlier_note(failure):
    code = failure.get("code")
    if code == "CHANNEL_OCCUPIED":
        return "再提前一分钟会与已排航次在航道重叠"
    if code == "CHANNEL_BLOCKED":
        return "再提前一分钟会撞上封航时段"
    if code == "PILOT_WINDOW":
        return "再提前一分钟引航窗口覆盖不住整程"
    if code == "ARRIVAL_DEADLINE":
        return "再提前一分钟抵达仍晚于申请窗，起航可再压缩"
    return "再提前一分钟，该段段内最低点余量不足"


def _better_later(ship, legs, series, pilots, blocks, occupied, chosen, low, high):
    """更晚起航若能让每段都按限速通过，作为未采用方案留档。"""
    moment = chosen["departure_s"] + PLAN_STEP_S
    best = None
    while moment <= high:
        trial, _ = simulate(ship, legs, series, pilots, blocks, occupied, moment)
        if trial:
            speeds = [step["speed_kn"] for step in trial["steps"]]
            at_limit = all(step["speed_kn"] == step["limit_kn"] for step in trial["steps"])
            worst = min(step["surplus_m"] for step in trial["steps"])
            if at_limit:
                record = {
                    "code": "LATER_DEPARTURE",
                    "departure_s": moment,
                    "arrival_s": trial["arrival_s"],
                    "min_surplus_m": worst,
                    "note": "再晚一些每段都能按限速通过，但规则采用最早可行窗，此方案不采用",
                }
                if best is None or worst > best["min_surplus_m"]:
                    best = record
                break
        moment += PLAN_STEP_S
    return best


def plan_ship(ship, legs, series, pilots, blocks, occupied):
    """为一个航次给出最早可行分段方案，并附未采用方案。"""
    plan = {
        "id": ship["id"],
        "status": "NO_SAFE_SLOT",
        "departure_s": None,
        "arrival_s": None,
        "total_minutes": None,
        "min_surplus_m": None,
        "limiting_segment": None,
        "depth_basis": [
            {"id": leg["id"], "seq": leg["seq"], "depth_m": leg["depth_m"],
             "version": leg.get("version")}
            for leg in legs
        ],
        "segments": [],
        "alternatives": [],
        "reason": None,
    }
    if not series:
        plan["reason"] = "NO_TIDE_DATA"
        plan["status"] = "NO_TIDE_DATA"
        return plan
    fastest = sum(transit_seconds(leg["length_m"], max(speeds_within(leg["limit_kn"])))
                  for leg in legs)
    low = max(Decimal(ship["start_s"]), series.first,
              min(Decimal(leg["open_s"]) for leg in legs))
    high = min(Decimal(ship["end_s"]), series.last,
               min(Decimal(leg["close_s"]) for leg in legs) - fastest)
    if low > high:
        plan["reason"] = "OUTSIDE_TIDE_COVERAGE"
        plan["status"] = "OUTSIDE_TIDE_COVERAGE"
        return plan

    chosen, failure_seen = None, None
    coarse = list(_grid(low, high, PLAN_COARSE_S))
    for departure in coarse:
        trial, failure = simulate(ship, legs, series, pilots, blocks, occupied, departure)
        if trial:
            for refined in _grid(max(low, departure - PLAN_COARSE_S), departure, PLAN_STEP_S):
                refined_trial, _ = simulate(ship, legs, series, pilots, blocks, occupied, refined)
                if refined_trial:
                    chosen = refined_trial
                    break
            chosen = chosen or trial
            break
        failure_seen = failure_seen or failure

    reference = conservative_reference(ship, legs, series, pilots, blocks, occupied, low, high)
    plan["alternatives"].append({"code": "GLOBAL_CONSERVATIVE", **reference})

    if chosen is None:
        plan["reason"] = (failure_seen or {}).get("code", "NO_SAFE_SLOT")
        plan["failure"] = failure_seen
        return plan

    steps = chosen["steps"]
    worst = min(steps, key=lambda step: step["surplus_m"])
    plan.update({
        "status": "SCHEDULED",
        "departure_s": chosen["departure_s"],
        "arrival_s": chosen["arrival_s"],
        "total_minutes": (chosen["arrival_s"] - chosen["departure_s"]) / Decimal(60),
        "segments": steps,
        "min_surplus_m": worst["surplus_m"],
        "limiting_segment": worst["id"],
    })

    earlier = chosen["departure_s"] - PLAN_STEP_S
    if earlier >= low:
        _, failure = simulate(ship, legs, series, pilots, blocks, occupied, earlier)
        if failure:
            plan["alternatives"].append({
                "code": "EARLIER_DEPARTURE",
                "departure_s": earlier,
                "segment": failure.get("segment"),
                "surplus_m": failure.get("surplus_m"),
                "failure": failure.get("code"),
                "note": _earlier_note(failure),
            })

    later = _better_later(ship, legs, series, pilots, blocks, occupied,
                          chosen, low, high)
    if later:
        plan["alternatives"].append(later)

    trap = None
    for departure in _grid(low, chosen["departure_s"] + Decimal(7200), Decimal(300)):
        naive = entry_only(ship, legs, series, departure)
        if naive is None:
            continue
        rows, worst_row, arrival = measure(ship, legs, series, naive["departure_s"],
                                           [step["speed_kn"] for step in naive["steps"]])
        if arrival > Decimal(ship["end_s"]):
            verdict = "arrival"
        elif worst_row["surplus_m"] is not None and worst_row["surplus_m"] < -EPS:
            verdict = "short"
        else:
            verdict = "ok"
        if verdict in {"short", "arrival"}:
            trap = {
                "code": "ENTRY_ONLY",
                "departure_s": naive["departure_s"],
                "arrival_s": arrival,
                "segments": rows,
                "min_surplus_m": worst_row["surplus_m"],
                "segment": worst_row["id"],
                "note": ("只看进段潮位会在该时刻放行，但段内最低点余量为负"
                         if verdict == "short" else "只看进段潮位会放行，但抵达晚于申请窗"),
            }
            break
    if trap:
        plan["alternatives"].append(trap)

    return plan


def build(ships, legs, series, pilots, blocks, existing=()):
    plans, assigned, occupied = [], [], [(Decimal(a), Decimal(b)) for a, b in existing]
    ordered = sorted(legs, key=lambda leg: (leg["seq"], leg["id"]))
    for ship in sorted(ships, key=lambda row: (row["start_s"], row["id"])):
        plan = plan_ship(ship, ordered, series, pilots, blocks, occupied)
        plans.append(plan)
        if plan["status"] == "SCHEDULED":
            occupied.append((plan["departure_s"], plan["arrival_s"]))
            assigned.append({"id": ship["id"], "start_s": plan["departure_s"],
                             "end_s": plan["arrival_s"], "status": "SCHEDULED",
                             "line": ship["line"]})
        else:
            assigned.append({"id": ship["id"], "start_s": None, "end_s": None,
                             "status": "NO_SAFE_SLOT", "line": ship["line"],
                             "reason": plan["reason"]})
    return plans, assigned
