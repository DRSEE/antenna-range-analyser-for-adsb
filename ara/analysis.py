"""Analyses : portée par azimut, diagramme de rayonnement mesuré, comparaison de campagnes, détection."""
import time

import numpy as np

from . import geometry as geo

EL_EDGES = [-2, 0, 1, 2, 3, 4, 5, 6, 8, 10, 13, 16, 20, 25, 30, 40, 60, 90]
ALT_BANDS = {'all': (-1e9, 1e9), 'low': (-1e9, 10000), 'mid': (10000, 25000), 'high': (25000, 1e9)}
MIN_N = 5
_cache = {}


def cached(fn):
    def wrap(*args, **kw):
        key = (fn.__name__, args[1:], tuple(sorted(kw.items())))
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        out = fn(*args, **kw)
        _cache[key] = (time.time(), out)
        if len(_cache) > 64:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:16]:
                del _cache[k]
        return out
    return wrap


def _load(store, t0, t1, need_gain, antenna_alt_m=None):
    con = store.connect()
    q = 'SELECT az, dist_km, alt_ft, gain, hex FROM samples WHERE t BETWEEN ? AND ?'
    if need_gain:
        q += ' AND gain IS NOT NULL AND alt_ft IS NOT NULL'
    rows = con.execute(q, (t0, t1)).fetchall()
    con.close()
    if not rows:
        return None
    az, dist, alt, gain, hexes = zip(*rows)
    f = lambda v: np.array([np.nan if x is None else x for x in v], dtype=float)
    d = {'az': f(az), 'dist': f(dist), 'alt': f(alt), 'gain': f(gain), 'hex': np.array(hexes)}
    # élévation calculée à l'analyse : une correction de l'altitude d'antenne s'applique aussi au passé
    if antenna_alt_m is not None:
        dh = (d['alt'] * geo.FT - antenna_alt_m) / 1000.0
        d['el'] = np.degrees(np.arctan2(dh - d['dist'] ** 2 / (2 * geo.K_FACTOR * geo.R_EARTH_KM), d['dist']))
    else:
        d['el'] = np.full(d['az'].size, np.nan)
    return d


def _r(x, n=1):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


