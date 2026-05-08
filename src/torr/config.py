from dataclasses import dataclass


@dataclass
class Configuration:
    def __init__(self):
        self.listening_port: int = 6881
        self.max_peers: int = 12
        self.iteration_sleep_interval: float = 0.001
        self.timeout: float = 3.0
        self.max_handshake_threads: int = 80
        self.udp_tracker_receive_size: int = 16384
        self.handshake_stripped_size: int = 48
        self.default_connection_id: int = 0x41727101980


CONFIGURATION = Configuration()
