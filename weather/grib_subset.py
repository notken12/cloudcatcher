"""Byte-range subsets of GRIB2 files on unsigned S3 buckets (HRRR, GFS): the .idx sidecar gives each
message's offset, so only the wanted fields are fetched (~10 MB instead of hundreds)."""
import os

from weather.s3 import client, get_bytes


def exists(bucket: str, key: str) -> bool:
    response = client().list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
    return any(o["Key"] == key for o in response.get("Contents", []))


def read_index(bucket: str, key: str) -> list[tuple[int, int, str, str, str]]:
    """(message number, byte offset, variable, level, forecast descriptor) per line of the .idx file."""
    rows = []
    for line in get_bytes(bucket, key + ".idx").decode().strip().split("\n"):
        number, offset, _, variable, level, forecast, *_ = line.split(":")
        rows.append((int(number), int(offset), variable, level, forecast))
    return rows


def byte_ranges(index, wanted: list[tuple[str, str]]) -> list[tuple[int, int | None]]:
    """Ranges of the wanted (variable, level) messages, instantaneous ones only (GFS also carries
    "0-6 hour ave" copies of the cloud layers under the same variable and level)."""
    ranges = []
    for i, (_, offset, variable, level, forecast) in enumerate(index):
        if (variable, level) not in wanted or "-" in forecast:
            continue
        end = index[i + 1][1] - 1 if i + 1 < len(index) else None
        ranges.append((offset, end))
    return ranges


def download_subset(bucket: str, key: str, out_dir: str, fields: list[tuple[str, str]]) -> str:
    """Cached per field count, so a subset downloaded with an older field list is not reused."""
    path = os.path.join(out_dir, os.path.basename(key).replace(".grib2", "") + f".subset{len(fields)}.grib2")
    if os.path.exists(path):
        return path
    os.makedirs(out_dir, exist_ok=True)
    parts = [get_bytes(bucket, key, r) for r in byte_ranges(read_index(bucket, key), fields)]
    with open(path, "wb") as f:
        f.write(b"".join(parts))
    return path
