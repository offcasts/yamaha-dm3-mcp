import pytest

from dm3_mcp.tools.safety import FaderLimitExceeded, SafetyContext


def test_preview_mode_blocks_send():
    ctx = SafetyContext(mode="preview", max_fader_db=6.0)
    assert ctx.should_send() is False


def test_live_mode_allows_send():
    ctx = SafetyContext(mode="live", max_fader_db=6.0)
    assert ctx.should_send() is True


def test_limited_mode_rejects_above_clamp():
    ctx = SafetyContext(mode="limited", max_fader_db=6.0)
    with pytest.raises(FaderLimitExceeded):
        ctx.check_fader_db(7.0)


def test_limited_mode_allows_at_clamp():
    ctx = SafetyContext(mode="limited", max_fader_db=6.0)
    ctx.check_fader_db(6.0)


def test_override_bypasses_clamp():
    ctx = SafetyContext(mode="limited", max_fader_db=6.0)
    ctx.check_fader_db(10.0, override=True)
