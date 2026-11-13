"""Collecte : un échantillon par avion toutes les N secondes à partir d'aircraft.json (readsb)."""
import glob
import gzip
import json
import os
import time
import urllib.request

from . import geometry as geo
from .config import receiver_position

RSSI_SATURATION = -3.0      # dBFS : au-delà, le récepteur sature et le RSSI ne mesure plus rien
MIN_DIST_KM = 2.0           # trop près : élévation très variable, champ proche du mât
FRESH_POS_S = 2.0           # position plus ancienne : readsb répète le dernier point connu


def aircraft_altitude_m(a, cfg):
    """Altitude de l'avion (m) : géométrique (GNSS) de préférence, sinon pression."""
    if isinstance(a.get('alt_geom'), (int, float)):
        return a['alt_geom'] * geo.FT - cfg['geoid_offset_m']
    if isinstance(a.get('alt_baro'), (int, float)):
        return a['alt_baro'] * geo.FT
    return None


def make_sample(t, hexc, az, dist_km, alt_m, rssi, cfg, src):
    ant = cfg['antenna_alt_m']
    el = geo.elevation_deg(dist_km, alt_m, ant) if ant is not None and alt_m is not None else None
    gain = None
    if rssi is not None and rssi < RSSI_SATURATION and dist_km >= MIN_DIST_KM:
        gain = round(geo.gain_index_db(rssi, dist_km), 2)
    alt_ft = round(alt_m / geo.FT) if alt_m is not None else None
    return (round(t, 1), hexc, round(az, 1), round(dist_km, 2), alt_ft,
            round(el, 2) if el is not None else None, rssi, gain, src)


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class Collector:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store
        self.last = {}            # hex -> instant du dernier échantillon
        self.live = []            # dernière image d'aircraft.json, pour la page temps réel

    def poll(self):
        """Lit aircraft.json et renvoie les nouveaux échantillons."""
        d = read_json(os.path.join(self.cfg['readsb_dir'], 'aircraft.json'))
        now = d.get('now', time.time())
        rows, live = [], []
        for a in d.get('aircraft', []):
            if a.get('r_dst') is None or a.get('r_dir') is None or (a.get('seen_pos') or 99) > FRESH_POS_S:
                continue
            alt_m = aircraft_altitude_m(a, self.cfg)
            if alt_m is None:
                continue
            dist_km = a['r_dst'] * geo.NM_KM
            s = make_sample(now - a.get('seen_pos', 0), a['hex'], a['r_dir'], dist_km, alt_m, a.get('rssi'), self.cfg, 'live')
            live.append({'hex': a['hex'], 'flight': (a.get('flight') or '').strip(), 'az': s[2], 'dist_km': s[3],
                         'alt_ft': s[4], 'el': s[5], 'rssi': s[6], 'gain': s[7]})
            if now - self.last.get(a['hex'], 0) >= self.cfg['sample_interval_s']:
                self.last[a['hex']] = now
                rows.append(s)
        self.live = live
        if len(self.last) > 5000:
            self.last = {k: v for k, v in self.last.items() if now - v < 600}
        return rows

    def run(self):
        buf, last_flush, last_purge = [], time.time(), 0
        while True:
            try:
                buf += self.poll()
            except Exception as e:
                print(f'collector: {e}')
            now = time.time()
            if now - last_flush >= 10:
                self.store.add_samples(buf)
                buf, last_flush = [], now
            if now - last_purge >= 3600:
                self.store.purge(self.cfg['retention_days'])
                last_purge = now
            time.sleep(1)


def import_tar1090(cfg, store):
    """Importe l'historique de tar1090 (positions sans RSSI) : sert à la carte de portée dès le démarrage."""
    lat0, lon0 = receiver_position(cfg)
    if lat0 is None:
        return 0
    done = float(store.meta('tar1090_imported_until') or 0)
    last, rows, newest = {}, [], done
    for path in sorted(glob.glob(os.path.join(cfg['tar1090_dir'], 'chunk_*.gz'))):
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as f:
                chunk = json.load(f)
        except (OSError, ValueError):
            continue
        for snap in chunk.get('files', []):
            t = snap.get('now', 0)
            if t <= done:
                continue
            newest = max(newest, t)
            for a in snap.get('aircraft', []):
                hexc, alt, lat, lon = a[0], a[1], a[4], a[5]
                if lat is None or lon is None or not isinstance(alt, (int, float)):
                    continue
                if t - last.get(hexc, 0) < cfg['sample_interval_s']:
                    continue
                last[hexc] = t
                dist = geo.haversine_km(lat0, lon0, lat, lon)
                az = geo.bearing_deg(lat0, lon0, lat, lon)
                rows.append(make_sample(t, hexc, az, dist, alt * geo.FT, None, cfg, 'tar1090'))
    store.add_samples(rows)
    store.meta('tar1090_imported_until', newest)
    return len(rows)


def reference_loop(cfg, store, collector):
    """Référence externe : avions vus par le réseau autour de la station, reçus ou non par l'antenne.

    Désactivé par défaut : la requête transmet la position de la station au service interrogé.
    """
    ref = cfg['reference']
    lat0, lon0 = receiver_position(cfg)
    if not ref.get('enabled') or lat0 is None:
        return
    url = ref['url'].format(lat=round(lat0, 3), lon=round(lon0, 3), radius=ref['radius_nm'])
    while True:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'antenna-range-analyser'})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.load(r)
            local = {a['hex'] for a in read_json(os.path.join(cfg['readsb_dir'], 'aircraft.json')).get('aircraft', [])
                     if (a.get('seen') or 99) < 60}
            now, rows = time.time(), []
            for a in d.get('ac', d.get('aircraft', [])):
                if a.get('lat') is None or not isinstance(a.get('alt_baro'), (int, float)):
                    continue
                dist = geo.haversine_km(lat0, lon0, a['lat'], a['lon'])
                rows.append((now, a['hex'], round(geo.bearing_deg(lat0, lon0, a['lat'], a['lon']), 1), round(dist, 2),
                             a['alt_baro'], 1 if a['hex'] in local else 0))
            store.add_reference(rows)
        except Exception as e:
            print(f'reference: {e}')
        time.sleep(max(30, ref['interval_s']))
