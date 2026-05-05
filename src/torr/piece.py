import logging
import os
import tempfile
from pathlib import Path

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
    pieces: list[Piece] = []

    for i, start in enumerate(range(0, file_size, piece_size)):
        end = min(start + piece_size, file_size)
        pieces.append(Piece(i, end - start))

    return pieces


class DiskManager:
    def __init__(self, output_directory: str, torrent):
        self.output_directory = Path(output_directory)
        self.torrent = torrent
        self.written = 0
        self.multi_part = "files" in self.torrent.info.keys()
        logging.getLogger("BitTorrent").debug(f"DiskManager output directory is {output_directory}")

        # Ensure output directory exists
        os.makedirs(output_directory, exist_ok=True)

        if self.multi_part:
            self.file = tempfile.TemporaryFile()
        else:
            # Update to use output_directory for single file torrent
            file_path = self.output_directory / self.torrent.file_name
            self.file = open(file_path, "wb")

    def write_piece(self, piece, piece_size):
        """
        Write piece to disk according to the offset
        """
        piece_data = piece.get_data()
        self.file.seek(piece_size * piece.index)
        self.file.write(piece_data)
        self.file.flush()

        self.written += 1

    def close(self):
        """
        Reorganize the pieces according to the
        Files structure specified in the torent file
        """
        # If torrent contain multiple file, split them
        if self.multi_part:
            self.file.seek(0)
            for file in self.torrent.info["files"]:
                # Calculate the full path of each file including the output_directory
                file_path: Path = Path(self.output_directory) / self.torrent.file_name
                for entity in file["path"]:
                    file_path: Path = file_path / entity

                logging.getLogger("BitTorrent").debug(f"Def close file path is {file_path}")
                logging.getLogger("BitTorrent").debug(f"Diskmanager output directory is {file_path}")
                os.makedirs(file_path.parent, exist_ok=True)
                logging.getLogger("BitTorrent").debug(f"Writing data in offsets {self.file.tell()}:{file['length']}")

                # Create the file and copy the data
                f = open(file_path, "wb")
                file_data = self.file.read(file["length"])
                f.write(file_data)
                f.close()

        self.file.close()
