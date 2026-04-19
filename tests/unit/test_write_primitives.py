from unittest.mock import AsyncMock

import pytest

import dm3_mcp.server as srv


@pytest.mark.asyncio
async def test_set_channel_label_name(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    result = await srv.set_channel_label("inch", 1, name="Kick")
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/InCh/Label/Name", 0, 0, "Kick")


@pytest.mark.asyncio
async def test_set_channel_label_multiple(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    result = await srv.set_channel_label("inch", 3, name="Snare", color="Red")
    assert result["ok"] is True
    assert mock_client.set.await_count == 2


@pytest.mark.asyncio
async def test_set_fader_level_normal(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"

    result = await srv.set_fader_level("inch", 1, -10.0)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)


@pytest.mark.asyncio
async def test_set_fader_level_above_clamp_rejected(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"
    srv._safety.max_fader_db = 6.0

    result = await srv.set_fader_level("inch", 1, 10.0)
    assert result["ok"] is False
    assert result["error"]["code"] == "safety"
    mock_client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_fader_level_override(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    srv._safety.mode = "limited"
    srv._safety.max_fader_db = 6.0

    result = await srv.set_fader_level("inch", 1, 10.0, override_safety=True)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_fader_neg_inf(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    result = await srv.set_fader_level("inch", 1, float("-inf"))
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/InCh/Fader/Level", 0, 0, -32768)


@pytest.mark.asyncio
async def test_set_channel_on(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)

    result = await srv.set_channel_on("inch", 2, False)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/InCh/Fader/On", 1, 0, 0)


@pytest.mark.asyncio
async def test_set_mute_group(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_mute_group(3, True)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("MIXER:Current/MuteGrpCtrl/On", 2, 0, 1)


@pytest.mark.asyncio
async def test_set_head_amp_gain(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_head_amp_gain(5, 30)
    assert result["ok"] is True
    mock_client.set.assert_awaited_once_with("IO:Current/InCh/HAGain", 4, 0, 30)


@pytest.mark.asyncio
async def test_set_head_amp_gain_out_of_range(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_head_amp_gain(1, 70)
    assert result["ok"] is False
    mock_client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_send_level_and_on(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_send("inch", 1, "mix", 1, level_db=0.0, on=True)
    assert result["ok"] is True
    assert mock_client.set.await_count == 2


@pytest.mark.asyncio
async def test_set_send_unknown_route(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.set_send("fx", 1, "mix", 1, level_db=0.0)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_emergency_mute_all(monkeypatch):
    mock_client = AsyncMock()
    monkeypatch.setattr(srv, "_client", mock_client)
    result = await srv.emergency_mute_all(True)
    assert result["ok"] is True
    # 16 input + 2 stereo + 4 FX = 22 writes
    assert mock_client.set.await_count == 22
