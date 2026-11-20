from app.modules.m4_channel.rules import subtract
from app.report import report
from app import plan as segment_plan


def first_fit(windows,pilots,duration,occupied):
    candidates=[]
    for a,b in windows:
        for p,q in pilots:
            lo,hi=max(a,p),min(b,q)
            if lo<hi:
                for x,y in subtract((lo,hi),occupied):
                    if y-x>=duration:candidates.append((x,x+duration))
    return min(candidates) if candidates else None


def _legacy(ctx):
    pilots=[(r["a"],r["b"]) for r in ctx["effective"] if r["kind"]=="pilot"]
    ships=sorted((r for r in ctx["effective"] if r["kind"]=="ship"),key=lambda r:(r["a"],r["id"]))
    assigned=[];occupied=[]
    for ship in ships:
        windows=[(w["start_s"],w["end_s"]) for w in ctx["channel_windows"] if w["id"]==ship["id"]]
        hit=first_fit(windows,pilots,ship["duration"],occupied)
        if hit:occupied.append(hit);assigned.append({"id":ship["id"],"start_s":hit[0],"end_s":hit[1],"status":"SCHEDULED","line":ship["line"]})
        else:assigned.append({"id":ship["id"],"start_s":None,"end_s":None,"status":"NO_SAFE_SLOT","line":ship["line"]})
    ctx["assigned"]=assigned;ctx["plans"]=[]
    return report(assigned,已排=sum(r["status"]=="SCHEDULED" for r in assigned),待排=sum(r["status"]!="SCHEDULED" for r in assigned))


def apply(ctx):
    legs=[r for r in ctx["effective"] if r["kind"]=="leg"]
    if not legs:
        return _legacy(ctx)
    ships=[{"id":r["id"],"draft":r["depth"],"start_s":r["a"],"end_s":r["b"],
            "duration":r["duration"],"line":r["line"]}
           for r in ctx["effective"] if r["kind"]=="ship"]
    pilots=[(r["a"],r["b"]) for r in ctx["effective"] if r["kind"]=="pilot"]
    blocks=[(r["a"],r["b"]) for r in ctx["effective"] if r["kind"]=="block"]
    points=[(r["a"],r["depth"]) for r in ctx["effective"] if r["kind"]=="tide"]
    series=segment_plan.TideSeries(points,ctx.get("tide_conflict_times",[]))
    leg_rows=[{"id":r["id"],"seq":i+1,"depth_m":r["depth"],"length_m":r["length_m"],
               "limit_kn":r["limit_kn"],"open_s":r["a"],"close_s":r["b"],
               "version":r["version_value"],"line":r["line"]}
              for i,r in enumerate(sorted(legs,key=lambda r:(r["line"],r["id"])))]
    plans,assigned=segment_plan.build(ships,leg_rows,series,pilots,blocks,ctx.get("occupancy",()))
    ctx["assigned"]=assigned;ctx["plans"]=plans;ctx["legs"]=leg_rows;ctx["tide_series"]=series
    ctx["plan_meta"]={"depth_basis":[{"id":leg["id"],"depth_m":leg["depth_m"],"version":leg["version"]} for leg in leg_rows],
                      "voyages":[{"id":ship["id"],"start_s":str(ship["start_s"]),"end_s":str(ship["end_s"])} for ship in ships],
                      "occupied":[[str(a),str(b)] for a,b in ctx.get("occupancy",())]}
    rows=[]
    for plan in plans:
        for step in plan["segments"]:
            rows.append({"id":plan["id"],"segment":step["id"],"order":step["seq"],
                         "start_s":step["enter_s"],"end_s":step["exit_s"],
                         "speed_kn":step["speed_kn"],"limit_kn":step["limit_kn"],
                         "length_m":step["length_m"],"depth_m":step["chart_depth_m"],
                         "depth_version":step["depth_version"],
                         "squat_m":step["squat_m"],"required_m":step["required_m"],
                         "min_tide_m":step["min_tide_m"],
                         "available_m":step["available_m"],"surplus_m":step["surplus_m"]})
    return report(rows,已排=sum(r["status"]=="SCHEDULED" for r in assigned),
                  待排=sum(r["status"]!="SCHEDULED" for r in assigned),
                  航道段=len(leg_rows),未采用方案=sum(len(p["alternatives"]) for p in plans))
