"""Relief autour de la station : masque d'horizon prédit, profils RF et grille 3D.

Source : tuiles d'altitude « Terrarium » du jeu de données ouvert AWS Terrain Tiles (zoom 8, ≈ 400 m par pixel).
Les tuiles sont téléchargées une fois puis gardées dans data/terrain/. Télécharger des tuiles révèle au service la zone
couverte (≈ 300 km autour de la station) ; c'est pourquoi le chargement se lance à la demande.
"""
import math
import os
import time
import urllib.request

import numpy as np

from . import geometry as geo

URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
ZOOM = 8
RADIUS_KM = 320
KM_PER_DEG = 111.2


def _tile_xy(lat, lon, z=ZOOM):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2 * n
    return x, y


class Terrain:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, 'terrain', str(ZOOM))
        self.tiles = {}
        self.state = {'status': 'absent', 'tiles': 0, 'needed': 0, 'error': None}
        if os.path.isdir(self.dir) and os.listdir(self.dir):
            self.state['status'] = 'ready'

    # ------------------------------------------------------------ tuiles
    def needed(self, lat0, lon0):
        dlat = RADIUS_KM / KM_PER_DEG
        dlon = RADIUS_KM / (KM_PER_DEG * math.cos(math.radians(lat0)))
        x0, y1 = _tile_xy(lat0 - dlat, lon0 - dlon)
        x1, y0 = _tile_xy(lat0 + dlat, lon0 + dlon)
        return [(x, y) for x in range(int(x0), int(x1) + 1) for y in range(int(y0), int(y1) + 1)]

    def fetch(self, lat0, lon0):
        """Télécharge les tuiles manquantes (à lancer dans un thread)."""
        tiles = self.needed(lat0, lon0)
        self.state.update(status='downloading', needed=len(tiles), tiles=0, error=None)
        try:
            for x, y in tiles:
                path = os.path.join(self.dir, f'{x}_{y}.png')
                if not os.path.exists(path):
                    os.makedirs(self.dir, exist_ok=True)
                    req = urllib.request.Request(URL.format(z=ZOOM, x=x, y=y), headers={'User-Agent': 'antenna-range-analyser'})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        data = r.read()
                    with open(path + '.tmp', 'wb') as f:
                        f.write(data)
                    os.replace(path + '.tmp', path)
                    time.sleep(0.2)
                self.state['tiles'] += 1
            self.tiles.clear()
            self.state['status'] = 'ready'
        except Exception as e:
            self.state.update(status='error', error=str(e))

    def _tile(self, x, y):
        key = (x, y)
        if key not in self.tiles:
            path = os.path.join(self.dir, f'{x}_{y}.png')
            if not os.path.exists(path):
                self.tiles[key] = None
            else:
                from PIL import Image
                a = np.asarray(Image.open(path).convert('RGB'), dtype=np.float32)
                self.tiles[key] = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
        return self.tiles[key]

    def elevation(self, lat, lon):
        """Altitude du terrain (m) pour des tableaux de latitudes et longitudes ; NaN hors tuiles."""
        lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
        x, y = _tile_xy(lat, lon)
        tx, ty = np.floor(x).astype(int), np.floor(y).astype(int)
        px = np.clip(((x - tx) * 256).astype(int), 0, 255)
        py = np.clip(((y - ty) * 256).astype(int), 0, 255)
        out = np.full(lat.shape, np.nan, dtype=float)
        for key in set(zip(tx.ravel().tolist(), ty.ravel().tolist())):
            t = self._tile(*key)
            if t is None:
                continue
            m = (tx == key[0]) & (ty == key[1])
            out[m] = t[py[m], px[m]]
        return out

    # ------------------------------------------------------------ calculs
    @staticmethod
    def _dest(lat0, lon0, az_deg, d_km):
        p1, l1 = math.radians(lat0), math.radians(lon0)
        th, dl = np.radians(az_deg), np.asarray(d_km) / geo.R_EARTH_KM
        p2 = np.arcsin(np.sin(p1) * np.cos(dl) + np.cos(p1) * np.sin(dl) * np.cos(th))
        l2 = l1 + np.arctan2(np.sin(th) * np.sin(dl) * np.cos(p1), np.cos(dl) - np.sin(p1) * np.sin(p2))
        return np.degrees(p2), np.degrees(l2)

    def profile(self, lat0, lon0, az, h_ant, dmax=320.0, step=0.5):
        d = np.arange(step, dmax + step, step)
        lat, lon = self._dest(lat0, lon0, az, d)
        h = self.elevation(lat, lon)
        drop = d ** 2 / (2 * geo.K_FACTOR * geo.R_EARTH_KM) * 1000        # m
        ang = np.degrees(np.arctan2((h - h_ant - drop) / 1000, d))
        return d, h, drop, ang

    def mask(self, lat0, lon0, h_ant, step_deg=1):
        """Masque d'horizon prédit par le relief : angle maximal du terrain dans chaque direction et crête responsable."""
        out = {'az': [], 'angle': [], 'ridge_km': [], 'ridge_alt_m': []}
        for az in range(0, 360, step_deg):
            d, h, _, ang = self.profile(lat0, lon0, az, h_ant)
            ang = np.where(d >= 1.0, ang, np.nan)               # le 1er km est sous la résolution du relief
            if np.all(np.isnan(ang)):
                continue
            i = int(np.nanargmax(ang))
            out['az'].append(az)
            out['angle'].append(round(float(ang[i]), 2))
            out['ridge_km'].append(round(float(d[i]), 1))
            out['ridge_alt_m'].append(int(h[i]))
        return out

    def grid(self, lat0, lon0, radius_km=300, n=121):
        """Grille du relief en coordonnées locales (km est, km nord), pour l'affichage 3D."""
        xs = np.linspace(-radius_km, radius_km, n)
        X, Y = np.meshgrid(xs, xs)
        lat = lat0 + Y / KM_PER_DEG
        lon = lon0 + X / (KM_PER_DEG * math.cos(math.radians(lat0)))
        Z = self.elevation(lat, lon)
        return {'x': [round(float(v), 1) for v in xs], 'y': [round(float(v), 1) for v in xs],
                'z': [[None if not np.isfinite(v) else int(v) for v in row] for row in Z]}
