import pytest

from sunroof_camera import vlm


@pytest.fixture(autouse=True)
def no_vlm(monkeypatch):
    """Tests never talk to a VLM (a local Ollama or a provisioned key would be auto-detected)."""
    monkeypatch.setenv("SUNROOF_VLM_BACKEND", "off")
    vlm.backend.cache_clear()
    yield
    vlm.backend.cache_clear()
