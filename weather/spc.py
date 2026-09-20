"""SPC storm reports. Daily filtered CSV (hail/wind/tornado sections) and yearly CSVs (times in CST)."""
import csv
import datetime as dt
import io

import requests

CST_TO_UTC = dt.timedelta(hours=6)


def daily_reports(day: dt.date) -> list[dict]:
    url = f"https://www.spc.noaa.gov/climo/reports/{day:%y%m%d}_rpts_filtered.csv"
    section = None
    reports = []
    for line in requests.get(url, timeout=60).text.splitlines():
        parts = line.split(",")
        if parts[0] == "Time":
            section = parts[1].lower()
            continue
        if len(parts) < 8 or not parts[0].isdigit():
            continue
        time = dt.datetime.combine(day, dt.time(int(parts[0][:2]), int(parts[0][2:]))).replace(tzinfo=dt.UTC)
        if time.hour < 12:
            time += dt.timedelta(days=1)
        reports.append({"kind": section, "time": time, "mag": parts[1], "lat": float(parts[5]), "lon": float(parts[6]), "comment": parts[7]})
    return reports


def yearly_reports(year: int, kinds=("hail", "wind", "torn")) -> list[dict]:
    reports = []
    for kind in kinds:
        text = requests.get(f"https://www.spc.noaa.gov/wcm/data/{year}_{kind}.csv", timeout=120).text
        for row in csv.DictReader(io.StringIO(text)):
            time = dt.datetime.strptime(row["date"] + " " + row["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.UTC) + CST_TO_UTC
            reports.append({"kind": kind, "time": time, "mag": float(row["mag"]), "lat": float(row["slat"]), "lon": float(row["slon"]), "state": row["st"]})
    return reports


if __name__ == "__main__":
    reports = yearly_reports(2025, kinds=("torn",))
    print(len(reports), "tornado reports in 2025; first:", reports[0])
