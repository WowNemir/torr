import re

from torr.utils import generate_peer_id


def test_peer_id_is_bytes():
    peer_id = generate_peer_id()
    assert isinstance(peer_id, bytes)


def test_peer_id_length():
    peer_id = generate_peer_id()
    assert len(peer_id) == 20


def test_peer_id_prefix_format():
    peer_id = generate_peer_id().decode()

    # Matches -TrXXXX- where X = digit
    assert re.match(r"^-Tr\d{4}-", peer_id)


def test_peer_id_randomness():
    peer_id1 = generate_peer_id()
    peer_id2 = generate_peer_id()

    assert peer_id1 != peer_id2  # very unlikely to fail
