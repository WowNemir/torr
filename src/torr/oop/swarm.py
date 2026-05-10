# TODO swarm only manages peers and connections, so only Handshake messages should be relevant here
import logging
import random
import select
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from torr.oop.message import (
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
from torr.oop.peer import Peer, Session
from torr.oop.piece import BlockStatus, Piece
from torr.oop.torrent_file import TorrentFile

MAX_HANDSHAKE_THREADS = 12
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
        self.piece_map: dict[int, Piece] = {}
        self.total_blocks = 0
        self.done_blocks = 0
        self.max_peers = max_peers or MAX_PEERS
        self.thread_pool = ThreadPoolExecutor(max_workers=MAX_HANDSHAKE_THREADS)

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
        futures = []

        for peer in self.peers:
            futures.append(self.thread_pool.submit(self._send_handshake, info_hash, peer))

        for future in as_completed(futures):
            session = future.result()
            if session and len(self.sessions) >= self.max_peers:
                logger.info(f"Reached max connected peers of {self.max_peers}")
                break

        logger.info(f"Total peers connected: {len(self.sessions)}")

    @property
    def num_of_unchoked(self):
        count = 0
        for session in self.sessions:
            if not session.is_choked:
                count += 1
        return count

    def get_random_session_by_piece(self, piece) -> Session | None:
        sessions_with_piece = []

        if all(session.is_choked for session in self.sessions):
            return None

        for session in (s for s in self.sessions if not s.is_choked):
            if session.have_piece(piece):
                sessions_with_piece.append(session)

        if sessions_with_piece:
            return random.choice(sessions_with_piece)

        return None

    def remove_session(self, session):
        if session in self.sessions:
            self.sessions.remove(session)

    def receive_messages(self) -> dict[Session, MessageTypes]:
        sockets = [session.socket for session in self.sessions]
        readable, _, _ = select.select(sockets, [], [])

        peers_to_message = {}

        for session in self.sessions:
            for should_read in readable:
                if session.socket == should_read:
                    peers_to_message[session] = None

        for peer in peers_to_message:
            message = peer.receive_message()
            if message is None:
                self.remove_session(peer)
                return self.receive_messages()
            peers_to_message[peer] = message

        return peers_to_message

    def _send_handshake(self, info_hash, peer: Peer):
        session = Session(self.client_id, peer, self.torrent.hash)
        try:
            session.socket.connect((str(peer.ip), peer.port))
        except OSError:
            return
        try:
            success = session._handshake(self.client_id, info_hash)
            if success is False:
                return
            self.sessions.append(session)
        except OSError:
            pass
        return session

    def _send_block_request(self, session, request, block):
        success = session.send_message(request)
        if success is False:
            self.remove_session(session)
            return
        block.set_requested()

    def request_piece(self, piece: Piece):
        if piece.index not in self.piece_map:
            self.piece_map[piece.index] = piece
            self.total_blocks += len(piece.blocks)

        session = self.get_random_session_by_piece(piece)
        if session is None:
            time.sleep(RETRY_INTERVAL)
            return None
        blocks = piece.get_free_blocks()
        futures = []

        for block in blocks:
            block.calculate_status()
            if block.status != BlockStatus.FREE:
                continue
            request = Request(piece.index, block.offset, block.size)
            futures.append(self.thread_pool.submit(self._send_block_request, session, request, block))

        for future in as_completed(futures):
            future.result()

    def handle_messages(self):
        if len(self.sessions) == 0:
            time.sleep(SHORT_RETRY_INTERVAL)

        try:
            messages = self.receive_messages()
        except OSError:
            return

        finished_pieces = []

        for session, message in messages.items():
            match message:
                case Handshake():
                    session.verify_handshake(message)
                case BitField():
                    session.set_bitfield(message)
                case HaveMessage():
                    session.set_have(message)
                case KeepAlive():
                    pass
                case Choke():
                    session.is_choked = True
                case Unchoke():
                    session.is_choked = False
                case PieceMessage():
                    piece = self.handle_piece(message)
                    if piece and piece.is_full():
                        finished_pieces.append(piece)

        return finished_pieces

    def handle_piece(self, pieceMessage: PieceMessage) -> Piece | None:
        index = pieceMessage.index
        piece = self.piece_map.get(index)
        if piece is None:
            return None

        block = piece.get_block_by_offset(pieceMessage.offset)
        if block is None:
            return None

        self.done_blocks += 1
        block.data = pieceMessage.data
        block.status = BlockStatus.FULL
        return piece
