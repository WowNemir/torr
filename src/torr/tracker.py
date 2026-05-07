import hashlib
import logging
import random
import socket
import struct
from abc import ABC, abstractmethod
from typing import cast
from urllib.parse import urlparse

import requests

from torr.bcoder import bdecode, bencode
from torr.configuration import CONFIGURATION
from torr.peer import Peer


class Tracker(ABC):
    def __init__(self, url):
        self.url = url

    @abstractmethod
    def get_peers(self, peer_id: bytes, port: int, torrent) -> list[Peer]:
        pass

    @staticmethod
    def extract_compact_peers(peers_bytes) -> list[Peer]:
        offset = 0
        peers = []
        if not peers_bytes:
            return []

        for _ in range(len(peers_bytes) // CONFIGURATION.compact_value_num_bytes):
            ip, port = struct.unpack_from("!iH", peers_bytes, offset)
            ip = socket.inet_ntoa(struct.pack("!i", ip))
            offset += CONFIGURATION.compact_value_num_bytes

            peers.append(Peer(ip, port))

        return peers


class Connection:
    def __init__(self, transaction_id=None, connection_id=CONFIGURATION.default_connection_id, action=0):
        self.transaction_id = transaction_id
        self.connection_id = connection_id
        self.action = action

        if transaction_id is None:
            self.transaction_id = random.randint(0, 65536)

    def __str__(self):
        return f"Transaction id: {self.transaction_id}, Connection id: {self.connection_id}, Action: {self.action}"

    def __eq__(self, other):
        return self.transaction_id == other.transaction_id

    def to_bytes(self):
        return struct.pack(">QII", self.connection_id, self.action, self.transaction_id)

    @staticmethod
    def from_bytes(payload):
        action, transaction_id, connection_id = struct.unpack(">IIQ", payload)
        return Connection(transaction_id=transaction_id, connection_id=connection_id, action=action)


class Announce:
    def __init__(self, connection_id, info_hash, peer_id, left, port, action=1, transaction_id=None):
        self.connection_id = connection_id
        self.transaction_id = transaction_id
        self.info_hash = info_hash
        self.left = left
        self.peer_id = peer_id
        self.port = port
        self.action = action

        if transaction_id is None:
            self.transaction_id = random.randint(0, 65536)

    def to_bytes(self):
        downloaded = 0
        left = 0
        uploaded = 0
        event = 0
        ip = 0
        key = 0
        num_want = -1

        _bytes = struct.pack(
            ">QII20s20sQQQIIIiH",
            self.connection_id,
            self.action,
            self.transaction_id,
            self.info_hash,
            self.peer_id,
            downloaded,
            left,
            uploaded,
            event,
            ip,
            key,
            num_want,
            self.port,
        )

        return _bytes


class AnnounceResult:
    def __init__(self, action, transaction_id, interval, leechers, seeders, peers=None):
        self.action = action
        self.transaction_id = transaction_id
        self.interval = interval
        self.leechers = leechers
        self.seeders = seeders
        self.peers = peers

    @staticmethod
    def from_bytes(payload):
        if len(payload) >= 20:
            return AnnounceResult(*struct.unpack(">IIIII", payload[:20]), payload[20:])
        else:
            return AnnounceResult(*struct.unpack(">II", payload[:8]), 0, 0, [])


class HTTPTracker(Tracker):
    def get_peers(self, peer_id: bytes, port: int, torrent) -> list[Peer]:
        logging.getLogger("BitTorrent").error(f"Connecting to HTTP Tracker {self.url}")

        params = {
            "info_hash": torrent.hash,
            "peer_id": peer_id,
            "uploaded": 0,
            "downloaded": 0,
            "port": port,
            "left": torrent.length,
            "event": "started",
            "timeout": CONFIGURATION.timeout,
        }
        try:
            with requests.get(self.url, params=params, stream=True) as r:
                r.raw.decode_content = True
                tracker_response = bdecode(r.raw, mode="str")
            logging.getLogger("BitTorrent").info(f"success in scraping {self.url}")
        except (requests.exceptions.RequestException, TypeError, ValueError) as e:
            logging.getLogger("BitTorrent").error(f"Failed to scrape {self.url}, {e}")
            return []

        peers = []

        if "peers" in tracker_response or "peers6" in tracker_response:
            peers_key = "peers"
            if "peers6" in tracker_response:
                peers_key += "6"

            if type(tracker_response[peers_key]) is list:
                peers = [Peer(info["ip"], info["port"], info["peer id"]) for info in tracker_response[peers_key]]
            else:
                logging.getLogger("BitTorrent").info(f"Tracker {self.url} using compact mode")
                peers = Tracker.extract_compact_peers(tracker_response[peers_key])

        elif "failure reason" in tracker_response:
            logging.getLogger("BitTorrent").error(
                f"Failure in tracker {self.url}: {tracker_response['failure reason']}"
            )
        else:
            logging.getLogger("BitTorrent").error(f"Unknown exception in tracker {self.url}")

        return peers


class UDPTracker(Tracker):
    def get_peers(self, peer_id: bytes, port: int, torrent) -> list[Peer]:
        """
        Connect to udp tracker and retrieve from him list of peers. Following the
        BitTorrent UDP Tracker specification, And sourceforge unofficial guide:
        https://www.bittorrent.org/beps/bep_0015.html
        https://xbtt.sourceforge.net/udp_tracker_protocol.html
        """
        url_details = urlparse(self.url)
        tracker_address = (url_details.hostname, url_details.port)
        connection_request = Connection()

        try:
            # Send Connection Request
            sock = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
            sock.sendto(connection_request.to_bytes(), tracker_address)

            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(CONFIGURATION.timeout)

            response = sock.recv(CONFIGURATION.udp_tracker_receive_size)  # Answer should be 16 bytes

            connection_response = Connection.from_bytes(response)
            connection_id = connection_response.connection_id

            if connection_request != connection_response:
                logging.getLogger("BitTorrent").error("UDP Tracker request and response are not equal")

            announce = Announce(connection_id, torrent.hash, peer_id, torrent.length, port)
            sock.sendto(announce.to_bytes(), tracker_address)

            response = sock.recv(CONFIGURATION.udp_tracker_receive_size)  # Answer should be 98 bytes
            announce_response: AnnounceResult = AnnounceResult.from_bytes(response)

            if announce_response.transaction_id != announce.transaction_id:
                logging.getLogger("BitTorrent").error("UDP Tracker request and response are not equal")

            peers = Tracker.extract_compact_peers(announce_response.peers)
            logging.getLogger("BitTorrent").info(f"success in scraping {self.url} got {len(peers)} peers")
            return peers

        except OSError:
            logging.getLogger("BitTorrent").error(f"Tracker {url_details.hostname}:{url_details.port} give no answer")
            return []


class TrackerFactory:
    @staticmethod
    def create_tracker(url: str) -> Tracker:
        """
        Check the scheme of the url,
        and decide which type of tracker to create.
        :param url: url of the tracker (HTTP/UDP)
        :return: Tracker
        """
        parsed = urlparse(url)
        if "http" in parsed.scheme.lower():
            return HTTPTracker(url)
        elif "udp" in parsed.scheme.lower():
            return UDPTracker(url)
        else:
            raise NotImplementedError("Unsupported protocol")

    @staticmethod
    def create_trackers(urls: list[list[str]]) -> list[Tracker]:
        """
        Create trackers from the given url list.
        Current options are HTTP/UDP.
        """
        trackers = []
        for url in urls:
            tracker = TrackerFactory.create_tracker(url[0])  # TODO for some reason it is important to pick first url
            trackers.append(tracker)

        return trackers


class TorrentFile:
    def __init__(self, torrent):
        logging.getLogger("BitTorrent").info("Start reading from BitTorrent file")
        with open(torrent, "rb") as torrent_file:
            self.config = bdecode(torrent_file, mode="str")
        self.config = cast(dict, self.config)

        self.info: dict = self.config["info"]
        self.hash = hashlib.sha1(bencode(self.info)).digest()
        self.length: int = sum(f["length"] for f in (self.info.get("files") or [self.info]))
        self.file_name: str = self.info.get("name", "default_name")
        self.piece_size: int = self.info.get("piece length", 0)
