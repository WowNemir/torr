import random
import string
from importlib import metadata

from rich.console import Console

console = Console()


def generate_peer_id():
    """
    Generate random peer id with length of 20 bytes
    """
    version = metadata.version("torr").replace(".", "") + "0"

    id_suffix = "".join([random.choice(string.ascii_letters) for _ in range(12)])
    peer_id = f"-Tr{version}-{id_suffix}"
    assert len(peer_id) == 20

    return peer_id.encode()
