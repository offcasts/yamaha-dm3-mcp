import pytest

from dm3_mcp.rcp.codec import (
    encode_get,
    encode_set,
    encode_sscurrent,
    encode_ssrecall,
    parse_response,
)


def test_encode_set_integer():
    line = encode_set("MIXER:Current/InCh/Fader/Level", 0, 0, -1000)
    assert line == "set MIXER:Current/InCh/Fader/Level 0 0 -1000\n"


def test_encode_set_string_simple():
    line = encode_set("MIXER:Current/InCh/Label/Name", 0, 0, "Kick")
    assert line == 'set MIXER:Current/InCh/Label/Name 0 0 "Kick"\n'


def test_encode_set_string_with_spaces():
    line = encode_set("MIXER:Current/InCh/Label/Name", 0, 0, "Lead Vox")
    assert line == 'set MIXER:Current/InCh/Label/Name 0 0 "Lead Vox"\n'


def test_encode_get():
    assert encode_get("MIXER:Current/InCh/Fader/Level", 0, 0) == "get MIXER:Current/InCh/Fader/Level 0 0\n"


def test_encode_ssrecall_a():
    assert encode_ssrecall("A", 5) == "ssrecall_ex scene_a 5\n"


def test_encode_ssrecall_b():
    assert encode_ssrecall("B", 99) == "ssrecall_ex scene_b 99\n"


def test_encode_ssrecall_bad_bank():
    with pytest.raises(ValueError):
        encode_ssrecall("C", 1)


def test_encode_sscurrent():
    assert encode_sscurrent("A") == "sscurrent_ex scene_a\n"


def test_parse_ok_set():
    r = parse_response("OK set MIXER:Current/InCh/Fader/Level 0 0 -1000")
    assert r.kind == "ok"
    assert r.address == "MIXER:Current/InCh/Fader/Level"
    assert r.x == 0 and r.y == 0
    assert r.value == -1000


def test_parse_ok_set_with_display_string():
    """M0: real DM3 includes a trailing display-string token."""
    r = parse_response('OK set MIXER:Current/InCh/Fader/Level 0 0 -1000 "-10.00"')
    assert r.kind == "ok"
    assert r.value == -1000


def test_parse_okm_clamped():
    r = parse_response('OKm set MIXER:Current/InCh/Fader/Level 0 0 1000 "10.00"')
    assert r.kind == "okm"
    assert r.value == 1000


def test_parse_notify():
    r = parse_response('NOTIFY set MIXER:Current/InCh/Fader/Level 5 0 -500 "-5.00"')
    assert r.kind == "notify"
    assert r.x == 5
    assert r.value == -500


def test_parse_get_with_string_value():
    r = parse_response('OK get MIXER:Current/InCh/Label/Name 0 0 "Lead Vox"')
    assert r.kind == "get"
    assert r.value == "Lead Vox"


def test_parse_error():
    r = parse_response("ERROR parameter out of range")
    assert r.kind == "error"
    assert r.message == "parameter out of range"
