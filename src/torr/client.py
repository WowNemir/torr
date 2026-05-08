import logging
import random
import socket
import string
import time
from importlib import metadata
from threading import Thread

from torr.configuration import CONFIGURATION
from torr.message import (
    BitField,
    Choke,
    Handshake,
    HaveMessage,
    KeepAlive,
    PieceMessage,
    Request,
    Unchoke,
)
from torr.peer import PeersManager
from torr.piece import Block, BlockStatus, DiskManager, Piece, create_pieces
from torr.tracker import TorrentFile, Tracker, TrackerFactory

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

SHORT_RETRY_INTERVAL = 2
RETRY_INTERVAL = 2.5


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
        max_peers: int = CONFIGURATION.max_peers,
        output_dir: str = ".",
    ):
        self.peer_manager: PeersManager = PeersManager(max_peers)
        self.id: bytes = generate_peer_id()
        self.listener_socket: socket.socket = socket.socket()
        self.listener_socket.settimeout(CONFIGURATION.timeout)
        self.port: int = CONFIGURATION.listening_port
        self.pieces: list[Piece] = []
        self.should_continue = True

        # decode the config file and assign it
        self.torrent = TorrentFile(torrent)
        self.piece_manager = DiskManager(output_dir, self.torrent)
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
        self.pieces = create_pieces(self.torrent.length, self.torrent.piece_size)
        self.number_of_pieces = len(self.pieces)

    def setup(self):
        # Send HTTP/UDP Requests to all Trackers, requesting for peers
        peers = []
        for tracker in self.trackers:
            tracker_peers = tracker.get_peers(self.id, self.port, self.torrent)
            peers += tracker_peers
        if len(peers) == 0:
            raise Exception("No peers found")

        logger.info("Number of peers: %d", len(peers))

        self.peer_manager.add_peers(peers)

    def start(self):
        if len(self.peer_manager.peers) == 0:
            self.setup()

        handshakes = Thread(target=self.peer_manager.send_handshakes, args=(self.id, self.torrent.hash))
        requester = Thread(target=self.piece_requester)

        handshakes.start()
        requester.start()
        self._download()
        handshakes.join()
        requester.join()
        logger.info("GoodBye!")

    def _download(self):
        for _ in range(len(self.pieces)):
            self.handle_messages()

    def handle_messages(self):
        while not self._all_pieces_full():
            if len(self.peer_manager.connected_peers) == 0:
                logger.error("No peers found, sleep for %d seconds", SHORT_RETRY_INTERVAL)
                time.sleep(SHORT_RETRY_INTERVAL)
                continue
            try:
                messages = self.peer_manager.receive_messages()
            except OSError as e:
                logger.info("Unknown socket error: %s", e)
                continue

            for peer, message in messages.items():
                match message:
                    case Handshake():
                        peer.verify_handshake(message)
                    case BitField():
                        logger.info("Got bitfield from %s", peer)
                        peer.set_bitfield(message)
                    case HaveMessage():
                        peer.set_have(message)
                    case KeepAlive():
                        logger.debug("Got keep alive from %s", peer)
                    case Choke():
                        peer.is_choked = True
                    case Unchoke():
                        logger.debug("Received unchoke from %s", peer)
                        peer.is_choked = False
                    case PieceMessage():
                        # "Got piece!", message)
                        if self.handle_piece(message):
                            return
                    case _:
                        logger.error("Unknown message: %s", message)  # should be error

    def piece_requester(self):
        """
        This function will run as different thread.
        Iterate over all the blocks of all the pieces
        in chronological order, and see if one of them is free.
        is yes - request it from random peer.
        """

        while self.should_continue:
            self.request_unfinished_block()
            time.sleep(CONFIGURATION.iteration_sleep_interval)

        logger.info("Exiting the requesting loop...")
        self.piece_manager.close()

    def request_unfinished_block(self) -> Block | None:
        for piece in self.pieces:
            block = piece.get_free_block()
            if block is None:
                continue
            peer = self.peer_manager.get_random_peer_by_piece(piece)
            if peer is None:
                time.sleep(RETRY_INTERVAL)
                continue
            request = Request(piece.index, block.offset, block.size)
            success = peer.send_message(request)
            if success is False:
                logger.error("%s disconnected when requesting for piece", peer)
                self.peer_manager.remove_peer(peer)
                continue
            block.set_requested()
            return block

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
            self.piece_manager.write_piece(piece, self.torrent.piece_size)
            self.pieces.remove(piece)
            logger.info(
                "Progress: %d/%d Unchoked peers: %d/%d",
                self.piece_manager.written,
                self.number_of_pieces,
                self.peer_manager.num_of_unchoked,
                len(self.peer_manager.connected_peers),
            )

            del piece
            return True
        return False
