import numpy as np
import pygrib


def read_first_message(path: str):
    """(values with masked→fill, lats, lons in -180..180) of the first GRIB message."""
    message = pygrib.open(path)[1]
    lats, lons = message.latlons()
    return message, lats, np.where(lons > 180, lons - 360, lons)


def read_messages(path: str) -> dict[tuple[str, str], np.ndarray]:
    """All messages keyed by (shortName, typeOfLevel); masked values become NaN."""
    return {(m.shortName, m.typeOfLevel): np.ma.filled(m.values.astype(float), np.nan) for m in pygrib.open(path)}
