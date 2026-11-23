"""Published tasks and class-scoped roster snapshots."""

from datetime import datetime
import json
import math
import re
from urllib.parse import parse_qs


SCHEMA = """
CREATE TABLE IF NOT EXISTS published_tasks (
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, original TEXT NOT NULL, sync_version INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS published_task_groups (
 task_id TEXT NOT NULL REFERENCES published_tasks(id), class_name TEXT NOT NULL, students TEXT NOT NULL,
 PRIMARY KEY(task_id, class_name)
);
CREATE INDEX IF NOT EXISTS published_task_groups_class ON published_task_groups(class_name, task_id);
"""
CAMPUS_NAMES = ("唐岛湾校区", "古镇口校区", "东营科教园区")


class TaskError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def require(condition, message, status=400):
    if not condition:
        raise TaskError(status, message)


def task_id(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value), "任务编号无效。")
    return value


def text(value, length, message, empty=False):
    require(isinstance(value, str) and (empty or value.strip()) and len(value) <= length, message)
    return value.strip()


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value), "任务时间有误。")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as error:
        raise TaskError(400, "任务时间有误。") from error
    return value


def number(value, lower, upper):
    return type(value) in (int, float) and lower <= value <= upper and math.isfinite(value)


def clean_fence(raw):
    require(isinstance(raw, dict) and raw.get("name") in CAMPUS_NAMES, "打卡校区无效。")
    name = raw["name"]
    version = text(raw.get("version"), 80, "围栏版本无效。")
    buffer, accuracy = raw.get("buffer"), raw.get("maxAccuracy")
    require(number(buffer, 0, 2000) and number(accuracy, 1, 1000), "围栏距离或精度无效。")
    geometry = raw.get("geometry")
    require(isinstance(geometry, dict) and geometry.get("type") in ("Polygon", "MultiPolygon"), "围栏必须是多边形。")
    coordinates = geometry.get("coordinates")
    polygons = [coordinates] if geometry["type"] == "Polygon" else coordinates
    require(isinstance(polygons, list) and 1 <= len(polygons) <= 50, "围栏多边形无效。")
    total = 0
    for polygon in polygons:
        require(isinstance(polygon, list) and 1 <= len(polygon) <= 50, "围栏多边形无效。")
        for ring in polygon:
            require(isinstance(ring, list) and len(ring) >= 4 and ring[0] == ring[-1], "围栏环线无效。")
            total += len(ring)
            require(total <= 10000, "围栏坐标过多。")
            for point in ring:
                require(isinstance(point, list) and len(point) == 2 and number(point[0], -180, 180)
                        and number(point[1], -90, 90), "打卡坐标无效。")
            x, y = ring[0]
            area = sum((a[0] - x) * (b[1] - y) - (b[0] - x) * (a[1] - y) for a, b in zip(ring, ring[1:]))
            require(abs(area) > 1e-12, "围栏环线面积无效。")
    return {"name": name, "version": version, "buffer": buffer, "maxAccuracy": accuracy,
            "geometry": {"type": geometry["type"], "coordinates": coordinates}}


def clean_new_task(raw):
    identifier = task_id(raw.get("id"))
    title = text(raw.get("title"), 70, "任务标题无效。")
    description = text(raw.get("description", ""), 2000, "任务说明最多 2000 字。", empty=True)
    opens, due = timestamp(raw.get("opens")), timestamp(raw.get("due"))
    require(opens < due, "截止时间须晚于开放时间。")
    groups = raw.get("groups")
    require(isinstance(groups, list) and 1 <= len(groups) <= 100, "请至少选择一个班级。")
    groups = list(dict.fromkeys(text(value, 50, "班级名称无效。") for value in groups))
    raw_fences = raw.get("fences")
    require(isinstance(raw_fences, list) and 1 <= len(raw_fences) <= 3, "请至少选择一个校区。")
    fences = [clean_fence(fence) for fence in raw_fences]
    require(len({fence["name"] for fence in fences}) == len(fences), "打卡校区重复。")
    return {"id": identifier, "title": title, "description": description, "opens": opens, "due": due,
            "groups": groups, "fences": fences, "revision": 1, "mode": "published"}


