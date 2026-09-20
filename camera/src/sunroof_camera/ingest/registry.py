"""Name -> adapter class. Add a line here when you write a new source."""

from __future__ import annotations

from .sources.alertca import AlertCaliforniaAdapter
from .sources.caltrans import CaltransAdapter
from .sources.cars import NewYork511Adapter, Ontario511Adapter
from .sources.digitraffic import DigitrafficAdapter
from .sources.fotowebcam import FotoWebcamAdapter
from .sources.iceland import IcelandAdapter
from .sources.iem import IEMAdapter
from .sources.manual import ManualAdapter
from .sources.ndbc import NDBCAdapter
from .sources.panomax import PanomaxAdapter
from .sources.phenocam import PhenoCamAdapter
from .sources.windy import WindyAdapter

ADAPTERS: dict[str, type] = {
    a.source: a
    for a in (
        ManualAdapter,
        CaltransAdapter,
        Ontario511Adapter,
        NewYork511Adapter,
        PanomaxAdapter,
        DigitrafficAdapter,
        IcelandAdapter,
        AlertCaliforniaAdapter,
        FotoWebcamAdapter,
        PhenoCamAdapter,
        IEMAdapter,
        NDBCAdapter,
        WindyAdapter,  # needs WINDY_API_KEY
    )
}
