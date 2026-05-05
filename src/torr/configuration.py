import json


class Configuration:
    def __init__(self, path: str = "./config.json"):
        self.path: str = path
        self.listening_port: int = 6881
        self.max_peers: int = 12
        self.iteration_sleep_interval: float = 0.001
        self.logging_level: int = 100
        self.timeout: float = 3.0
        self.max_handshake_threads: int = 80
        self.udp_tracker_receive_size: int = 16384
        self.handshake_stripped_size: int = 48
        self.default_connection_id: int = 0x41727101980
        self.compact_value_num_bytes: int = 6

    def load(self):
        with open(self.path) as f:
            config = json.load(f)
        for key, value in config.items():
            setattr(self, key, value)


CONFIGURATION = Configuration()
CONFIGURATION.load()
