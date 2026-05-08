import logging
import os
import tempfile
import time
from enum import Enum
from pathlib import Path


class BlockStatus(Enum):
    FREE = 1
    REQUESTED = 2
    FULL = 3


class Block:
    default_size = 16384
    max_waiting_time = 7

    def __init__(self, offset, size=default_size):
        self.status = BlockStatus.FREE
        self.offset = offset
        self.size = size
        self.data = b""
        self.time_requested = 0  # Used for determine block status

    def set_requested(self):
        self.time_requested = time.time()
        self.status = BlockStatus.REQUESTED

    def calculate_status(self):
        """
        Check if the block status should change from
        REQUESTED to FREE if the max waiting time passed.
        """
        if self.status == BlockStatus.REQUESTED:
            duration_waited = time.time() - self.time_requested
            if duration_waited > Block.max_waiting_time:
                self.status = BlockStatus.FREE


def create_blocks(piece_size) -> list[Block]:
    """
    Create blocks according to blocks_length parameter
    """
    blocks: list[Block] = []
    blocks_amount = int(piece_size / Block.default_size)
    for i in range(blocks_amount):
        block = Block(i * Block.default_size)
        blocks.append(block)

    # Check if there is left over bytes
    last_block_size = piece_size % Block.default_size

    # The size of the last block will be the left over
    if last_block_size:
        last_block = Block(blocks_amount * Block.default_size, last_block_size)
        blocks.append(last_block)

    return blocks


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
        for block in self.blocks:
            block.calculate_status()
            if block.status == BlockStatus.FREE:
                return block

    def get_block_by_offset(self, offset) -> Block | None:
        """
        Iterate over the blocks and check if
        one of them match the given offset
        """
        for block in self.blocks:
            if block.offset == offset:
                return block

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
