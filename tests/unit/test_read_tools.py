import pytest

import dm3_mcp.server as srv


@pytest.mark.asyncio
async def test_get_channel_state_bad_range():
    result = await srv.get_channel_state(0)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_get_channel_state_returns_label_and_fader():
    srv._cache.record_set("MIXER:Current/InCh/Label/Name", 0, 0, "Kick")
    srv._cache.record_set("MIXER:Current/InCh/Fader/Level", 0, 0, -500)
    srv._cache.record_set("MIXER:Current/InCh/Fader/On", 0, 0, 1)
    result = await srv.get_channel_state(1)
    assert result["ok"] is True
    assert result["label"] == "Kick"
    assert result["fader_db"] == -5.0
    assert result["on"] is True


@pytest.mark.asyncio
async def test_get_all_labels_includes_populated():
    srv._cache.record_set("MIXER:Current/InCh/Label/Name", 1, 0, "Snare")
    result = await srv.get_all_labels()
    assert result["ok"] is True
    assert 2 in result["inch"]
    assert result["inch"][2] == "Snare"
