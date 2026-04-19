from unittest.mock import AsyncMock

import pytest

import dm3_mcp.server as srv


@pytest.mark.asyncio
async def test_set_mix_exclusive_inputs_enables_two_disables_rest(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"

    result = await srv.set_mix_exclusive_inputs(1, [1, 2], level_db=-3.0)
    assert result["ok"] is True
    assert result["enabled"] == [1, 2]
    assert result["disabled"] == [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    # 16 On writes + 2 Level writes = 18 total
    assert mock_client.set.await_count == 18


@pytest.mark.asyncio
async def test_label_channels_bulk(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.label_channels({"1": {"name": "Kick"}, "2": {"name": "Snare"}})
    assert result["ok"] is True
    assert mock_client.set.await_count == 2


@pytest.mark.asyncio
async def test_configure_mix_bus_combines_steps(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"

    result = await srv.configure_mix_bus(
        1, name="FOH", fader_db=0.0, exclusive_inputs=[1], send_level_db=-6.0
    )
    assert result["ok"] is True
    # 1 label + 1 mix-fader + 16 on-writes + 1 level-write = 19
    assert mock_client.set.await_count == 19
