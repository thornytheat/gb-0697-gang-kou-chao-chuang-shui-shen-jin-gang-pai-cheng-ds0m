import csv, io, json
from app.timeops import display_full

HEADER = ["run_id", "sha256", "rule_version", "record", "vessel", "segment", "departure_utc",
          "arrival_utc", "enter_utc", "exit_utc", "speed_kn", "limit_kn", "chart_depth_m",
          "tide_min_m", "required_m", "surplus_m", "status", "line", "detail"]


def _row(run, record, **values):
    base = {name: "" for name in HEADER}
    base.update(run_id=run["id"], sha256=run["digest"], rule_version=run["rule_version"],
                record=record)
    base.update({k: v for k, v in values.items() if v is not None})
    return [base[name] for name in HEADER]


def _at(value):
    return display_full(value) if value not in (None, "") else ""


def evidence_csv(run):
    result = run["result"]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADER)
    manifest = {
        "balance": result.get("balance"),
        "decision": run["decision"],
        "revision": run["revision"],
        "blocked": result.get("blocked"),
        "superseded": run.get("superseded", False),
        "superseded_reason": run.get("superseded_reason", ""),
        "depth_basis": result.get("plan_meta", {}).get("depth_basis", []),
        "occupied": result.get("plan_meta", {}).get("occupied", []),
    }
    writer.writerow(_row(run, "manifest", detail=json.dumps(manifest, ensure_ascii=False)))

    for plan in result.get("plans", []):
        summary = {
            "status": plan["status"],
            "reason": plan.get("reason"),
            "min_surplus_m": plan.get("min_surplus_m"),
            "limiting_segment": plan.get("limiting_segment"),
            "total_minutes": plan.get("total_minutes"),
        }
        writer.writerow(_row(run, "voyage", vessel=plan["id"],
                             departure_utc=_at(plan.get("departure_s")),
                             arrival_utc=_at(plan.get("arrival_s")),
                             status=plan["status"],
                             detail=json.dumps(summary, ensure_ascii=False)))
        for step in plan.get("segments", []):
            writer.writerow(_row(run, "segment", vessel=plan["id"], segment=step["id"],
                                 enter_utc=_at(step["enter_s"]), exit_utc=_at(step["exit_s"]),
                                 speed_kn=step["speed_kn"], limit_kn=step["limit_kn"],
                                 chart_depth_m=step["chart_depth_m"],
                                 tide_min_m=step["min_tide_m"], required_m=step["required_m"],
                                 surplus_m=step["surplus_m"], status="ADOPTED",
                                 detail=json.dumps({"squat_m": step["squat_m"],
                                                    "under_keel_m": step["under_keel_m"],
                                                    "depth_version": step["depth_version"],
                                                    "min_tide_at": _at(step["min_tide_at_s"])},
                                                   ensure_ascii=False)))
        for alt in plan.get("alternatives", []):
            writer.writerow(_row(run, "alternative", vessel=plan["id"],
                                 segment=alt.get("segment"), departure_utc=_at(alt.get("departure_s")),
                                 arrival_utc=_at(alt.get("arrival_s")),
                                 tide_min_m=alt.get("min_tide_m"),
                                 surplus_m=alt.get("min_surplus_m") if alt.get("min_surplus_m") is not None else alt.get("surplus_m"),
                                 status=alt["code"], detail=json.dumps(alt, ensure_ascii=False)))

    for row in result.get("final", []):
        writer.writerow(_row(run, "result", vessel=row.get("id"), status=row.get("status"),
                             line=row.get("line"),
                             detail=json.dumps(row, ensure_ascii=False)))
    for row in result.get("quarantine", []):
        if row.get("line"):
            writer.writerow(_row(run, "quarantine", line=row.get("line"),
                                 status=row.get("reason"), detail=json.dumps(row, ensure_ascii=False)))
    return "﻿" + out.getvalue()
