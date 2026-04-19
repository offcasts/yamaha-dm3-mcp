import math

import pytest

from dm3_mcp.rcp.types import db_to_raw, raw_to_db


def test_zero_db():
    assert db_to_raw(0.0) == 0
    assert raw_to_db(0) == 0.0


def test_positive_db():
    assert db_to_raw(10.0) == 1000
    assert raw_to_db(1000) == 10.0


def test_negative_db():
    assert db_to_raw(-13.8) == -1380
    assert raw_to_db(-1380) == -13.8


def test_neg_infinity():
    assert db_to_raw(float("-inf")) == -32768
    assert math.isinf(raw_to_db(-32768)) and raw_to_db(-32768) < 0


def test_clamp_above_max():
    with pytest.raises(ValueError):
        db_to_raw(20.0, min_raw=-32768, max_raw=1000)


def test_clamp_below_min():
    # default min_raw=-13800; -200 dB -> -20000 raw is below the default floor
    with pytest.raises(ValueError):
        db_to_raw(-200.0)
