"""Name -> adapter class. Add a line here when you write a new source."""

from __future__ import annotations

from .sources.faa import FaaAdapter
from .sources.manual import ManualAdapter

ADAPTERS: dict[str, type] = {
    ManualAdapter.source: ManualAdapter,
    FaaAdapter.source: FaaAdapter,
}
