from dm3_mcp.state.cache import StateCache


def test_record_set_stores_value_with_source():
    cache = StateCache()
    cache.record_set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
    entry = cache.get("MIXER:Current/InCh/Fader/Level", 0, 0)
    assert entry.value == -1000
    assert entry.source == "set"
    assert entry.updated_at > 0


def test_record_notify_overwrites():
    cache = StateCache()
    cache.record_set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
    cache.record_notify("MIXER:Current/InCh/Fader/Level", 0, 0, -500)
    entry = cache.get("MIXER:Current/InCh/Fader/Level", 0, 0)
    assert entry.value == -500
    assert entry.source == "notify"


def test_mark_stale_flips_all_sources():
    cache = StateCache()
    cache.record_set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
    cache.record_init("MIXER:Current/InCh/Label/Name", 0, 0, "Kick")
    cache.mark_all_stale()
    assert cache.get("MIXER:Current/InCh/Fader/Level", 0, 0).source == "stale"
    assert cache.get("MIXER:Current/InCh/Label/Name", 0, 0).source == "stale"


def test_missing_entry_returns_none():
    cache = StateCache()
    assert cache.get("MIXER:Current/InCh/Fader/Level", 0, 0) is None


def test_channel_view_exposes_typed_getters():
    cache = StateCache()
    cache.record_set("MIXER:Current/InCh/Fader/Level", 4, 0, -500)
    cache.record_set("MIXER:Current/InCh/Fader/On", 4, 0, 1)
    cache.record_set("MIXER:Current/InCh/Label/Name", 4, 0, "Snare")

    ch = cache.channel(5)
    assert ch.fader_db == -5.0
    assert ch.on is True
    assert ch.label == "Snare"
