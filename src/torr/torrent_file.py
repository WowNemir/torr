import hashlib
import logging
from typing import cast

from torr.bcoder import bdecode, bencode


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
