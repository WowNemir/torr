import struct

import pytest

from torr.message import (
    Choke,
    Handshake,
    HaveMessage,
    KeepAlive,
    MessageFactory,
    PieceMessage,
    Request,
    Unchoke,
)


@pytest.mark.parametrize(
    "msg, expected",
    [
        (Choke(), struct.pack(">IB", 1, 0)),
        (Unchoke(), struct.pack(">IB", 1, 1)),
    ],
)
def test_simple_to_bytes(msg, expected):
    assert msg.to_bytes() == expected


@pytest.mark.parametrize(
    "index, offset, length",
    [
        (5, 10, 1024),
        (1, 0, 512),
    ],
)
def test_request_to_bytes_and_back(index, offset, length):
    msg = Request(index=index, offset=offset, length=length)
    parsed = Request.from_bytes(msg.to_bytes())

    assert parsed.index == index
    assert parsed.begin == offset
    assert parsed.piece_length == length


@pytest.mark.parametrize(
    "data, should_wait",
    [
        (b"", True),
        (b"abc", False),
    ],
)
def test_piece_should_wait_for_data(data, should_wait):
    msg = PieceMessage(index=1, offset=0, data=data)
    assert msg.should_wait_for_data() is should_wait


def test_piece_from_bytes():
    index = 2
    offset = 100
    payload = struct.pack(">II", index, offset) + b"DATA"

    msg = PieceMessage.from_bytes(payload)

    assert msg.index == index
    assert msg.offset == offset
    assert msg.data == b"DATA"


def test_have_from_bytes():
    index = 42
    payload = struct.pack(">I", index)

    msg = HaveMessage.from_bytes(payload)

    assert msg.index == index


def test_keepalive_to_bytes():
    msg = KeepAlive()
    assert msg.to_bytes() == struct.pack("I", 0)


def test_handshake_to_bytes_and_back():
    peer_id = b"-PC0001-123456789012"
    info_hash = b"12345678901234567890"

    msg = Handshake(peer_id, info_hash)
    parsed = Handshake.from_bytes(msg.to_bytes())

    assert parsed.peer_id == peer_id
    assert parsed.info_hash == info_hash


def test_message_factory():
    msg = MessageFactory.create_message(b"")
    assert isinstance(msg, KeepAlive)

    payload = bytes([7]) + struct.pack(">II", 1, 2) + b"abc"
    msg = MessageFactory.create_message(payload)

    assert isinstance(msg, PieceMessage)
    assert msg.index == 1
    assert msg.offset == 2
    assert msg.data == b"abc"
