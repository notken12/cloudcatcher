"""Name -> adapter class. Add a line here when you write a new source."""

from __future__ import annotations

from .sources.alertca import AlertCaliforniaAdapter
from .sources.caltrans import CaltransAdapter
from .sources.cars import NewYork511Adapter, Ontario511Adapter
from .sources.cars_gql import CARS_GQL_ADAPTERS
from .sources.cars_list import CARS_LIST_ADAPTERS
from .sources.digitraffic import DigitrafficAdapter
from .sources.drivebc import DriveBCAdapter
from .sources.fotowebcam import FotoWebcamAdapter
from .sources.hongkong import HongKongTDAdapter
from .sources.iceland import IcelandAdapter
from .sources.iem import IEMAdapter
from .sources.manual import ManualAdapter
from .sources.ndbc import NDBCAdapter
from .sources.nzta import NZTAAdapter
from .sources.panomax import PanomaxAdapter
from .sources.phenocam import PhenoCamAdapter
from .sources.singapore import SingaporeLTAAdapter
from .sources.taiwan import TaiwanTDXAdapter
from .sources.tfl import TfLJamCamAdapter
from .sources.tripcheck import TripCheckAdapter
from .sources.vegvesen import VegvesenAdapter
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
        DriveBCAdapter,
        TripCheckAdapter,
        NZTAAdapter,
        HongKongTDAdapter,
        SingaporeLTAAdapter,
        TaiwanTDXAdapter,
        TfLJamCamAdapter,
        VegvesenAdapter,
        *CARS_LIST_ADAPTERS,  # cars_fl, cars_ut, ... (keyless 511 list endpoint)
        *CARS_GQL_ADAPTERS,  # cars_mn, cars_ia, ... (OneWeb GraphQL portals)
        WindyAdapter,  # needs WINDY_API_KEY
    )
}
