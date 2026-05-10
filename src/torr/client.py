import logging
import random
import string
from importlib import metadata
from threading import Thread

from torr.piece import Piece, create_pieces
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

SELF_PORT = 6881


def generate_peer_id():
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

        self.unfinished_pieces: list[Piece] = []
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
        self.unfinished_pieces = create_pieces(self.torrent)

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
        while self.unfinished_pieces:
            p = random.choice(self.unfinished_pieces)
            self.swarm.request_piece(p)
            finished_pieces = self.swarm.handle_messages()
            for p in finished_pieces:
                self.store_piece(p)

        logger.info("Exiting the requesting loop...")
        self.storage.close()

    def store_piece(self, piece):
        self.storage.write_piece(piece, self.torrent.piece_size)
        piece in self.unfinished_pieces and self.unfinished_pieces.remove(piece)
        logger.info(
            "Progress: %d/%d Unchoked peers: %d/%d",
            self.storage.written,
            len(self.unfinished_pieces) + self.storage.written,
            self.swarm.num_of_unchoked,
            len(self.swarm.sessions),
        )

        del piece
