from torr.piece import Piece, create_pieces


def test_create_piece():
    p = Piece(5, 100)
    assert p
    assert p.size == 100
    assert p.index == 5


def test_create_pieces():
    pieces = create_pieces(1000, 10)
    assert len(pieces) == 100

    pieces = create_pieces(1001, 10)
    assert len(pieces) == 101
    assert pieces[-1].size == 1
