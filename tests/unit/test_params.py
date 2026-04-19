from pathlib import Path

import pytest

from dm3_mcp.rcp.params import ParamDef, load_dm3_params


@pytest.fixture
def params():
    path = Path(__file__).parents[2] / "data" / "DM3 Parameters-2.txt"
    return load_dm3_params(path)


def test_load_returns_dict(params):
    assert isinstance(params, dict)
    assert len(params) > 100


def test_fader_level_is_parsed(params):
    fader = params["MIXER:Current/InCh/Fader/Level"]
    assert isinstance(fader, ParamDef)
    assert fader.x_max == 16
    assert fader.y_max == 1
    assert fader.min == -32768
    assert fader.max == 1000
    assert fader.unit == "dB"
    assert fader.type == "integer"
    assert fader.rw == "rw"
    assert fader.scale == 100


def test_read_only_param_detected(params):
    role = params["MIXER:Current/StInCh/Role"]
    assert role.rw == "r"


def test_bool_inferred_from_integer_0_1(params):
    on = params["MIXER:Current/InCh/Fader/On"]
    assert on.type == "bool"
