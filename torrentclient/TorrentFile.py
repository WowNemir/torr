import hashlib
import logging

from torrentclient.torrentclient.bcoder import bdecode, bencode


class TorrentFile:
    def __init__(self, torrent):
        logging.getLogger("BitTorrent").info("Start reading from BitTorrent file")
        with open(torrent, "rb") as torrent_file:
            self.config = bdecode(torrent_file, mode="str")

        self.info: dict = self.config["info"]
        self.hash = hashlib.sha1(bencode(self.info)).digest()
        self.length: int = (
            sum(file["length"] for file in self.info["files"])
            if "files" in self.info.keys()
            else self.info.get("length") or self.config.get("length")
        )
        self.file_name: str = self.info.get("name") or self.config.get("name")
        self.piece_size: int = self.info.get("piece length") or self.config.get("piece length")
