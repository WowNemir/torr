import logging
import random
import select
import socket
import string
import threading
import time
from importlib import metadata
from threading import Thread

from torr.config import CONFIGURATION
from torr.message import (
    BitField,
    Choke,
    Handshake,
    HaveMessage,
    KeepAlive,
    MessageTypes,
    PieceMessage,
    Request,
    Unchoke,
)
from torr.peer import Peer, Session
from torr.piece import Block, BlockStatus, Piece, create_pieces
from torr.storage import DiskManager
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
        self.id: bytes = generate_peer_id()
        self.listener_socket: socket.socket = socket.socket()
        self.listener_socket.settimeout(CONFIGURATION.timeout)
        self.port: int = CONFIGURATION.listening_port
        self.pieces: list[Piece] = []
        self.should_continue = True
        self.max_peers = max_peers
        self.peers: list[Peer] = []
        self.sessions = []

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
        self.pieces = create_pieces(self.torrent)

    def setup(self):
        # Send HTTP/UDP Requests to all Trackers, requesting for peers
        peers = []
        for tracker in self.trackers:
            tracker_peers = tracker.get_peers(self.id, self.port, self.torrent)
            peers += tracker_peers
        if len(peers) == 0:
            raise Exception("No peers found")

        logger.info("Number of peers: %d", len(peers))

        self.peers += peers

    def start(self):
        if len(self.peers) == 0:
            self.setup()

        handshakes = Thread(target=self.send_handshakes, args=(self.torrent.hash,))
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
            if len(self.sessions) == 0:
                logger.error("No peers found, sleep for %d seconds", SHORT_RETRY_INTERVAL)
                time.sleep(SHORT_RETRY_INTERVAL)
                continue
            try:
                messages = self.receive_messages()
            except OSError as e:
                logger.info("Unknown socket error: %s", e)
                continue

            for session, message in messages.items():
                match message:
                    case Handshake():
                        session.verify_handshake(message)
                    case BitField():
                        logger.info("Got bitfield from %s", session.peer)
                        session.set_bitfield(message)
                    case HaveMessage():
                        session.set_have(message)
                    case KeepAlive():
                        logger.debug("Got keep alive from %s", session.peer)
                    case Choke():
                        session.is_choked = True
                    case Unchoke():
                        logger.debug("Received unchoke from %s", session.peer)
                        session.is_choked = False
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
            session = self.get_random_session_by_piece(piece)
            if session is None:
                time.sleep(RETRY_INTERVAL)
                continue
            request = Request(piece.index, block.offset, block.size)
            success = session.send_message(request)
            if success is False:
                logger.error("%s disconnected when requesting for piece", session.peer)
                self.remove_session(session)
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
                len(self.pieces) + self.piece_manager.written,
                self.num_of_unchoked,
                len(self.sessions),
            )

            del piece
            return True
        return False

    def _send_handshake(self, info_hash, peer: Peer):
        """
        Send handshake to the given peer.
        NOTE: this function is BLOCKING.
        it waits until handshake response received, and failed otherwise.
        """
        session = Session(self.id, peer, self.torrent)
        try:
            session.socket.connect((str(peer.ip), peer.port))
        except OSError:
            return
        try:
            # Send the handshake to peer
            logging.getLogger("BitTorrent").info(f"Trying handshake with peer {peer.ip}")

            success = session._handshake(self.id, info_hash)
            if success is False:
                return
            # Consider it as connected client
            self.sessions.append(session)

            logging.getLogger("BitTorrent").debug(f"Adding {peer}: {len(self.sessions)}/{self.max_peers}")

        except OSError:
            pass

    def send_handshakes(self, info_hash):
        """
        Send handshake to all clients by create polls of threads
        That each one of them sending handshake to a constant number
        of peers. MAX_HANDSHAKE_THREADS decide the max peers to send
        handshake in each thread. big value will cause long run time
        for each thread and less threads, small value will cause for
        less run time to each thread and bigger number of threads.
        """
        # Create handshake thread for each peer
        handshake_threads = []
        for peer in self.peers:
            thread = threading.Thread(target=self._send_handshake, args=(info_hash, peer))
            handshake_threads.append(thread)

        number_of_polls = int(len(handshake_threads) / CONFIGURATION.max_handshake_threads) + 1

        for i in range(1, number_of_polls + 1):
            logging.getLogger("BitTorrent").debug(f"Poll number {i}/{number_of_polls}")
            poll = handshake_threads[: CONFIGURATION.max_handshake_threads]

            # Execute threads
            for thread in poll:
                thread.start()

            # Wait for them to finish
            for thread in poll:
                thread.join()

            if len(self.sessions) >= self.max_peers:
                logging.getLogger("BitTorrent").info(f"Reached max connected peers of {self.max_peers}")
                break

            # Slice the handshake threads
            del handshake_threads[: CONFIGURATION.max_handshake_threads]

        logging.getLogger("BitTorrent").info(f"Total peers connected: {len(self.sessions)}")

    @property
    def num_of_unchoked(self):
        """
        Count the number of unchoked peers
        """
        count = 0
        for session in self.sessions:
            if not session.is_choked:
                count += 1

        return count

    def get_random_session_by_piece(self, piece) -> Session | None:
        """
        Get random peer having the given piece
        Will check at the beginning if all peers are choked,
        And choose randomly one of the peers that have the
        piece (By looking at each peer bitfiled).
        """
        sessions_with_piece = []

        # Check if all the peers choked
        if all(session.is_choked for session in self.sessions):
            # If they are, then even if they have the piece it's not relevant
            logging.getLogger("BitTorrent").debug("All of %d peers is chocked", len(self.sessions))
            return None

        # Check from all the peers who have the piece
        for session in self.sessions:
            if session.have_piece(piece) and not session.is_choked:
                sessions_with_piece.append(session)

        # If we left with any peers, shuffle from them
        if sessions_with_piece:
            return random.choice(sessions_with_piece)

        # If we reached so far... then no peer founded
        logging.getLogger("BitTorrent").debug("No peers have piece %d", piece.index)
        return None

    def remove_session(self, session):
        """
        Remove peer from the 'connected_peers' list.
        The reason why twin-like function not exists for the
        'peers' list resides in the fact we don't really care
        from this list, while we are very care form the connected_peers
        list, because we use in the receive_message function later.
        """
        if session in self.sessions:
            self.sessions.remove(session)

    def receive_messages(self) -> dict[Session, MessageTypes]:
        """
        Receive new messages from clients
        """

        # Check for new readable sockets from the connected peers
        sockets = [session.socket for session in self.sessions]  # The bug resides in here...
        readable, _, _ = select.select(sockets, [], [])

        peers_to_message = {}
        # Extract peer from given sockets
        for session in self.sessions:
            for should_read in readable:
                if session.socket == should_read:
                    peers_to_message[session] = None

        # Receive messages from all the given peers
        for peer in peers_to_message:
            message = peer.receive_message()
            if message is None:
                logging.getLogger("BitTorrent").debug("%s disconnected while waiting for message", peer)
                self.remove_session(peer)
                return self.receive_messages()
            peers_to_message[peer] = message

        return peers_to_message
