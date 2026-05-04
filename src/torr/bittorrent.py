import logging
import socket
import time
from threading import Thread

from rich import progress

from torr.block import BlockStatus
from torr.configuration import CONFIGURATION
from torr.exceptions import (
    AllPeersChocked,
    NoPeersHavePiece,
    PeerDisconnected,
    PieceIsFull,
    PieceIsPending,
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
from torr.tracker import TrackerFactory, TrackerManager
from torr.utils import console, generate_peer_id, read_peers_from_input

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
        peers_input: str | None = None,
        output_dir: str = ".",
    ):
        self.peer_manager: PeersManager = PeersManager(max_peers)
        self.tracker_manager: TrackerManager
        self.id: bytes = generate_peer_id()
        self.listener_socket: socket.socket = socket.socket()
        self.listener_socket.settimeout(CONFIGURATION.timeout)
        self.port: int = CONFIGURATION.listening_port
        self.peers_input: str | None = peers_input
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

        while None in trackers:
            trackers.remove(None)

        if len(trackers) == 0:
            raise ValueError("No trackers found")

        self.tracker_manager = TrackerManager(trackers)
        file_size, piece_size = self.torrent.length, self.torrent.piece_size
        self.pieces = create_pieces(file_size, piece_size)
        self.number_of_pieces = len(self.pieces)

    def setup(self):
        # Send HTTP/UDP Requests to all Trackers, requesting for peers
        if self.peers_input:
            logger.info("Reading peers from input")
            peers = read_peers_from_input(self.peers_input)
        else:
            peers = self.tracker_manager.get_peers(self.id, self.port, self.torrent)
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
                if type(message) is Handshake:
                    peer.verify_handshake(message)

                elif type(message) is BitField:
                    logger.info("Got bitfield from %s", peer)
                    peer.set_bitfield(message)

                elif type(message) is HaveMessage:
                    peer.set_have(message)

                elif type(message) is KeepAlive:
                    logger.debug("Got keep alive from %s", peer)

                elif type(message) is Choke:
                    peer.set_choked()

                elif type(message) is Unchoke:
                    logger.debug("Received unchoke from %s", peer)
                    peer.set_unchoked()

                elif type(message) is PieceMessage:
                    # "Got piece!", message)
                    if self.handle_piece(message):
                        return

                else:
                    logger.error("Unknown message: %s", message.id)  # should be error

    def piece_requester(self):
        """
        This function will run as different thread.
        Iterate over all the blocks of all the pieces
        in chronological order, and see if one of them is free.
        is yes - request it from random peer.
        """

        while self.should_continue:
            self.request_current_block()
            time.sleep(CONFIGURATION.iteration_sleep_interval)

        logger.info("Exiting the requesting loop...")
        self.piece_manager.close()

    def request_current_block(self):
        for piece in self.pieces:
            try:
                block = piece.get_free_block()
                peer = self.peer_manager.get_random_peer_by_piece(piece)
                request = Request(piece.index, block.offset, block.size)
                peer.send_message(request)
                block.set_requested()
                return

            except PieceIsPending:
                continue

            except PieceIsFull:
                continue

            except NoPeersHavePiece:
                logger.debug("No peers have piece %d", piece.index)
                time.sleep(RETRY_INTERVAL)

            except AllPeersChocked:
                logger.debug("All of %d peers is chocked", len(self.peer_manager.connected_peers))
                time.sleep(RETRY_INTERVAL)

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

    def _get_piece_by_index(self, index):
        for piece in self.pieces:
            if piece.index == index:
                return piece

    def handle_piece(self, pieceMessage: PieceMessage):
        try:
            if not len(pieceMessage.data):
                logger.debug("Empty piece: %d", pieceMessage.index)
                return

            piece = self._get_piece_by_index(pieceMessage.index)
            if piece is None:
                return
            block = piece.get_block_by_offset(pieceMessage.offset)
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

        except PieceIsPending:
            logger.debug("Piece %d is pending", pieceMessage.index)

        return False
