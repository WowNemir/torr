import io
import logging

import requests

from torrentclient.bcoder import bdecode
from torrentclient.configuration import CONFIGURATION
from torrentclient.exceptions import UnexpectedResponse
from torrentclient.peer import Peer
from torrentclient.tracker import Tracker


class HTTPTracker(Tracker):
    def get_peers(self, peer_id: bytes, port: int, torrent) -> list[Peer]:
        """
        Request from the http tracker all the peers,
        parse them, and then return list containing
        Peer objects.
        """
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
            raw_response = requests.get(self.url, params=params).content
            tracker_response = bdecode(io.BytesIO(raw_response), mode="str")
            logging.getLogger("BitTorrent").info(f"success in scraping {self.url}")
        except (requests.exceptions.RequestException, TypeError, UnexpectedResponse):
            logging.getLogger("BitTorrent").error(f"Failed to scrape {self.url}")
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
                f'Failure in tracker {self.url}: {tracker_response["failure reason"]}'
            )
        else:
            logging.getLogger("BitTorrent").error(f"Unknown exception in tracker {self.url}")

        return peers