def dump(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def read_item(db, row, account):
    task = json.loads(row["payload"])
    role = account["role"]
    snapshots = []
    if role in ("admin", "leader"):
        sql = "SELECT class_name,students FROM published_task_groups WHERE task_id=?"
        args = (task["id"],)
        if role == "leader":
            sql += " AND class_name=?"
            args += (account["class_name"],)
        snapshots = [{"taskId": task["id"], "group": snap["class_name"], "students": json.loads(snap["students"])}
                     for snap in db.execute(sql + " ORDER BY class_name", args)]
    return {"task": task, "snapshots": snapshots}


def feed(db, account, query):
    raw = parse_qs(query).get("since", ["0"])[0]
    require(re.fullmatch(r"[0-9]{1,15}", raw), "同步游标无效。")
    since = int(raw)
    db.execute("BEGIN")
    cursor = db.execute("SELECT COALESCE(MAX(sync_version),0) FROM published_tasks").fetchone()[0]
    sql = "SELECT t.* FROM published_tasks t"
    args = ()
    if account["role"] != "admin":
        sql += " JOIN published_task_groups g ON g.task_id=t.id AND g.class_name=?"
        args = (account["class_name"],)
    rows = db.execute(sql + " WHERE t.sync_version>? ORDER BY t.sync_version", args + (since,)).fetchall()
    items = [read_item(db, row, account) for row in rows]
    return {"cursor": max(cursor, since), "items": items}


def next_version(db):
    return db.execute("SELECT COALESCE(MAX(sync_version),0)+1 FROM published_tasks").fetchone()[0]


def publish(db, admin, raw, now):
    task = clean_new_task(raw)
    payload = dump(task)
    with db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT * FROM published_tasks WHERE id=?", (task["id"],)).fetchone()
        if current:
            require(current["original"] == payload, "该任务编号已使用，请刷新任务列表。", 409)
            return read_item(db, current, admin)
        require(task["due"] > now, "任务截止时间已到。")
        snapshots = []
        for group in task["groups"]:
            students = [{"id": row["student_id"], "name": row["display_name"], "group": group}
                        for row in db.execute("""SELECT student_id,display_name FROM accounts
                        WHERE role='student' AND class_name=? ORDER BY student_id""", (group,))]
            require(students, "班级「" + group + "」没有可发布任务的学生。")
            snapshots.append((task["id"], group, dump(students)))
        db.execute("INSERT INTO published_tasks VALUES (?,?,?,?)", (task["id"], payload, payload, next_version(db)))
        db.executemany("INSERT INTO published_task_groups VALUES (?,?,?)", snapshots)
        row = db.execute("SELECT * FROM published_tasks WHERE id=?", (task["id"],)).fetchone()
        return read_item(db, row, admin)


def update(db, admin, identifier, action, raw, now):
    revision = raw.get("revision")
    require(type(revision) is int and revision > 0, "任务版本无效。")
    due = timestamp(raw.get("due")) if action == "extend" else None
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM published_tasks WHERE id=?", (identifier,)).fetchone()
        require(row, "找不到这项线上任务。", 404)
        task = json.loads(row["payload"])
        if (action == "revoke" and task["mode"] == "revoked") or (action == "extend" and task["due"] == due and task["mode"] == "published"):
            return read_item(db, row, admin)
        require(task["revision"] == revision and task["mode"] == "published", "任务已更新，请刷新后重试。", 409)
        if action == "extend":
            require(due > task["due"] and due > now, "新的截止时间必须晚于原截止时间和当前时间。")
            task["due"] = due
        else:
            task["mode"] = "revoked"
        task["revision"] += 1
        db.execute("UPDATE published_tasks SET payload=?,sync_version=? WHERE id=?",
                   (dump(task), next_version(db), identifier))
        return read_item(db, db.execute("SELECT * FROM published_tasks WHERE id=?", (identifier,)).fetchone(), admin)
