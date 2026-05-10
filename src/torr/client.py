import logging
import random
import string
import time
from importlib import metadata
from threading import Thread

from torr.config import CONFIGURATION
from torr.message import (
    PieceMessage,
)
from torr.piece import Block, BlockStatus, Piece, create_pieces
from torr.storage import DiskManager
from torr.swarm import Swarm
from torr.torrent_file import TorrentFile
from torr.tracker import Tracker, TrackerFactory

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

SHORT_RETRY_INTERVAL = 2
RETRY_INTERVAL = 2.5
SELF_PORT = 6881


def generate_peer_id():
    """
    Generate random peer id with length of 20 bytes
    """
    version = metadata.version(__package__ or "").replace(".", "") + "0"

    id_suffix = "".join([random.choice(string.ascii_letters) for _ in range(12)])
    peer_id = f"-Tr{version}-{id_suffix}"
    assert len(peer_id) == 20

    return peer_id.encode()


class TorrentClient:
    def __init__(
        self,
        torrent: str,
        max_peers: int | None = None,
        output_dir: str = ".",
    ):
        self.id: bytes = generate_peer_id()
        self.should_continue = True
        self.torrent = TorrentFile(torrent)
        self.swarm = Swarm(self.id, self.torrent, max_peers=max_peers)

        self.pieces: list[Piece] = []
        self.storage = DiskManager(output_dir, self.torrent)

    def setup(self):
        # create tracker for each url of tracker in the config file
        trackers = []
        if "announce" in self.torrent.config:
            tracker = TrackerFactory.create_tracker(self.torrent.config["announce"])
            trackers.append(tracker)

        if "announce-list" in self.torrent.config:
            new_trackers = TrackerFactory.create_trackers(self.torrent.config["announce-list"])
            trackers += new_trackers

        if len(trackers) == 0:
            raise ValueError("No trackers found")

        self.trackers: list[Tracker] = trackers
        self.pieces = create_pieces(self.torrent)

        # Send HTTP/UDP Requests to all Trackers, requesting for peers
        for tracker in self.trackers:
            tracker_peers = tracker.get_peers(self.id, SELF_PORT, self.torrent)
            self.swarm.add(tracker_peers)
        if len(self.swarm.peers) == 0:
            raise Exception("No peers found")

        logger.info("Number of peers: %d", len(self.swarm.peers))

    def start(self):
        self.setup()

        handshakes = Thread(target=self.swarm.send_handshakes)
        requester = Thread(target=self.piece_requester)

        handshakes.start()
        requester.start()
        handshakes.join()
        requester.join()
        logger.info("GoodBye!")

    def piece_requester(self):
        """
        This function will run as different thread.
        Iterate over all the blocks of all the pieces
        in chronological order, and see if one of them is free.
        is yes - request it from random peer.
        """

        while self.should_continue:
            piece_messages = self.swarm.handle_messages()
            for msg in piece_messages:
                self.handle_piece(msg)
            self.request_unfinished_block()
            time.sleep(CONFIGURATION.iteration_sleep_interval)

        logger.info("Exiting the requesting loop...")
        self.storage.close()

    def request_unfinished_block(self) -> Block | None:
        for piece in self.pieces:
            if piece.is_full():
                continue
            self.swarm.request_piece(piece)

        if self._all_pieces_full():
            self.should_continue = False

    def _all_pieces_full(self) -> bool:
        for piece in self.pieces:
            if not piece.is_full():
                return False

        return True

    def _get_piece_by_index(self, index) -> Piece | None:
        for piece in self.pieces:
            if piece.index == index:
                return piece

    def handle_piece(self, pieceMessage: PieceMessage):
        if not len(pieceMessage.data):
            logger.debug("Empty piece: %d", pieceMessage.index)
            return False

        piece = self._get_piece_by_index(pieceMessage.index)
        if piece is None:
            return False
        block = piece.get_block_by_offset(pieceMessage.offset)
        if block is None:
            # TODO Here should be a reson why block is None in other case we risk to get infinite loop
            logger.debug(
                "Unexpected block: piece=%d offset=%d",
                pieceMessage.index,
                pieceMessage.offset,
            )
            return False
        block.data = pieceMessage.data
        block.status = BlockStatus.FULL

        if piece.is_full():
            self.storage.write_piece(piece, self.torrent.piece_size)
            self.pieces.remove(piece)
            logger.info(
                "Progress: %d/%d Unchoked peers: %d/%d",
                self.storage.written,
                len(self.pieces) + self.storage.written,
                self.swarm.num_of_unchoked,
                len(self.swarm.sessions),
            )

            del piece
            return True
        return False
