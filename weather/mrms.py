"""MRMS on s3://noaa-mrms-pds: composite reflectivity, precip rate, ProbSevere storm objects. Files every 2 min, ~50 s upload lag."""
import datetime as dt
import gzip
import json
import os

import numpy as np
from scipy import ndimage

from common.geo import distance_km
from weather.grib import read_first_message
from weather.s3 import get_bytes, latest_key, list_keys

BUCKET = "noaa-mrms-pds"
COMPOSITE_REFLECTIVITY = "MergedReflectivityQCComposite_00.50"
PRECIP_RATE = "PrecipRate_00.00"


def product_prefix(product: str, day: dt.date, region: str = "CONUS") -> str:
    return f"{region}/{product}/{day:%Y%m%d}/"


def latest_object(product: str, now: dt.datetime, region: str = "CONUS") -> dict:
    obj = latest_key(BUCKET, product_prefix(product, now.date(), region))
    if obj is None:
        obj = latest_key(BUCKET, product_prefix(product, now.date() - dt.timedelta(days=1), region))
    return obj


def object_at(product: str, when: dt.datetime, region: str = "CONUS") -> dict:
    """The file whose valid time is closest to `when` (files are stamped YYYYMMDD-HHMMSS)."""
    objects = list_keys(BUCKET, product_prefix(product, when.date(), region))
    return min(objects, key=lambda o: abs((valid_time(o["Key"]) - when).total_seconds()))


def valid_time(key: str) -> dt.datetime:
    stamp = key.rsplit("_", 1)[-1].split(".")[0]
    return dt.datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=dt.UTC)


def download_grib(key: str, out_dir: str) -> str:
    path = os.path.join(out_dir, os.path.basename(key).removesuffix(".gz"))
    if not os.path.exists(path):
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(gzip.decompress(get_bytes(BUCKET, key)))
    return path


def read_grid(path: str):
    """(values with missing→NaN, lats, lons)."""
    message, lats, lons = read_first_message(path)
    return np.ma.filled(message.values.astype(float), np.nan), lats, lons


def cells_above(values, lats, lons, threshold_dbz: float = 50.0, min_pixels: int = 10) -> list[dict]:
    """Connected clusters of pixels >= threshold, largest first. Pixels are ~1 km."""
    labels, count = ndimage.label(values >= threshold_dbz, structure=np.ones((3, 3)))
    cells = []
    for i in range(1, count + 1):
        idx = np.where(labels == i)
        if len(idx[0]) < min_pixels:
            continue
        peak = np.argmax(values[idx])
        cells.append({
            "n_px": int(len(idx[0])),
            "peak_dbz": float(values[idx][peak]),
            "peak_lat": float(lats[idx][peak]),
            "peak_lon": float(lons[idx][peak]),
            "lat": float(lats[idx].mean()),
            "lon": float(lons[idx].mean()),
        })
    return sorted(cells, key=lambda c: -c["n_px"])


def max_within(values, lats, lons, lat: float, lon: float, radius_km: float) -> float:
    inside = distance_km(lat, lon, lats, lons) < radius_km
    return float(np.nanmax(values[inside]))


def probsevere_objects(when: dt.datetime) -> list[dict]:
    """Storm objects nearest `when`: polygon centroid + properties (strings; MLON is truncated, so use the centroid)."""
    objects = list_keys(BUCKET, f"ProbSevere/{when:%Y%m%d}/MRMS_PROBSEVERE_{when:%Y%m%d}_{when:%H}")
    if not objects:
        return []
    obj = min(objects, key=lambda o: abs((probsevere_time(o["Key"]) - when).total_seconds()))
    raw = get_bytes(BUCKET, obj["Key"])
    text = gzip.decompress(raw).decode() if obj["Key"].endswith(".gz") else raw.decode()
    features = json.loads(text)["features"]
    for feature in features:
        ring = np.array(feature["geometry"]["coordinates"][0])
        feature["properties"]["centroid_lat"] = float(ring[:, 1].mean())
        feature["properties"]["centroid_lon"] = float(ring[:, 0].mean())
        feature["properties"]["valid_time"] = probsevere_time(obj["Key"]).isoformat()
    return features


def probsevere_time(key: str) -> dt.datetime:
    stamp = "_".join(key.rsplit("_", 2)[-2:]).split(".")[0]
    return dt.datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=dt.UTC)


if __name__ == "__main__":
    now = dt.datetime.now(dt.UTC)
    obj = latest_object(COMPOSITE_REFLECTIVITY, now)
    print(f"latest composite: {obj['Key']} valid {valid_time(obj['Key']):%H:%M:%S}Z age {(now - valid_time(obj['Key'])).total_seconds() / 60:.1f} min")
    values, lats, lons = read_grid(download_grib(obj["Key"], "/tmp/mrms"))
    for cell in cells_above(values, lats, lons)[:5]:
        print(cell)
