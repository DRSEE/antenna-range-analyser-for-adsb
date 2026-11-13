"""Configuration : valeurs par défaut, surchargées par config.local.json (non versionné)."""
import copy
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(ROOT, 'config.local.json')

DEFAULTS = {
    'readsb_dir': '/run/readsb',            # aircraft.json, receiver.json, stats.json
    'tar1090_dir': '/run/tar1090',          # historique (import de la portée au démarrage)
    'antenna_alt_m': None,                  # altitude de l'antenne (sol + mât), en mètres au-dessus de la mer
    'geoid_offset_m': 0.0,                  # alt_geom (WGS84) − altitude ; ≈ 50 m en France, négligeable au-delà de 10 km
    'sample_interval_s': 10,                # un échantillon par avion toutes les N secondes
    'retention_days': 7,                    # échantillons hors campagne conservés N jours
    'host': '0.0.0.0',
    'port': 5002,
    'reference': {                          # référence externe (probabilité de détection), désactivée par défaut
        'enabled': False,
        'url': 'https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}',
        'radius_nm': 150,
        'interval_s': 60,
    },
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load():
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(LOCAL):
        with open(LOCAL, encoding='utf-8') as f:
            _merge(cfg, json.load(f))
    cfg['data_dir'] = os.path.join(ROOT, 'data')
    os.makedirs(cfg['data_dir'], exist_ok=True)
    return cfg


def receiver_position(cfg):
    """Position du récepteur lue dans receiver.json de readsb (jamais écrite dans le dépôt)."""
    try:
        with open(os.path.join(cfg['readsb_dir'], 'receiver.json'), encoding='utf-8') as f:
            r = json.load(f)
        return r.get('lat'), r.get('lon')
    except (OSError, ValueError):
        return None, None
