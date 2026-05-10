import logging
import random
import select
import threading
import time

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
from torr.piece import Piece
from torr.torrent_file import TorrentFile

MAX_HANDSHAKE_THREADS = 80
MAX_PEERS = 12
SHORT_RETRY_INTERVAL = 2
RETRY_INTERVAL = 2.5


logger = logging.getLogger(__name__)


class Swarm:
    def __init__(self, client_id, torrent: TorrentFile, max_peers: int | None = None):
        self.client_id = client_id
        self.torrent = torrent
        self.sessions = []
        self.peers = []

    def __iter__(self):
        while self.sessions:
            yield from iter(self.sessions)

    def add(self, obj: Peer | list[Peer]):
        match obj:
            case list():
                self.peers.extend(obj)
            case Peer():
                self.peers.append(obj)

    def send_handshakes(self):
        info_hash = self.torrent.hash

        # Create handshake thread for each peer
        handshake_threads = []
        for peer in self.peers:
            thread = threading.Thread(target=self._send_handshake, args=(info_hash, peer))
            handshake_threads.append(thread)

        number_of_polls = int(len(handshake_threads) / MAX_HANDSHAKE_THREADS) + 1

        for i in range(1, number_of_polls + 1):
            logging.getLogger("BitTorrent").debug(f"Poll number {i}/{number_of_polls}")
            poll = handshake_threads[:MAX_HANDSHAKE_THREADS]

            # Execute threads
            for thread in poll:
                thread.start()

            # Wait for them to finish
            for thread in poll:
                thread.join()

            if len(self.sessions) >= MAX_PEERS:
                logging.getLogger("BitTorrent").info(f"Reached max connected peers of {MAX_PEERS}")
                break

            # Slice the handshake threads
            del handshake_threads[:MAX_HANDSHAKE_THREADS]

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
        for session in (s for s in self.sessions if not s.is_choked):
            if session.have_piece(piece):
                sessions_with_piece.append(session)

        # If we left with any peers, shuffle from them
        if sessions_with_piece:
            return random.choice(sessions_with_piece)

        # If we reached so far... then no peer founded
        logging.getLogger("BitTorrent").debug("No peers have piece %d", piece.index)
        return None

    def remove_session(self, session):
        if session in self.sessions:
            self.sessions.remove(session)

    def receive_messages(self) -> dict[Session, MessageTypes]:
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

    def _send_handshake(self, info_hash, peer: Peer):
        """
        Send handshake to the given peer.
        NOTE: this function is BLOCKING.
        it waits until handshake response received, and failed otherwise.
        """
        session = Session(self.client_id, peer, self.torrent.hash)
        try:
            session.socket.connect((str(peer.ip), peer.port))
        except OSError:
            return
        try:
            # Send the handshake to peer
            logging.getLogger("BitTorrent").info(f"Trying handshake with peer {peer.ip}")

            success = session._handshake(self.client_id, info_hash)
            if success is False:
                return
            # Consider it as connected client
            self.sessions.append(session)

            logging.getLogger("BitTorrent").debug(f"Adding {peer}: {len(self.sessions)}/{MAX_PEERS}")

        except OSError:
            pass
        return session

    def request_piece(self, piece: Piece):
        session = self.get_random_session_by_piece(piece)
        if session is None:
            time.sleep(RETRY_INTERVAL)
            return None
        block = piece.get_free_block()
        if block is None:
            return None

        request = Request(piece.index, block.offset, block.size)
        success = session.send_message(request)
        if success is False:
            logger.error("%s disconnected when requesting for piece", session.peer)
            self.remove_session(session)
        block.set_requested()
        return block

    def handle_messages(self):
        if len(self.sessions) == 0:
            logger.error("No peers found, sleep for %d seconds", SHORT_RETRY_INTERVAL)
            time.sleep(SHORT_RETRY_INTERVAL)
        try:
            messages = self.receive_messages()
        except OSError as e:
            logger.info("Unknown socket error: %s", e)
        pieces = []
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
                    pieces.append(message)
                case _:
                    logger.error("Unknown message: %s", message)  # should be error
        return pieces
