"""Géométrie et propagation : angle d'élévation, affaiblissement en espace libre, horizon radio."""
import math

R_EARTH_KM = 6371.0
K_FACTOR = 4 / 3            # réfraction atmosphérique standard (rayon terrestre « radio »)
FREQ_MHZ = 1090.0
FT = 0.3048
NM_KM = 1.852


def elevation_deg(dist_km, alt_aircraft_m, alt_antenna_m):
    """Angle d'élévation de l'avion vu de l'antenne (degrés), courbure terrestre et réfraction comprises."""
    if dist_km <= 0:
        return 90.0
    dh_km = (alt_aircraft_m - alt_antenna_m) / 1000.0
    drop_km = dist_km ** 2 / (2 * K_FACTOR * R_EARTH_KM)
    return math.degrees(math.atan2(dh_km - drop_km, dist_km))


def fspl_db(dist_km, freq_mhz=FREQ_MHZ):
    """Affaiblissement de propagation en espace libre (dB)."""
    return 20 * math.log10(max(dist_km, 0.01)) + 20 * math.log10(freq_mhz) + 32.44


def radio_horizon_km(alt_antenna_m, alt_aircraft_m):
    """Portée maximale théorique (horizon radio, k = 4/3) entre l'antenne et un avion."""
    k = math.sqrt(2 * K_FACTOR * R_EARTH_KM / 1000.0)        # ≈ 4,12 km/√m
    return k * (math.sqrt(max(alt_antenna_m, 0)) + math.sqrt(max(alt_aircraft_m, 0)))


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def gain_index_db(rssi_dbfs, dist_km):
    """Indice de gain : RSSI corrigé de l'affaiblissement en espace libre.

    RSSI = P_émise + G_avion + G_antenne − FSPL − pertes − constante du récepteur.
    À puissance d'émission égale (moyennée sur de nombreux avions), RSSI + FSPL ne dépend plus que du gain
    de l'antenne de réception dans la direction de l'avion, à une constante près.
    """
    return rssi_dbfs + fspl_db(dist_km)
