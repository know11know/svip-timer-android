from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
AUTHORIZED_ENDPOINT = os.getenv("AUTHORIZED_CLAIM_ENDPOINT", "")
DB_PATH = Path(os.getenv("ANALYTICS_DB", str(ROOT / "data" / "analytics.sqlite3")))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
ALLOWED_EVENTS = {
    "first_launch", "app_open", "verify_success", "verify_failure",
    "task_started", "claim_success", "claim_finished", "network_error"
}

jobs: dict[str, dict] = {}
lock = threading.Lock()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            install_id TEXT NOT NULL,
            event TEXT NOT NULL,
            app_version TEXT NOT NULL DEFAULT '',
            android_version TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL DEFAULT 'direct',
            outcome TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            install_id TEXT NOT NULL,
            app_version TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL DEFAULT 'direct',
            created_at TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_events_install ON events(install_id)")


def analytics_summary() -> dict:
    today = datetime.now().astimezone().date().isoformat()
    month_ago = (datetime.now().astimezone() - timedelta(days=30)).isoformat()
    with sqlite3.connect(DB_PATH) as db:
        installs = db.execute("SELECT COUNT(DISTINCT install_id) FROM events WHERE event='first_launch'").fetchone()[0]
        dau = db.execute("SELECT COUNT(DISTINCT install_id) FROM events WHERE substr(created_at,1,10)=?", (today,)).fetchone()[0]
        mau = db.execute("SELECT COUNT(DISTINCT install_id) FROM events WHERE created_at>=?", (month_ago,)).fetchone()[0]
        successes = db.execute("SELECT COUNT(*) FROM events WHERE event='claim_success'").fetchone()[0]
        feedback_count = db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        downloads = db.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
        channels = dict(db.execute("SELECT channel,COUNT(*) FROM downloads GROUP BY channel ORDER BY 2 DESC").fetchall())
        versions = dict(db.execute("SELECT app_version,COUNT(DISTINCT install_id) FROM events WHERE app_version<>'' GROUP BY app_version ORDER BY 2 DESC").fetchall())
        events = dict(db.execute("SELECT event,COUNT(*) FROM events GROUP BY event ORDER BY 2 DESC").fetchall())
    return {"downloads": downloads, "installs": installs, "dau": dau, "mau30": mau,
            "claimSuccesses": successes, "feedbackCount": feedback_count,
            "channels": channels, "versions": versions, "events": events, "updatedAt": now_iso()}


def valid_install_id(value: object) -> str:
    install_id = str(value or "")
    if not re.fullmatch(r"[a-f0-9-]{32,36}", install_id):
        raise ValueError("invalid install_id")
    return install_id


init_db()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def log(job: dict, message: str) -> None:
    job["logs"].append(f"[{now_iso()}] {message}")
    job["logs"] = job["logs"][-200:]


def next_target(hms: str) -> datetime:
    parsed = datetime.strptime(hms, "%H:%M:%S.%f").time()
    now = datetime.now().astimezone()
    target = now.replace(hour=parsed.hour, minute=parsed.minute, second=parsed.second,
                         microsecond=parsed.microsecond)
    if target <= now:
        target += timedelta(days=1)
    return target


def send_once(job: dict) -> tuple[int, str]:
    if not AUTHORIZED_ENDPOINT:
        return 200, json.dumps({"code": 0, "message": "演示模式：未配置授权接口"}, ensure_ascii=False)
    body = json.dumps({"couponType": job["couponType"]}).encode()
    req = request.Request(AUTHORIZED_ENDPOINT, data=body, method="POST",
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=1.0) as resp:
        return resp.status, resp.read(4096).decode("utf-8", "replace")


def run_job(job_id: str) -> None:
    with lock:
        job = jobs[job_id]
        job["status"] = "waiting"
        target = next_target(job["targetTime"])
        job["targetAt"] = target.isoformat(timespec="milliseconds")
        log(job, f"任务已创建，目标时间 {job['targetAt']}")
    while True:
        with lock:
            if job["cancelled"]:
                job["status"] = "cancelled"
                log(job, "任务已停止")
                return
        if datetime.now().astimezone() >= target:
            break
        time.sleep(min(0.2, max(0.01, (target - datetime.now().astimezone()).total_seconds())))

    with lock:
        job["status"] = "running"
        log(job, "到达目标时间，开始执行")
    attempts = max(1, min(int(job["attempts"]), 5))
    interval = max(1.0, float(job["intervalSeconds"]))
    for index in range(attempts):
        with lock:
            if job["cancelled"]:
                job["status"] = "cancelled"
                log(job, "任务已停止")
                return
        started = time.perf_counter()
        try:
            status, result = send_once(job)
            latency = (time.perf_counter() - started) * 1000
            with lock:
                log(job, f"第 {index + 1} 次：HTTP {status}，耗时 {latency:.1f}ms，结果 {result}")
            if status == 200:
                with lock:
                    job["status"] = "success"
                    log(job, "任务完成")
                return
        except Exception as exc:
            with lock:
                log(job, f"第 {index + 1} 次失败：{type(exc).__name__}: {exc}")
        if index + 1 < attempts:
            time.sleep(interval)
    with lock:
        job["status"] = "failed"
        log(job, "达到最大尝试次数，任务结束")


