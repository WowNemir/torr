from torr.block import Block, BlockStatus, create_blocks
from torr.exceptions import PieceIsFull, PieceIsPending


class Piece:
    def __init__(self, index, size):
        self.index = index
        self.size = size
        self.blocks: list[Block] = create_blocks(self.size)

    def __str__(self):
        return f"[{self.index}]"

    def is_full(self):
        """
        Iterate over the blocks and
        check if they are all fulls
        """
        for block in self.blocks:
            if block.status != BlockStatus.FULL:
                return False

        return True

    def get_free_block(self) -> Block | None:
        """
        Iterate over the blocks and
        check if of them is free
        """
        for block in self.blocks:
            block.calculate_status()
            if block.status == BlockStatus.FREE:
                return block

        if self.is_full():
            raise PieceIsFull
        else:
            raise PieceIsPending

    def get_block_by_offset(self, offset):
        """
        Iterate over the blocks and check if
        one of them match the given offset
        """
        for block in self.blocks:
            if block.offset == offset:
                return block

        raise PieceIsPending

    def get_data(self):
        """
        Concat the data in all the blocks to
        retrieve the full data of the piece
        """
        data = b""
        for block in self.blocks:
            data += block.data

        return data


def create_pieces(file_size, piece_size) -> list[Piece]:
    """
    Create list of empty pieces for the given
    file_size and the given piece_size
    """
    pieces: list[Piece] = []
    pieces_amount = int(file_size / piece_size)

    # Generate pieces
    for i in range(pieces_amount):
        piece = Piece(i, piece_size)
        pieces.append(piece)

    last_piece_size = file_size % piece_size

    if last_piece_size:
        last_piece = Piece(pieces_amount, last_piece_size)
        pieces.append(last_piece)

    return pieces
