"""One-host SQLite WAL queue. All state transitions use short transactions."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS reviews (
 id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL,
 fingerprint TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
 consent_version TEXT NOT NULL, consent_publication INTEGER NOT NULL,
 is_demo INTEGER NOT NULL DEFAULT 0, moderation TEXT NOT NULL DEFAULT 'pending',
 published_text TEXT, safe_text TEXT, status TEXT NOT NULL DEFAULT 'new',
 lease TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT, next_attempt REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS review_queue ON reviews(status,next_attempt,lease_until);
CREATE TABLE IF NOT EXISTS analyses (
 review_id INTEGER PRIMARY KEY REFERENCES reviews(id), payload TEXT NOT NULL,
 provider TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS analysis_history (
 id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL, payload TEXT NOT NULL,
 provider TEXT NOT NULL, archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS drafts (
 review_id INTEGER PRIMARY KEY REFERENCES reviews(id), text TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft', approved_by TEXT, approved_at TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
 review_id INTEGER PRIMARY KEY REFERENCES reviews(id), status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 message_id INTEGER, last_error TEXT, updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notification_events (
 id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL, status TEXT NOT NULL,
 error_code TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS moderation_events (
 id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL, action TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS daily_budget (day TEXT PRIMARY KEY, requests INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, text, request_id, consent_version, publication=False, demo=False):
        fingerprint = hashlib.sha256(text.strip().casefold().encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM reviews WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                return existing[0]
            recent = db.execute("SELECT id FROM reviews WHERE fingerprint=? AND created_at>datetime('now','-1 day')", (fingerprint,)).fetchone()
            if recent:
                return recent[0]
            cur = db.execute("INSERT INTO reviews(request_id,fingerprint,text,created_at,consent_version,consent_publication,is_demo) VALUES(?,?,?,strftime('%Y-%m-%d %H:%M:%f','now'),?,?,?)", (request_id, fingerprint, text, consent_version, int(publication), int(demo)))
            return cur.lastrowid

    def get(self, review_id):
        with self.connect() as db:
            row = db.execute("SELECT r.*,a.payload,a.provider,d.text AS draft,d.status AS draft_status,n.status AS notification_status,n.message_id FROM reviews r LEFT JOIN analyses a ON a.review_id=r.id LEFT JOIN drafts d ON d.review_id=r.id LEFT JOIN notifications n ON n.review_id=r.id WHERE r.id=?", (review_id,)).fetchone()
            return dict(row) if row else None

    def listing(self, public=False, after=0, limit=50):
        with self.connect() as db:
            if public:
                return [dict(r) for r in db.execute("SELECT r.id,r.published_text AS text,r.created_at,CASE WHEN d.status='approved' THEN d.text END AS response FROM reviews r LEFT JOIN drafts d ON d.review_id=r.id WHERE r.moderation='approved' AND r.is_demo=0 AND r.id>? ORDER BY r.id LIMIT ?", (after,limit))]
            return [dict(r) for r in db.execute("SELECT id,text,created_at,status,moderation FROM reviews WHERE id>? ORDER BY id LIMIT ?", (after,limit))]

    def claim(self):
        now, lease = time.time(), uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE reviews SET status='failed',lease=NULL,last_error='lease_exhausted' WHERE status='processing' AND lease_until<? AND attempts>=5",(now,))
            row = db.execute("SELECT * FROM reviews WHERE (status='new' OR (status='processing' AND lease_until<?)) AND next_attempt<=? AND attempts<5 ORDER BY id LIMIT 1", (now,now)).fetchone()
            if not row:
                return None
            db.execute("UPDATE reviews SET status='processing',lease=?,lease_until=?,attempts=attempts+1 WHERE id=?", (lease, now+300, row['id']))
            return {**dict(row), 'lease': lease}

    def complete(self, review, analysis, provider, notify):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE reviews SET status='processed',lease=NULL,lease_until=NULL,last_error=NULL WHERE id=? AND lease=?", (review['id'], review['lease'])).rowcount
            if not changed:
                return False
            db.execute("INSERT INTO analyses(review_id,payload,provider) VALUES(?,?,?)", (review['id'],analysis.model_dump_json(),provider))
            db.execute("INSERT INTO drafts(review_id,text) VALUES(?,?)", (review['id'],analysis.draft))
            if notify:
                db.execute("INSERT INTO notifications(review_id) VALUES(?) ON CONFLICT(review_id) DO UPDATE SET status='pending' WHERE status='cancelled'", (review['id'],))
            else:
                db.execute("UPDATE notifications SET status='cancelled' WHERE review_id=? AND status IN ('pending','retry')",(review['id'],))
            return True

    def failed(self, review, error):
        with self.connect() as db:
            db.execute("UPDATE reviews SET status=CASE WHEN attempts>=5 THEN 'failed' ELSE 'new' END,lease=NULL,last_error=?,next_attempt=? WHERE id=? AND lease=?", (error,time.time()+60,review['id'],review['lease']))

    def reserve_budget(self, limit):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO daily_budget VALUES(date('now'),0)")
            return bool(db.execute("UPDATE daily_budget SET requests=requests+1 WHERE day=date('now') AND requests<?",(limit,)).rowcount)

    def allow_request(self, key, limit=5, period=3600):
        now=time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM rate_limits WHERE expires<?",(now,))
            db.execute("INSERT OR IGNORE INTO rate_limits VALUES(?,0,?)",(key,now+period))
            return bool(db.execute("UPDATE rate_limits SET count=count+1 WHERE key=? AND count<?",(key,limit)).rowcount)

    def claim_notification(self):
        now=time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A crashed sender may already have delivered. Never blindly retry it.
            stale=db.execute("SELECT review_id FROM notifications WHERE status='sending' AND updated_at<?",(now-120,)).fetchall()
            for row in stale:
                db.execute("UPDATE notifications SET status='uncertain',last_error='interrupted_send' WHERE review_id=?",(row[0],))
                db.execute("INSERT INTO notification_events(review_id,status,error_code) VALUES(?,'uncertain','interrupted_send')",(row[0],))
            row=db.execute("SELECT review_id FROM notifications WHERE status IN ('pending','retry') AND EXISTS(SELECT 1 FROM analyses WHERE analyses.review_id=notifications.review_id) AND next_attempt<=? AND attempts<5 ORDER BY review_id LIMIT 1",(now,)).fetchone()
            if row:
                db.execute("UPDATE notifications SET status='sending',attempts=attempts+1,updated_at=? WHERE review_id=?",(now,row[0]))
                return row[0]

    def notification_result(self, review_id, status, message_id=None, error=None, retry_after=60):
        with self.connect() as db:
            db.execute("UPDATE notifications SET status=CASE WHEN ?='retry' AND attempts>=5 THEN 'failed' ELSE ? END,message_id=?,last_error=?,next_attempt=?,updated_at=? WHERE review_id=?",(status,status,message_id,error,time.time()+retry_after,time.time(),review_id))
            db.execute("INSERT INTO notification_events(review_id,status,error_code) VALUES(?,?,?)",(review_id,status,error))

    def moderate(self, review_id, action, actor, text=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM reviews WHERE id=?",(review_id,)).fetchone()
            if not row:
                raise ValueError('not_found')
            if action=='publish':
                if not row['consent_publication'] or row['is_demo']:
                    raise ValueError('publication_not_allowed')
                if not text or not 3<=len(text)<=3000:
                    raise ValueError('publication_text_required')
                db.execute("UPDATE reviews SET moderation='approved',published_text=? WHERE id=?",(text,review_id))
            elif action=='reject':
                db.execute("UPDATE reviews SET moderation='rejected',published_text=NULL WHERE id=?",(review_id,))
            elif action=='approve_reply':
                if not db.execute("UPDATE drafts SET status='approved',text=COALESCE(?,text),approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE review_id=? AND status IN ('draft','revision')",(text,actor,review_id)).rowcount:
                    return False
            elif action=='revise':
                db.execute("UPDATE drafts SET status='revision',approved_by=NULL,approved_at=NULL WHERE review_id=?",(review_id,))
            elif action=='retry_notification':
                # Explicit administrator reconciliation is required for uncertain sends.
                db.execute("UPDATE notifications SET status='retry',attempts=0,next_attempt=0 WHERE review_id=? AND status IN ('uncertain','failed')",(review_id,))
            elif action=='mark_delivered':
                db.execute("UPDATE notifications SET status='confirmed_manually',last_error=NULL WHERE review_id=? AND status='uncertain'",(review_id,))
            elif action=='safe_text':
                if not text or not 3<=len(text)<=3000:
                    raise ValueError('safe_text_required')
                if row['status']=='processing':
                    raise ValueError('processing_in_progress')
                draft=db.execute("SELECT status FROM drafts WHERE review_id=?",(review_id,)).fetchone()
                if draft and draft[0]=='approved':
                    raise ValueError('revoke_reply_before_reanalysis')
                db.execute("INSERT INTO analysis_history(review_id,payload,provider) SELECT review_id,payload,provider FROM analyses WHERE review_id=?",(review_id,))
                db.execute("DELETE FROM analyses WHERE review_id=?",(review_id,))
                db.execute("DELETE FROM drafts WHERE review_id=?",(review_id,))
                db.execute("UPDATE reviews SET safe_text=?,status='new',attempts=0,next_attempt=0 WHERE id=?",(text,review_id))
            else:
                raise ValueError('invalid_action')
            db.execute("INSERT INTO moderation_events(review_id,action,actor) VALUES(?,?,?)",(review_id,action,actor))
            return True

    def heartbeat(self):
        with self.connect() as db:
            db.execute("INSERT INTO meta VALUES('heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(time.time()),))
