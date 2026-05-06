import logging
import socket
import time
from threading import Thread

from rich import progress

from torr.block import Block, BlockStatus
from torr.configuration import CONFIGURATION
from torr.exceptions import (
    PeerDisconnected,
)
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
from torr.piece import DiskManager, Piece, create_pieces
from torr.torrent_file import TorrentFile
from torr.tracker import Tracker, TrackerFactory
from torr.utils import console, generate_peer_id

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

SHORT_RETRY_INTERVAL = 2
RETRY_INTERVAL = 2.5


class TorrentClient:
    def __init__(
        self,
        torrent: str,
        max_peers: int = CONFIGURATION.max_peers,
        use_progress_bar: bool = True,
        output_dir: str = ".",
    ):
        self.peer_manager: PeersManager = PeersManager(max_peers)
        self.id: bytes = generate_peer_id()
        self.listener_socket: socket.socket = socket.socket()
        self.listener_socket.settimeout(CONFIGURATION.timeout)
        self.port: int = CONFIGURATION.listening_port
        self.pieces: list[Piece] = []
        self.should_continue = True
        self.use_progress_bar = use_progress_bar
        if use_progress_bar:
            logger.setLevel(CONFIGURATION.logging_level)

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
        self.progress_download()
        handshakes.join()
        requester.join()
        console.print("[green]GoodBye!")

    def progress_download(self):
        if self.use_progress_bar:
            for _ in progress.track(
                range(len(self.pieces)),
                description=f"Downloading {self.torrent.file_name}",
            ):
                self.handle_messages()
        else:
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
                        peer.set_choked()
                    case Unchoke():
                        logger.debug("Received unchoke from %s", peer)
                        peer.set_unchoked()
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
            try:
                block = piece.get_free_block()
                if block is None:
                    continue
                peer = self.peer_manager.get_random_peer_by_piece(piece)
                if peer is None:
                    time.sleep(RETRY_INTERVAL)
                    continue
                request = Request(piece.index, block.offset, block.size)
                peer.send_message(request)
                block.set_requested()
                return block

            except PeerDisconnected:
                logger.error("Peer %s disconnected when requesting for piece", peer)
                self.peer_manager.remove_peer(peer)

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
            if not self.use_progress_bar:
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
