import numpy as np

KM_PER_DEG = 111.0


def distance_km(lat1, lon1, lat2, lon2):
    """Equirectangular distance; accurate to ~1% within a few hundred km. Accepts scalars or numpy arrays."""
    mean_lat = np.radians((np.asarray(lat1) + np.asarray(lat2)) / 2)
    dlat = np.asarray(lat2) - np.asarray(lat1)
    dlon = (np.asarray(lon2) - np.asarray(lon1)) * np.cos(mean_lat)
    return np.hypot(dlat, dlon) * KM_PER_DEG


def bearing_deg(lat_from, lon_from, lat_to, lon_to):
    """Compass bearing 0-360 from one point to another."""
    dlat = np.asarray(lat_to) - np.asarray(lat_from)
    dlon = (np.asarray(lon_to) - np.asarray(lon_from)) * np.cos(np.radians(lat_from))
    return (np.degrees(np.arctan2(dlon, dlat)) + 360) % 360


def angle_diff_deg(a, b):
    """Smallest absolute difference between two bearings, 0-180."""
    return np.abs((np.asarray(a) - np.asarray(b) + 180) % 360 - 180)


def point_along(lat, lon, bearing, distance_km):
    """Point `distance_km` away along `bearing` (flat-earth step; fine for ray sampling)."""
    lat2 = lat + distance_km / KM_PER_DEG * np.cos(np.radians(bearing))
    lon2 = lon + distance_km / KM_PER_DEG * np.sin(np.radians(bearing)) / np.cos(np.radians(lat))
    return lat2, lon2
