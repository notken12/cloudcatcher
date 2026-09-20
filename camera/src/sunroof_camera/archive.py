"""Archive ("time travel") frames: a Camera for any `source:key` id of a source with a deep
per-timestamp archive, so `/proxy/history` works even for cameras missing from the catalog.

Only sources whose adapters resolve a frame from a timestamp alone are listed here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .ingest.sources.fotowebcam import FotoWebcamAdapter
from .ingest.sources.iem import ARCHIVE as IEM_ARCHIVE
from .ingest.sources.iem import IEMAdapter
from .ingest.sources.phenocam import ARCHIVE as PHENOCAM_ARCHIVE
from .ingest.sources.phenocam import LATEST as PHENOCAM_LATEST
from .ingest.sources.phenocam import PhenoCamAdapter
from .schema import Camera

ARCHIVE_SOURCES = (FotoWebcamAdapter.source, PhenoCamAdapter.source, IEMAdapter.source)
# Archive paths are camera-local wall-clock time except for these, which are UTC.
ARCHIVE_UTC_SOURCES = frozenset({IEMAdapter.source})


def archive_time(source: str, ts: datetime) -> datetime:
    """Naive time to feed the adapter for an aware (or naive = already local) instant."""
    if ts.tzinfo is not None and source in ARCHIVE_UTC_SOURCES:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.replace(tzinfo=None)


def camera_from_id(camera_id: str) -> Camera | None:
    source, _, key = camera_id.partition(":")
    if not key or source not in ARCHIVE_SOURCES:
        return None
    common = dict(id=camera_id, source=source, source_kind="jpeg", name=key, lat=0.0, lon=0.0)
    if source == FotoWebcamAdapter.source:
        return Camera(
            **common,
            image_url=f"https://www.foto-webcam.eu/webcam/{key}/current/1200.jpg",
            page_url=f"https://www.foto-webcam.eu/webcam/{key}/",
            history_kind="url_template",
            history_template=f"https://www.foto-webcam.eu/webcam/{key}/%Y/%m/%d/%H%M_la.jpg",
            history_depth_days=3650,
            attribution="foto-webcam.eu",
        )
    if source == PhenoCamAdapter.source:
        return Camera(
            **common,
            image_url=PHENOCAM_LATEST.format(site=key),
            page_url=f"https://phenocam.nau.edu/webcam/sites/{key}/",
            history_kind="url_template",
            history_template=PHENOCAM_ARCHIVE.format(site=key),
            history_depth_days=6000,
            attribution="PhenoCam Network, Northern Arizona University",
        )
    return Camera(
        **common,
        page_url=f"https://mesonet.agron.iastate.edu/current/webcam.php?cid={key}",
        history_kind="api",
        history_template=IEM_ARCHIVE,
        history_depth_days=5000,
        attribution="Iowa Environmental Mesonet / partner TV stations",
    )
