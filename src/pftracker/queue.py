"""Durable outbox; rows remain until acknowledged, including during HTTP calls."""
import json
import sqlite3
import threading
import time
from pathlib import Path


class Outbox:
    def __init__(self, path, max_points=100000, max_mb=128):
        self.lock = threading.RLock()
        self.max_points, self.max_bytes = max_points, max_mb*1024*1024
        self.db = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS points (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, epoch REAL NOT NULL,
          body TEXT NOT NULL, bytes INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        # Scheduling uses monotonic memory state; persisted points become eligible after reboot.
        self.cooldown = {}
        self.session_min_seq = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM points').fetchone()[0]
        self.db.commit()

    def _get(self, key, default=0):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def enqueue(self, payload, url):
        body = json.dumps({'payload': payload, 'url': url}, separators=(',', ':'), allow_nan=False)
        size = len(body.encode()) + 128
        with self.lock, self.db:
            # Dedup across process and machine restarts, per device and destination.
            identity = url + '\n' + payload['id']
            last = self._get('last_epoch:' + identity, 0)
            if payload['timestamp'] <= last:
                return False
            self.db.execute('INSERT INTO points(epoch,body,bytes) VALUES (?,?,?)', (payload['timestamp'], body, size))
            self._set('last_epoch:' + identity, payload['timestamp'])
            count, used = self.db.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM points').fetchone()
            dropped = 0
            # Delete in bounded batches; keep the newest points under both limits.
            while count > self.max_points or used > self.max_bytes:
                rows = self.db.execute('SELECT seq,bytes FROM points ORDER BY seq LIMIT 256').fetchall()
                remove = []
                for seq, n in rows:
                    if count <= self.max_points and used <= self.max_bytes:
                        break
                    remove.append((seq,))
                    count, used, dropped = count-1, used-n, dropped+1
                    self.cooldown.pop(seq, None)
                self.db.executemany('DELETE FROM points WHERE seq=?', remove)
            if dropped:
                self._set('dropped', self._get('dropped') + dropped)
                self._set('last_drop', time.time())
            return True

    def next(self, now):
        with self.lock:
            # Only newly captured, never-attempted points take the real-time lane.
            row = self.db.execute('SELECT seq,body,attempts FROM points WHERE seq>=? AND attempts=0 ORDER BY seq DESC LIMIT 1', (self.session_min_seq,)).fetchone()
            if row:
                self.session_min_seq = row[0]+1
            else:
                # A failing point must not block all older or newer history.
                blocked = {key for key, until in self.cooldown.items() if until > now}
                self.cooldown = {key: until for key, until in self.cooldown.items() if until > now}
                row = next((r for r in self.db.execute('SELECT seq,body,attempts FROM points ORDER BY seq') if r[0] not in blocked), None)
            return (row[0], json.loads(row[1]), row[2]) if row else None

    def acknowledge(self, seq):
        with self.lock, self.db:
            self.db.execute('DELETE FROM points WHERE seq=?', (seq,))
            self.cooldown.pop(seq, None)
            self._set('sent', self._get('sent')+1)
            self._set('last_success', time.time())

    def fail(self, seq, now, delay):
        with self.lock, self.db:
            self.db.execute('UPDATE points SET attempts=attempts+1 WHERE seq=?', (seq,))
            self.cooldown[seq] = now + delay
            self._set('failures', self._get('failures')+1)

    def stats(self):
        with self.lock:
            count, size, oldest, newest = self.db.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0),MIN(epoch),MAX(epoch) FROM points').fetchone()
            return dict(count=count, payload_bytes=size, oldest=oldest, newest=newest,
                        dropped=self._get('dropped'), last_drop=self._get('last_drop'),
                        sent=self._get('sent'), failures=self._get('failures'), last_success=self._get('last_success'))

    def close(self):
        with self.lock:
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.db.close()
