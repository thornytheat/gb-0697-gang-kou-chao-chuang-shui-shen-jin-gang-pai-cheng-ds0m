import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from app.config import DATA_DIR, RULE_VERSION
from app.errors import BusinessError


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "runs.sqlite", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, decision TEXT NOT NULL DEFAULT '待复核', created TEXT NOT NULL)")
    for column, definition in (("depth_key", "TEXT NOT NULL DEFAULT ''"),
                               ("voyage_key", "TEXT NOT NULL DEFAULT ''"),
                               ("superseded_by", "TEXT NOT NULL DEFAULT ''"),
                               ("superseded_reason", "TEXT NOT NULL DEFAULT ''")):
        try:
            db.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError:
            pass
    db.execute("CREATE TABLE IF NOT EXISTS reservations (batch TEXT NOT NULL, ship TEXT NOT NULL, start_s TEXT NOT NULL, end_s TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, PRIMARY KEY(batch, ship))")
    return db


def digest_of(text):
    return hashlib.sha256((RULE_VERSION + "\n" + text).encode()).hexdigest()


def depth_basis(result):
    return list(result.get("plan_meta", {}).get("depth_basis") or [])


def depth_key(result):
    """航道测深基线：任一段的海图水深或有效版本变化，旧方案的富余立即失效。"""
    basis = depth_basis(result)
    payload = "|".join(f"{row.get('id')}:{row.get('depth_m')}@{row.get('version')}" for row in basis)
    return hashlib.sha256(payload.encode()).hexdigest()[:16] if basis else ""


def voyage_key(result):
    """同一批航次（船号 + 申请窗）；方案互为版本。"""
    ships = sorted((str(row.get("id")), str(row.get("start_s")), str(row.get("end_s")))
                   for row in result.get("plan_meta", {}).get("voyages", []))
    payload = "|".join(f"{a}:{b}:{c}" for a, b, c in ships)
    return hashlib.sha256(payload.encode()).hexdigest()[:16] if ships else ""


def _versions(result):
    return {str(row["id"]): int(row["version"]) for row in depth_basis(result)}


def compare_depth(new, old):
    """new 相对 old：newer / older / mixed / same。任一段版本回退即视为旧稿。"""
    if not new or not old:
        return "same"
    newer = older = False
    for ident in set(new) | set(old):
        a, b = new.get(ident), old.get(ident)
        if a is None or b is None:
            newer = older = True
        elif a > b:
            newer = True
        elif a < b:
            older = True
    if newer and older:
        return "mixed"
    if older:
        return "older"
    if newer:
        return "newer"
    return "same"


def _current(db, vkey, exclude):
    if not vkey:
        return []
    return db.execute("SELECT id,depth_key,result FROM runs WHERE voyage_key=? AND id<>? AND superseded_by=''",
                      (vkey, exclude)).fetchall()


def _retire(db, ident, reason, successor):
    db.execute("UPDATE runs SET superseded_by=?, superseded_reason=? WHERE id=?",
               (successor, reason, ident))
    db.execute("UPDATE reservations SET active=0 WHERE batch=?", (ident,))


def save(text, result):
    digest = digest_of(text)
    ident = digest[:16]
    merged = dict(result, rule_version=RULE_VERSION)
    dkey, vkey = depth_key(result), voyage_key(result)
    new_versions = _versions(result)
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(merged, ensure_ascii=False)
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,digest,result,created,depth_key,voyage_key) VALUES(?,?,?,?,?,?)",
                   (ident, digest, payload, now, dkey, vkey))
        others = _current(db, vkey, ident)

        # 提交的是旧测深（版本回退）：这份方案不生效、不占道，也不能挤掉现行方案
        older = any(compare_depth(new_versions, _versions(json.loads(row["result"]))) in {"older", "mixed"}
                    for row in others)
        if older:
            db.execute("UPDATE runs SET superseded_by=?, superseded_reason='DEPTH_STALE' WHERE id=?",
                       (others[0]["id"], ident))
            db.execute("UPDATE reservations SET active=0 WHERE batch=?", (ident,))
            return get(ident)

        # 现行方案：同批航次的旧方案让位，旧富余随之失效
        for row in others:
            reason = "DEPTH_UPDATED" if row["depth_key"] != dkey else "REPLAN"
            _retire(db, row["id"], reason, ident)
        db.execute("UPDATE runs SET superseded_by='', superseded_reason='' WHERE id=?", (ident,))

        # 占道登记按批覆盖：重跑同一批不会重复占用
        db.execute("UPDATE reservations SET active=0 WHERE batch=?", (ident,))
        for row in result.get("assigned", []):
            if row.get("status") != "SCHEDULED":
                continue
            db.execute("INSERT OR REPLACE INTO reservations(batch,ship,start_s,end_s,active,created) VALUES(?,?,?,?,1,?)",
                       (ident, row["id"], str(row["start_s"]), str(row["end_s"]), now))
    return get(ident)


def occupancy(exclude=None):
    """其他仍有效的批次已占用的航道时段（Decimal UTC 秒）。"""
    with connect() as db:
        rows = db.execute("SELECT r.start_s,r.end_s FROM reservations r JOIN runs ON runs.id=r.batch "
                          "WHERE r.active=1 AND runs.superseded_by='' AND r.batch<>?",
                          (exclude or "",)).fetchall()
    return [(Decimal(row["start_s"]), Decimal(row["end_s"])) for row in rows]


def plan(text):
    """同一内容只落一条批次；重跑复用旧批次，不会重复占道。
    计算或写入中断时，旧批次及其占道登记原样保留。"""
    from app.pipeline import run
    ident = digest_of(text)[:16]
    try:
        existing = get(ident)
    except BusinessError:
        existing = None
    if existing:
        # 存档批次（旧测深已被新方案取代）重放时原样返回，不重新占道
        return existing
    return save(text, run(text, occupancy(exclude=ident)))


def get(ident):
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise BusinessError("NOT_FOUND", "批次不存在", 404)
    item = dict(row)
    item["result"] = json.loads(item["result"])
    item["rule_version"] = item["result"].get("rule_version", "2026.11.18.1")
    item["superseded"] = bool(item.get("superseded_by"))
    item["stale"] = item.get("superseded_reason") in {"DEPTH_UPDATED", "DEPTH_STALE"}
    return item


def history():
    with connect() as db:
        rows = db.execute("SELECT id,revision,decision,created,superseded_by,superseded_reason,depth_key,voyage_key "
                          "FROM runs ORDER BY created DESC, id DESC LIMIT 40").fetchall()
    return [dict(row) for row in rows]


def review(ident, revision, decision):
    if not isinstance(decision, str) or decision not in {"通过", "退回"}:
        raise BusinessError("DECISION", "复核决定无效")
    current = get(ident)
    if current["superseded"]:
        raise BusinessError("SUPERSEDED", "该批方案已失效，请用最新批次复核", 409)
    if decision == "通过" and current["result"].get("blocked"):
        raise BusinessError("BLOCKED", "存在阻断项，请先修正数据", 409)
    with connect() as db:
        changed = db.execute("UPDATE runs SET revision=revision+1,decision=? WHERE id=? AND revision=?",
                             (decision, ident, revision)).rowcount
        if not changed:
            raise BusinessError("STALE", "版本已更新，请刷新", 409)
        db.execute("UPDATE reservations SET active=? WHERE batch=?",
                   (0 if decision == "退回" else 1, ident))
    return get(ident)
