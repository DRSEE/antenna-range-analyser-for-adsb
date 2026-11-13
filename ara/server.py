"""Serveur web : page de visualisation et API JSON."""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from threading import Thread

from . import analysis
from .config import LOCAL, ROOT, receiver_position
from .terrain import Terrain

WEB = os.path.join(ROOT, 'web')


def make_handler(cfg, store, collector):
    terrain = Terrain(cfg['data_dir'])
    terrain_cache = {}
    def period(q, key='campaign'):
        c = q.get(key, [''])[0]
        if c.startswith('h'):                        # « h24 » : dernières 24 h
            return store.period(hours=float(c[1:]))
        return store.period(campaign=int(c) if c else None, hours=q.get('hours', ['24'])[0])

    def num(q, k, default=None):
        v = q.get(k, [''])[0]
        return float(v) if v not in ('', 'null') else default

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith(('text', 'application/json')) else ''))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ('/', '/index.html'):
                    with open(os.path.join(WEB, 'index.html'), 'rb') as f:
                        return self.send(200, f.read(), 'text/html')
                if u.path == '/api/live':
                    stats = {}
                    try:
                        with open(os.path.join(cfg['readsb_dir'], 'stats.json'), encoding='utf-8') as f:
                            s = json.load(f).get('last1min', {})
                        loc = s.get('local', {})
                        stats = {'messages_1min': s.get('messages'), 'signal': loc.get('signal'), 'noise': loc.get('noise'),
                                 'peak_signal': loc.get('peak_signal'), 'strong_signals': loc.get('strong_signals')}
                    except (OSError, ValueError):
                        pass
                    return self.send(200, {'aircraft': collector.live, 'stats': stats, 'antenna_alt_m': cfg['antenna_alt_m'],
                                           'time': time.time()})
                if u.path == '/api/status':
                    con = store.connect()
                    n, n_rssi, first = con.execute('SELECT COUNT(*), COUNT(gain), MIN(t) FROM samples').fetchone()
                    nref = con.execute('SELECT COUNT(*) FROM reference').fetchone()[0]
                    con.close()
                    return self.send(200, {'samples': n, 'samples_with_gain': n_rssi, 'first': first, 'reference_samples': nref,
                                           'antenna_alt_m': cfg['antenna_alt_m'], 'reference_enabled': cfg['reference']['enabled'],
                                           'sample_interval_s': cfg['sample_interval_s'], 'retention_days': cfg['retention_days']})
                if u.path == '/api/campaigns':
                    return self.send(200, store.campaigns())
                if u.path == '/api/range':
                    t0, t1, label = period(q)
                    out = analysis.range_by_azimuth(store, t0, t1, q.get('band', ['all'])[0], int(num(q, 'step', 5)),
                                                    cfg['antenna_alt_m'])
                    return self.send(200, {**out, 'label': label})
                if u.path == '/api/pattern':
                    t0, t1, label = period(q)
                    out = analysis.pattern(store, t0, t1, q.get('correct', ['1'])[0] == '1', num(q, 'el_lo', 0.0),
                                           num(q, 'el_hi', 10.0), num(q, 'az'), num(q, 'half', 180.0), num(q, 'max_dist'),
                                           antenna_alt_m=cfg['antenna_alt_m'])
                    return self.send(200, {**out, 'label': label})
                if u.path == '/api/compare':
                    out = analysis.compare(store, period(q, 'a'), period(q, 'b'), cfg['antenna_alt_m'],
                                           num(q, 'el_lo', 0.0), num(q, 'el_hi', 10.0))
                    return self.send(200, out)
                if u.path == '/api/terrain':
                    lat0, lon0 = receiver_position(cfg)
                    out = {'state': terrain.state, 'antenna_alt_m': cfg['antenna_alt_m']}
                    if terrain.state['status'] == 'prêt' and lat0 is not None and cfg['antenna_alt_m'] is not None:
                        key = (cfg['antenna_alt_m'],)
                        if terrain_cache.get('key') != key:
                            terrain_cache.update(key=key, mask=terrain.mask(lat0, lon0, cfg['antenna_alt_m']),
                                                 grid=terrain.grid(lat0, lon0))
                        out.update(mask=terrain_cache['mask'], grid=terrain_cache['grid'])
                    return self.send(200, out)
                if u.path == '/api/coverage3d':
                    t0, t1, label = period(q)
                    return self.send(200, {'label': label, 'antenna_alt_m': cfg['antenna_alt_m'],
                                           'volume': analysis.coverage_volume(store, t0, t1, cfg['antenna_alt_m']),
                                           'mask': analysis.measured_mask(store, t0, t1, cfg['antenna_alt_m'])})
                if u.path == '/api/profile':
                    t0, t1, label = period(q)
                    az = num(q, 'az', 0.0) % 360
                    out = {'label': label, 'az': az, 'antenna_alt_m': cfg['antenna_alt_m'],
                           'aircraft': analysis.azimuth_points(store, t0, t1, az)}
                    lat0, lon0 = receiver_position(cfg)
                    if terrain.state['status'] == 'prêt' and lat0 is not None and cfg['antenna_alt_m'] is not None:
                        d, h, drop, ang = terrain.profile(lat0, lon0, az, cfg['antenna_alt_m'])
                        out['terrain'] = {'dist': d.round(1).tolist(), 'alt_m': [None if v != v else int(v) for v in h],
                                          'drop_m': drop.round().tolist(), 'angle': [None if v != v else round(float(v), 3) for v in ang]}
                    return self.send(200, out)
                if u.path == '/api/detection':
                    t0, t1, label = period(q)
                    return self.send(200, {**analysis.detection(store, t0, t1), 'label': label})
                self.send(404, {'error': 'introuvable'})
            except (ValueError, KeyError) as e:
                self.send(400, {'error': str(e)})
            except Exception as e:
                print(f'GET {u.path} : {e!r}')
                self.send(500, {'error': 'erreur interne'})

        def do_POST(self):
            u = urlparse(self.path)
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if n > 10000:
                    raise ValueError('requête trop grande')
                body = json.loads(self.rfile.read(n) or b'{}')
                if u.path == '/api/campaigns':
                    act = body.get('action')
                    if act == 'start':
                        name = str(body.get('name') or '').strip()[:80]
                        if not name:
                            raise ValueError('nom de campagne requis')
                        return self.send(200, {'id': store.start_campaign(name, str(body.get('notes') or '')[:500])})
                    if act == 'stop':
                        store.stop_campaign(int(body['id']))
                        return self.send(200, {'ok': True})
                    if act == 'delete':
                        store.delete_campaign(int(body['id']))
                        return self.send(200, {'ok': True})
                    raise ValueError('action inconnue')
                if u.path == '/api/terrain':
                    lat0, lon0 = receiver_position(cfg)
                    if lat0 is None:
                        raise ValueError('position du récepteur introuvable (receiver.json)')
                    if terrain.state['status'] != 'téléchargement':
                        Thread(target=terrain.fetch, args=(lat0, lon0), daemon=True).start()
                        terrain_cache.clear()
                    return self.send(200, {'ok': True})
                if u.path == '/api/config':
                    alt = body.get('antenna_alt_m')
                    alt = None if alt in (None, '') else float(alt)
                    if alt is not None and not -100 <= alt <= 5000:
                        raise ValueError('altitude hors bornes (−100 à 5 000 m)')
                    local = {}
                    if os.path.exists(LOCAL):
                        with open(LOCAL, encoding='utf-8') as f:
                            local = json.load(f)
                    local['antenna_alt_m'] = alt
                    with open(LOCAL, 'w', encoding='utf-8') as f:
                        json.dump(local, f, indent=2)
                    cfg['antenna_alt_m'] = alt
                    analysis._cache.clear()
                    return self.send(200, {'ok': True, 'antenna_alt_m': alt})
                self.send(404, {'error': 'introuvable'})
            except (ValueError, KeyError, TypeError) as e:
                self.send(400, {'error': str(e)})

    return Handler


def serve(cfg, store, collector):
    httpd = ThreadingHTTPServer((cfg['host'], cfg['port']), make_handler(cfg, store, collector))
    httpd.daemon_threads = True
    print(f"Analyseur de portée : http://<ip-du-pi>:{cfg['port']}")
    httpd.serve_forever()