class Handler(BaseHTTPRequestHandler):
    server_version = "CouponTimer/1.0"

    def send_json(self, status: int, data: object) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            raise ValueError("请求体过大")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self.send_json(200, {"ok": True, "mode": "authorized" if AUTHORIZED_ENDPOINT else "demo"})
        if parsed.path == "/api/analytics/summary":
            return self.send_json(200, analytics_summary())
        if parsed.path == "/download":
            apk = STATIC / "SVIP-timer-v2.4.3-analytics.apk"
            if not apk.is_file():
                return self.send_json(404, {"error": "APK not found"})
            channel = str(parse_qs(parsed.query).get("from", ["direct"])[0])[:32]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", channel):
                channel = "direct"
            with sqlite3.connect(DB_PATH) as db:
                db.execute("INSERT INTO downloads(channel,created_at) VALUES(?,?)", (channel, now_iso()))
            raw = apk.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Disposition", f'attachment; filename="{apk.name}"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/api/analytics/feedback":
            if not ADMIN_TOKEN or self.headers.get("Authorization") != f"Bearer {ADMIN_TOKEN}":
                return self.send_json(401, {"error": "unauthorized"})
            with sqlite3.connect(DB_PATH) as db:
                rows = db.execute("SELECT id,app_version,message,created_at FROM feedback ORDER BY id DESC LIMIT 200").fetchall()
            return self.send_json(200, [{"id": r[0], "appVersion": r[1], "message": r[2], "createdAt": r[3]} for r in rows])
        if parsed.path == "/api/jobs":
            with lock:
                snapshot = list(jobs.values())
            return self.send_json(200, snapshot)
        path = "/index.html" if parsed.path == "/" else parsed.path
        file = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in file.parents or not file.is_file():
            return self.send_json(404, {"error": "not found"})
        raw = file.read_bytes()
        mime = "text/html; charset=utf-8" if file.suffix == ".html" else "text/css; charset=utf-8" if file.suffix == ".css" else "application/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        try:
            data = self.read_json()
            if self.path == "/api/analytics/events":
                install_id = valid_install_id(data.get("install_id"))
                event = str(data.get("event", ""))
                if event not in ALLOWED_EVENTS:
                    raise ValueError("invalid event")
                row = (install_id, event, str(data.get("app_version", ""))[:24],
                       str(data.get("android_version", ""))[:24],
                       str(data.get("channel", "direct"))[:32],
                       str(data.get("outcome", ""))[:64], now_iso())
                with sqlite3.connect(DB_PATH) as db:
                    if event == "first_launch" and db.execute("SELECT 1 FROM events WHERE install_id=? AND event='first_launch'", (install_id,)).fetchone():
                        return self.send_json(200, {"ok": True, "duplicate": True})
                    db.execute("INSERT INTO events(install_id,event,app_version,android_version,channel,outcome,created_at) VALUES(?,?,?,?,?,?,?)", row)
                return self.send_json(201, {"ok": True})
            if self.path == "/api/analytics/feedback":
                install_id = valid_install_id(data.get("install_id"))
                message = str(data.get("message", "")).strip()
                if not 2 <= len(message) <= 1000:
                    raise ValueError("feedback length must be 2..1000")
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("INSERT INTO feedback(install_id,app_version,message,created_at) VALUES(?,?,?,?)",
                               (install_id, str(data.get("app_version", ""))[:24], message, now_iso()))
                return self.send_json(201, {"ok": True})
            if self.path == "/api/jobs":
                target = str(data.get("targetTime", "09:30:00.000"))
                datetime.strptime(target, "%H:%M:%S.%f")
                job_id = uuid.uuid4().hex[:10]
                job = {
                    "id": job_id,
                    "couponType": str(data.get("couponType", "大牌券"))[:50],
                    "targetTime": target,
                    "attempts": max(1, min(int(data.get("attempts", 1)), 5)),
                    "intervalSeconds": max(1.0, float(data.get("intervalSeconds", 1))),
                    "status": "created", "targetAt": "", "cancelled": False, "logs": []
                }
                with lock:
                    jobs[job_id] = job
                threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
                return self.send_json(201, job)
            if self.path.startswith("/api/jobs/") and self.path.endswith("/cancel"):
                job_id = self.path.split("/")[3]
                with lock:
                    if job_id not in jobs:
                        return self.send_json(404, {"error": "job not found"})
                    jobs[job_id]["cancelled"] = True
                return self.send_json(200, {"ok": True})
            return self.send_json(404, {"error": "not found"})
        except Exception as exc:
            return self.send_json(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{now_iso()}] {self.address_string()} {fmt % args}")


def main() -> None:
    init_db()
    print(f"Coupon Timer listening on http://{HOST}:{PORT} ({'authorized' if AUTHORIZED_ENDPOINT else 'demo'} mode)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
