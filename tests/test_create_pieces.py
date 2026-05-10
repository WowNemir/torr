from torr.oop.piece import Piece, create_pieces


def test_create_piece():
    p = Piece(5, 100)
    assert p
    assert p.size == 100
    assert p.index == 5


def test_create_pieces():
    class MockTorrentFile:
        def __init__(self, size, piece_size):
            self.length = size
            self.piece_size = piece_size

    pieces = create_pieces(MockTorrentFile(1000, 10))
    assert len(pieces) == 100

    pieces = create_pieces(MockTorrentFile(1001, 10))
    assert len(pieces) == 101
    assert pieces[-1].size == 1
