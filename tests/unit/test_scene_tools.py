from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import dm3_mcp.server as srv
from dm3_mcp.state.scenes import SceneStore


@pytest.fixture
def fresh_store(tmp_path: Path, monkeypatch):
    store = SceneStore(tmp_path / "scenes.json")
    monkeypatch.setattr(srv, "_scene_store", store)
    return store


@pytest.mark.asyncio
async def test_store_and_recall_scene(monkeypatch, fresh_store):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    srv._cache.record_set("MIXER:Current/InCh/Label/Name", 0, 0, "Kick")

    stored = await srv.store_current_as_scene("A", 5, "Test", description="")
    assert stored["ok"] is True
    assert stored["console_store"] is False  # M0: no scene-store via RCP

    recalled = await srv.recall_scene("A", 5)
    assert recalled["ok"] is True
    assert recalled["metadata"]["name"] == "Test"


@pytest.mark.asyncio
async def test_recall_by_name_ambiguous(monkeypatch, fresh_store):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    fresh_store.upsert("A", 1, name="Podcast solo")
    fresh_store.upsert("A", 2, name="Podcast duo")
    result = await srv.recall_scene_by_name("Podcast")
    assert result["ok"] is False
    assert result["error"]["code"] == "ambiguous"


@pytest.mark.asyncio
async def test_list_scenes_filter(monkeypatch, fresh_store):
    fresh_store.upsert("A", 1, name="Worship")
    fresh_store.upsert("B", 1, name="Theater")
    result = await srv.list_scenes(bank="A")
    assert result["ok"] is True
    assert len(result["scenes"]) == 1
    assert result["scenes"][0]["name"] == "Worship"
