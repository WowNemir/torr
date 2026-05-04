import socket
import struct
from abc import ABC, abstractmethod

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


class TrackerManager:
    def __init__(self, trackers: list[Tracker]):
        self.trackers: list[Tracker] = trackers

    def get_peers(self, peer_id: bytes, port: int, torrent_file) -> list[Peer]:
        """
        Return list of peers, by calling each tracker 'get_peer' method.
        This will cause a series of HTTP/UDP requests, in the end each
        one will return his list of peers.
        """
        peers = []
        for tracker in self.trackers:
            tracker_peers = tracker.get_peers(peer_id, port, torrent_file)
            peers += tracker_peers

        return peers
