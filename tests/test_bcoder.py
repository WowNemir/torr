import io
from bcoder import decode, encode
import pytest


def test_decode_types():
    assert type(decode(io.BytesIO(b"i0e"))) in [int, str, list, dict]

@pytest.mark.parametrize("raw,expected", [
    ("i0e", 0),
    ("i42e", 42),
    ("i-42e", -42),
    ("0:", b""),
    ("7:bencode", b"bencode"),
    ("le", []),
    ("l7:bencodei-20ee", [b"bencode", -20]),
    ("llee", [[]]),
    ("llleee", [[[]]]),
    ("li0eli0ei0ei0eee", [0, [0,0,0]]),
    ("de", {}),
    ("d7:meaningi42e4:wiki7:bencodee", {b"meaning": 42, b"wiki":b"bencode"}),
    ("d1:adee", {b'a':{}}),
    ("d1:ade1:blee", {b'a':{}, b'b':[]}),
    ])
def test_decode_int(raw, expected):
    assert decode(io.BytesIO(raw.encode())) == expected

    assert encode(expected) == raw.encode()
