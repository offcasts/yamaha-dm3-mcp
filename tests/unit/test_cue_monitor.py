from unittest.mock import AsyncMock

import pytest

import dm3_mcp.server as srv


@pytest.mark.asyncio
async def test_set_cue_inch(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_cue("inch", 1, on=True)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/Cue/InCh/On", 0, 0, 1)


@pytest.mark.asyncio
async def test_set_cue_exclusive_clears_others(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_cue("inch", 3, on=True, exclusive=True)
    assert result["ok"] is True
    # 16+2+4+6+2+2+2 = 34 cue slots; exclusive clears 33 (all but our target)
    # plus 1 set for the target itself = 34 awaits.
    assert mock_client.set.await_count == 34
    assert result["exclusive_cleared"] == 33


@pytest.mark.asyncio
async def test_set_cue_bad_target_type(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_cue("drums", 1)
    assert result["ok"] is False
    mock_client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_cue_out_of_range(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_cue("mix", 99)
    assert result["ok"] is False
    mock_client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_all_cues(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.clear_all_cues()
    assert result["ok"] is True
    # Clears 16+2+4+6+2+2+2 = 34 slots
    assert result["cleared_count"] == 34
    assert mock_client.set.await_count == 34


@pytest.mark.asyncio
async def test_set_cue_mode_partial(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_cue_mode(in_point="AFL")
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/Cue/InCh/Point", 0, 0, "AFL")


@pytest.mark.asyncio
async def test_set_monitor_full(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"
    result = await srv.set_monitor(on=True, level_db=-10.0, mono=True, cue_interrupts=False)
    assert result["ok"] is True
    # 4 writes
    assert mock_client.set.await_count == 4


@pytest.mark.asyncio
async def test_set_monitor_clamp(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"
    srv._safety.max_fader_db = 6.0
    result = await srv.set_monitor(level_db=10.0)
    assert result["ok"] is False
    assert result["error"]["code"] == "safety"


@pytest.mark.asyncio
async def test_set_monitor_source_exclusive(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_monitor_source("mix", 1, on=True, exclusive=True)
    assert result["ok"] is True
    # 6+2+2+1+1 = 12 sources; exclusive clears 11 + 1 set target = 12
    assert mock_client.set.await_count == 12
    assert result["exclusive_cleared"] == 11
