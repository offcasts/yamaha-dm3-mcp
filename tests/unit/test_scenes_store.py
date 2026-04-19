from pathlib import Path

from dm3_mcp.state.scenes import SceneStore


def test_roundtrip_store(tmp_path: Path):
    f = tmp_path / "scenes.json"
    s = SceneStore(f)
    s.upsert("A", 5, name="Worship band", description="Acoustic + bass", tags=["worship"])
    s.flush()

    s2 = SceneStore(f)
    s2.load()
    e = s2.get("A", 5)
    assert e is not None
    assert e["name"] == "Worship band"
    assert e["tags"] == ["worship"]


def test_list_filters_by_query(tmp_path: Path):
    f = tmp_path / "scenes.json"
    s = SceneStore(f)
    s.upsert("A", 1, name="Podcast solo")
    s.upsert("A", 2, name="Podcast duo")
    s.upsert("A", 3, name="Theater")
    s.flush()

    matches = s.list_(query="Podcast")
    assert len(matches) == 2


def test_atomic_write_does_not_corrupt(tmp_path: Path):
    f = tmp_path / "scenes.json"
    s = SceneStore(f)
    s.upsert("A", 1, name="One")
    s.flush()
    s2 = SceneStore(f)
    s2.load()
    assert s2.get("A", 1)["name"] == "One"