# ---------------------------------------------------------------- portée
@cached
def range_by_azimuth(store, t0, t1, band='all', step=5, antenna_alt_m=None):
    d = _load(store, t0, t1, False)
    nb = int(360 / step)
    out = {'step': step, 'band': band, 'az': [i * step + step / 2 for i in range(nb)],
           'max': [None] * nb, 'p95': [None] * nb, 'p50': [None] * nb, 'n': [0] * nb, 'total': 0}
    lo, hi = ALT_BANDS.get(band, ALT_BANDS['all'])
    if antenna_alt_m is not None:
        ref_ft = {'low': 10000, 'mid': 25000, 'high': 38000, 'all': 38000}[band]
        out['horizon_km'] = round(geo.radio_horizon_km(antenna_alt_m, ref_ft * geo.FT))
        out['horizon_alt_ft'] = ref_ft
    if d is None:
        return out
    m = (d['alt'] >= lo) & (d['alt'] < hi) & np.isfinite(d['dist'])
    az, dist = d['az'][m], d['dist'][m]
    idx = (az // step).astype(int) % nb
    for i in range(nb):
        v = dist[idx == i]
        out['n'][i] = int(v.size)
        if v.size:
            out['max'][i] = _r(v.max())
            out['p95'][i] = _r(np.percentile(v, 95))
            out['p50'][i] = _r(np.percentile(v, 50))
    out['total'] = int(dist.size)
    out['aircraft'] = int(np.unique(d['hex'][m]).size)
    return out


# ---------------------------------------------------------------- diagramme de rayonnement
def _corrected_gain(d, correct):
    """Retire la part propre à chaque avion (puissance d'émission, câblage) par moindres carrés alternés.

    gain_mesuré = G(direction) + P(avion) + bruit ; P est centrée sur 0 pour garder le niveau absolu.
    """
    g = d['gain'].copy()
    if not correct:
        return g
    az_i = (d['az'] // 10).astype(int) % 36
    el_i = np.clip(np.searchsorted(EL_EDGES, d['el'], side='right') - 1, 0, len(EL_EDGES) - 2)
    cell = az_i * 100 + el_i
    hexes, h_i = np.unique(d['hex'], return_inverse=True)
    counts = np.bincount(h_i)
    off = np.zeros(hexes.size)
    for _ in range(6):
        resid = g - off[h_i]
        cells, c_i = np.unique(cell, return_inverse=True)
        cell_mean = np.bincount(c_i, resid) / np.bincount(c_i)
        r2 = g - cell_mean[c_i]
        off = np.bincount(h_i, r2, minlength=hexes.size) / np.maximum(counts, 1)
        off[counts < 5] = 0.0                     # trop peu de points pour estimer l'avion
        off -= np.average(off[counts >= 5]) if (counts >= 5).any() else 0
    return g - off[h_i]


def _cut(values, keys, edges_or_n, min_n=MIN_N):
    means, ns, spreads = [], [], []
    for k in range(edges_or_n):
        v = values[keys == k]
        ns.append(int(v.size))
        means.append(_r(np.median(v), 2) if v.size >= min_n else None)
        spreads.append(_r(np.percentile(v, 75) - np.percentile(v, 25), 1) if v.size >= min_n else None)
    return means, ns, spreads


@cached
def pattern(store, t0, t1, correct=True, el_lo=0.0, el_hi=10.0, az_center=None, az_half=180.0, max_dist_km=None,
            antenna_alt_m=None):
    if antenna_alt_m is None:
        return {'samples': 0, 'reason': "altitude de l'antenne non renseignée"}
    d = _load(store, t0, t1, True, antenna_alt_m)
    if d is None:
        return {'samples': 0}
    if max_dist_km:
        keep = d['dist'] <= max_dist_km
        d = {k: v[keep] for k, v in d.items()}
        if not d['gain'].size:
            return {'samples': 0}
    g = _corrected_gain(d, correct)
    # grille 3D : azimut 10° × bandes d'élévation
    az_i = (d['az'] // 10).astype(int) % 36
    el_i = np.clip(np.searchsorted(EL_EDGES, d['el'], side='right') - 1, 0, len(EL_EDGES) - 2)
    grid, grid_n = [], []
    for e in range(len(EL_EDGES) - 1):
        row, row_n = _cut(g[el_i == e], az_i[el_i == e], 36)[:2]
        grid.append(row)
        grid_n.append(row_n)
    # plan horizontal : bande d'élévation choisie, pas de 5°
    m = (d['el'] >= el_lo) & (d['el'] < el_hi)
    h_means, h_n, h_spread = _cut(g[m], (d['az'][m] // 5).astype(int) % 72, 72)
    # plan vertical : secteur d'azimut choisi, pas de 1° d'élévation
    if az_center is None:
        sm = np.ones(g.size, dtype=bool)
    else:
        sm = np.abs(((d['az'] - az_center + 540) % 360) - 180) <= az_half
    el_bins = np.arange(-2, 61)
    v_idx = np.clip(np.floor(d['el'][sm]).astype(int) + 2, 0, el_bins.size - 1)
    v_means, v_n, v_spread = _cut(g[sm], v_idx, el_bins.size)
    vals = [x for row in grid for x in row if x is not None]
    peak = max(vals + [x for x in h_means + v_means if x is not None]) if vals else None
    return {
        'samples': int(g.size), 'aircraft': int(np.unique(d['hex']).size), 'corrected': bool(correct), 'peak': peak,
        'grid': {'az': [i * 10 + 5 for i in range(36)], 'el_edges': EL_EDGES, 'values': grid, 'n': grid_n},
        'horizontal': {'az': [i * 5 + 2.5 for i in range(72)], 'values': h_means, 'n': h_n, 'spread': h_spread,
                       'el_lo': el_lo, 'el_hi': el_hi},
        'vertical': {'el': [float(e) + 0.5 for e in el_bins], 'values': v_means, 'n': v_n, 'spread': v_spread,
                     'az_center': az_center, 'az_half': az_half},
    }


# ---------------------------------------------------------------- comparaison
def _delta(a, b):
    return [None if x is None or y is None else round(y - x, 2) for x, y in zip(a, b)]


def compare(store, pa, pb, antenna_alt_m=None, el_lo=0.0, el_hi=10.0):
    out = {}
    for key, (t0, t1, label) in (('a', pa), ('b', pb)):
        out[key] = {'label': label, 'range': range_by_azimuth(store, t0, t1, 'all', 10, antenna_alt_m),
                    'pattern': pattern(store, t0, t1, True, el_lo, el_hi, antenna_alt_m=antenna_alt_m)}
    ra, rb = out['a']['range'], out['b']['range']
    out['delta_range_p95'] = _delta(ra['p95'], rb['p95'])
    ha = out['a']['pattern'].get('horizontal', {}).get('values') or [None] * 72
    hb = out['b']['pattern'].get('horizontal', {}).get('values') or [None] * 72
    out['delta_gain'] = _delta(ha, hb)

    def med(v):
        v = [x for x in v if x is not None]
        return round(float(np.median(v)), 2) if v else None
    out['summary'] = {'delta_gain_median_db': med(out['delta_gain']),
                      'delta_range_p95_median_km': med(out['delta_range_p95']),
                      'samples_a': out['a']['pattern'].get('samples', 0), 'samples_b': out['b']['pattern'].get('samples', 0)}
    return out


# ---------------------------------------------------------------- probabilité de détection (référence externe)
DIST_EDGES = [0, 50, 100, 150, 200, 250, 300, 400]


@cached
def detection(store, t0, t1):
    con = store.connect()
    rows = con.execute('SELECT az, dist_km, detected FROM reference WHERE t BETWEEN ? AND ?', (t0, t1)).fetchall()
    con.close()
    if not rows:
        return {'samples': 0}
    az, dist, det = (np.array(x, dtype=float) for x in zip(*rows))
    by_dist = []
    for i in range(len(DIST_EDGES) - 1):
        m = (dist >= DIST_EDGES[i]) & (dist < DIST_EDGES[i + 1])
        by_dist.append({'from': DIST_EDGES[i], 'to': DIST_EDGES[i + 1], 'n': int(m.sum()),
                        'p': _r(det[m].mean() * 100) if m.sum() >= MIN_N else None})
    az_i = (az // 10).astype(int) % 36
    by_az = []
    for i in range(36):
        m = (az_i == i) & (dist < 200)
        by_az.append(_r(det[m].mean() * 100) if m.sum() >= MIN_N else None)
    return {'samples': len(rows), 'by_dist': by_dist, 'by_az': by_az, 'az': [i * 10 + 5 for i in range(36)]}
