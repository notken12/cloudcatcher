import boto3
from botocore import UNSIGNED
from botocore.config import Config

_client = None


def client():
    global _client
    if _client is None:
        _client = boto3.client("s3", config=Config(signature_version=UNSIGNED), region_name="us-east-1")
    return _client


def list_keys(bucket: str, prefix: str) -> list[dict]:
    """All objects under a prefix (paginated), sorted by key."""
    paginator = client().get_paginator("list_objects_v2")
    objects = [o for page in paginator.paginate(Bucket=bucket, Prefix=prefix) for o in page.get("Contents", [])]
    return sorted(objects, key=lambda o: o["Key"])


def latest_key(bucket: str, prefix: str) -> dict | None:
    objects = list_keys(bucket, prefix)
    return objects[-1] if objects else None


def get_bytes(bucket: str, key: str, byte_range: tuple[int, int | None] | None = None) -> bytes:
    kwargs = {"Bucket": bucket, "Key": key}
    if byte_range:
        start, end = byte_range
        kwargs["Range"] = f"bytes={start}-" if end is None else f"bytes={start}-{end}"
    return client().get_object(**kwargs)["Body"].read()
