import io

import pytest

from torr.bcoder import bdecode, bencode


def test_decode_types():
    assert type(bdecode(io.BytesIO(b"i0e"))) in [int, str, list, dict]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("i0e", 0),
        ("i42e", 42),
        ("i-42e", -42),
        ("0:", b""),
        ("7:bencode", b"bencode"),
        ("le", []),
        ("l7:bencodei-20ee", [b"bencode", -20]),
        ("llee", [[]]),
        ("llleee", [[[]]]),
        ("li0eli0ei0ei0eee", [0, [0, 0, 0]]),
        ("de", {}),
        ("d7:meaningi42e4:wiki7:bencodee", {b"meaning": 42, b"wiki": b"bencode"}),
        ("d1:adee", {b"a": {}}),
        ("d1:ade1:blee", {b"a": {}, b"b": []}),
    ],
)
def test_decode_bytes_mode(raw, expected):
    assert bdecode(io.BytesIO(raw.encode())) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("i0e", 0),
        ("i42e", 42),
        ("i-42e", -42),
        ("0:", ""),
        ("7:bencode", "bencode"),
        ("le", []),
        ("l7:bencodei-20ee", ["bencode", -20]),
        ("llee", [[]]),
        ("llleee", [[[]]]),
        ("li0eli0ei0ei0eee", [0, [0, 0, 0]]),
        ("de", {}),
        ("d7:meaningi42e4:wiki7:bencodee", {"meaning": 42, "wiki": "bencode"}),
        ("d1:adee", {"a": {}}),
        ("d1:ade1:blee", {"a": {}, "b": []}),
    ],
)
def test_decode_str_mode(raw, expected):
    assert bdecode(io.BytesIO(raw.encode()), mode="str") == expected


@pytest.mark.parametrize(
    "expected,raw",
    [
        (0, "i0e"),
        (42, "i42e"),
        (-42, "i-42e"),
        (b"", "0:"),
        (b"bencode", "7:bencode"),
        ("bencode", "7:bencode"),
        ([], "le"),
        ([b"bencode", -20], "l7:bencodei-20ee"),
        ([[]], "llee"),
        ([[[]]], "llleee"),
        ([0, [0, 0, 0]], "li0eli0ei0ei0eee"),
        ({}, "de"),
        ({b"meaning": 42, b"wiki": b"bencode"}, "d7:meaningi42e4:wiki7:bencodee"),
        ({b"a": {}}, "d1:adee"),
        ({b"a": {}, b"b": []}, "d1:ade1:blee"),
    ],
)
def test_encode(raw, expected):
    assert bencode(expected) == raw.encode()


def test_decode_unknown_token():
    with pytest.raises(ValueError, match="Unknown token"):
        bdecode(io.BytesIO(b"x123"))


def test_decode_invalid_utf8_fallback():
    # 2 bytes that are invalid UTF-8
    raw = b"2:\xff\xff"

    result = bdecode(io.BytesIO(raw), mode="str")

    assert isinstance(result, bytes)
    assert result == b"\xff\xff"


def test_encode_float_unsupported():
    with pytest.raises(
        ValueError,
        match=r"Unsupported type for bencoding: float",
    ):
        bencode(3.14)
