#!/usr/bin/env python3
"""Antenna Range Analyser for ADS-B : collecte + page web."""
from threading import Thread

from ara import config
from ara.collector import Collector, import_tar1090, reference_loop
from ara.server import serve
from ara.store import Store

if __name__ == '__main__':
    cfg = config.load()
    store = Store(cfg['data_dir'])
    if cfg['antenna_alt_m'] is None:
        print('⚠️  antenna_alt_m not set: range available, radiation pattern unavailable '
              '(set it in the web page or config.local.json)')
    collector = Collector(cfg, store)
    Thread(target=lambda: print(f'tar1090 history: {import_tar1090(cfg, store)} samples imported'),
           daemon=True).start()
    Thread(target=collector.run, daemon=True).start()
    Thread(target=reference_loop, args=(cfg, store, collector), daemon=True).start()
    serve(cfg, store, collector)
