"""Tests de la géométrie et de l'analyse : python3 -m unittest discover tests"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ara import geometry as geo, analysis          # noqa: E402
from ara.store import Store                       # noqa: E402


class Geometry(unittest.TestCase):
    def test_fspl(self):
        self.assertAlmostEqual(geo.fspl_db(100), 133.19, places=1)          # 100 km à 1 090 MHz

    def test_horizon(self):
        # antenne à 10 m, avion à 10 000 m : ≈ 13 + 412 km
        self.assertAlmostEqual(geo.radio_horizon_km(10, 10000), 425, delta=3)

    def test_elevation(self):
        self.assertAlmostEqual(geo.elevation_deg(10, 1000, 0), 5.7, delta=0.1)
        self.assertLess(geo.elevation_deg(450, 10000, 10), 0)               # au-delà de l'horizon radio (≈ 425 km)

    def test_bearing(self):
        self.assertAlmostEqual(geo.bearing_deg(45, 5, 46, 5), 0, delta=0.01)
        self.assertAlmostEqual(geo.bearing_deg(45, 5, 45, 6), 90, delta=0.5)


class Pattern(unittest.TestCase):
    def test_offset_correction_recovers_pattern(self):
        """Antenne simulée : +6 dB vers l'est ; transpondeurs de puissances différentes (±5 dB)."""
        import random
        random.seed(1)
        store = Store(tempfile.mkdtemp())
        now, rows = time.time(), []
        for k in range(60):
            p = random.uniform(-5, 5)                                      # puissance propre de l'avion
            for i in range(40):
                az = random.uniform(0, 360)
                g = 6.0 if 45 <= az < 135 else 0.0
                rows.append((now - i, f'a{k}', az, 50.0, 15000, None, -20.0, g + p + random.gauss(0, 1), 'test'))
        store.add_samples(rows)
        d = analysis.pattern(store, now - 3600, now + 1, True, 0.0, 10.0, antenna_alt_m=200)   # élévation ≈ 4,7°
        h = d['horizontal']
        east = [v for a, v in zip(h['az'], h['values']) if 60 <= a <= 120 and v is not None]
        west = [v for a, v in zip(h['az'], h['values']) if 240 <= a <= 300 and v is not None]
        diff = sum(east) / len(east) - sum(west) / len(west)
        self.assertAlmostEqual(diff, 6.0, delta=1.0)


if __name__ == '__main__':
    unittest.main()
