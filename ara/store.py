"""Stockage SQLite : échantillons, campagnes de mesure, échantillons de référence."""
import os
import sqlite3
import time
from threading import Lock

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    t REAL, hex TEXT, az REAL, dist_km REAL, alt_ft REAL, el REAL, rssi REAL, gain REAL, src TEXT
);
CREATE INDEX IF NOT EXISTS samples_t ON samples(t);
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, notes TEXT, t0 REAL NOT NULL, t1 REAL
);
CREATE TABLE IF NOT EXISTS reference (
    t REAL, hex TEXT, az REAL, dist_km REAL, alt_ft REAL, detected INTEGER
);
CREATE INDEX IF NOT EXISTS reference_t ON reference(t);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, 'antenna.db')
        self.lock = Lock()
        con = self.connect()
        con.executescript(SCHEMA)
        con.close()

    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.execute('PRAGMA journal_mode=WAL')
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------ écriture
    def add_samples(self, rows):
        if not rows:
            return
        with self.lock:
            con = self.connect()
            con.executemany('INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?)', rows)
            con.commit()
            con.close()

    def add_reference(self, rows):
        if not rows:
            return
        with self.lock:
            con = self.connect()
            con.executemany('INSERT INTO reference VALUES (?,?,?,?,?,?)', rows)
            con.commit()
            con.close()

    def meta(self, k, v=None):
        con = self.connect()
        if v is None:
            r = con.execute('SELECT v FROM meta WHERE k = ?', (k,)).fetchone()
            con.close()
            return r[0] if r else None
        with self.lock:
            con.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (k, str(v)))
            con.commit()
            con.close()

    def purge(self, retention_days):
        """Supprime les échantillons anciens, sauf ceux qui appartiennent à une campagne."""
        limit = time.time() - retention_days * 86400
        with self.lock:
            con = self.connect()
            camps = con.execute('SELECT t0, COALESCE(t1, 1e12) FROM campaigns').fetchall()
            keep = ' AND '.join(['NOT (t BETWEEN ? AND ?)'] * len(camps)) or '1'
            args = [x for c in camps for x in c]
            for table in ('samples', 'reference'):
                con.execute(f'DELETE FROM {table} WHERE t < ? AND {keep}', [limit] + args)
            con.commit()
            con.close()

    # ------------------------------------------------------------ campagnes
    def campaigns(self):
        con = self.connect()
        rows = [dict(r) for r in con.execute('SELECT * FROM campaigns ORDER BY t0 DESC')]
        for r in rows:
            n = con.execute('SELECT COUNT(*) FROM samples WHERE t BETWEEN ? AND ? AND rssi IS NOT NULL',
                            (r['t0'], r['t1'] or time.time())).fetchone()[0]
            r['samples'] = n
        con.close()
        return rows

    def start_campaign(self, name, notes=''):
        with self.lock:
            con = self.connect()
            now = time.time()
            con.execute('UPDATE campaigns SET t1 = ? WHERE t1 IS NULL', (now,))     # une seule campagne active
            cur = con.execute('INSERT INTO campaigns (name, notes, t0) VALUES (?, ?, ?)', (name, notes, now))
            con.commit()
            cid = cur.lastrowid
            con.close()
            return cid

    def stop_campaign(self, cid):
        with self.lock:
            con = self.connect()
            con.execute('UPDATE campaigns SET t1 = ? WHERE id = ? AND t1 IS NULL', (time.time(), cid))
            con.commit()
            con.close()

    def delete_campaign(self, cid):
        with self.lock:
            con = self.connect()
            con.execute('DELETE FROM campaigns WHERE id = ?', (cid,))
            con.commit()
            con.close()

    def period(self, campaign=None, hours=None):
        """(t0, t1, libellé) d'une campagne ou des dernières heures."""
        now = time.time()
        if campaign:
            con = self.connect()
            r = con.execute('SELECT * FROM campaigns WHERE id = ?', (campaign,)).fetchone()
            con.close()
            if not r:
                raise ValueError('unknown campaign')
            return r['t0'], r['t1'] or now, r['name']
        h = float(hours or 24)
        return now - h * 3600, now, f'last {h:g} h'
