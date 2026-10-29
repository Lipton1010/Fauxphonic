"""Local durable queue. Interrupted work restarts deterministically from the source."""
import json
import sqlite3
import time
import uuid


class Jobs:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request TEXT, state TEXT, updated REAL, detail TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (job_id TEXT, at REAL, event TEXT)")
        self.db.commit()

    def add(self, request):
        job_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO jobs VALUES (?, ?, 'queued', ?, '')", (job_id, json.dumps(request), time.time()))
        self.db.commit()
        return job_id

    def update(self, job_id, event):
        state = event.get("stage", "queued")
        detail = event.get("detail", event.get("status", ""))
        self.db.execute("UPDATE jobs SET state=?, updated=?, detail=? WHERE id=?", (state, time.time(), detail, job_id))
        self.db.execute("INSERT INTO events VALUES (?, ?, ?)", (job_id, time.time(), json.dumps(event)))
        self.db.commit()

    def recover(self):
        self.db.execute("UPDATE jobs SET state='interrupted' WHERE state NOT IN ('queued','complete','failed','cancelled','interrupted')")
        self.db.commit()

    def rows(self):
        return self.db.execute("SELECT id, request, state, detail FROM jobs ORDER BY updated").fetchall()
