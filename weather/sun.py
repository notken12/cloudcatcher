import datetime as dt

from astral import Observer, SunDirection
from astral.sun import azimuth, elevation, golden_hour, sunset


def sunset_utc(lat: float, lon: float, local_date: dt.date, utc_offset_hours: float) -> dt.datetime:
    """Sunset on the given *local* calendar date, returned in UTC."""
    tz = dt.timezone(dt.timedelta(hours=utc_offset_hours))
    return sunset(Observer(latitude=lat, longitude=lon), local_date, tzinfo=tz).astimezone(dt.UTC)


def golden_hour_minutes(lat: float, lon: float, local_date: dt.date, utc_offset_hours: float) -> float:
    """Length of the evening golden hour (sun from +6 to -4 deg) on the given local date."""
    tz = dt.timezone(dt.timedelta(hours=utc_offset_hours))
    start, end = golden_hour(Observer(latitude=lat, longitude=lon), local_date, SunDirection.SETTING, tzinfo=tz)
    return (end - start).total_seconds() / 60


def sun_azimuth(lat: float, lon: float, when: dt.datetime) -> float:
    return azimuth(Observer(latitude=lat, longitude=lon), when)


def sun_elevation(lat: float, lon: float, when: dt.datetime) -> float:
    return elevation(Observer(latitude=lat, longitude=lon), when)
