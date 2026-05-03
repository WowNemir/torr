import json

from torrentclient.Exceptions import InvalidConfigurationValue


class Configuration:
    def __init__(self, path: str = "./config.json"):
        self.path: str = path
        self.listening_port: int = 6881
        self.max_listening_port: int = 6889
        self.max_peers: int = 12
        self.request_interval: float = 0.2
        self.iteration_sleep_interval: float = 0.001
        self.logging_level: int = 100
        self.timeout: float = 3.0
        self.max_handshake_threads: int = 80
        self.udp_tracker_receive_size: int = 16384
        self.handshake_stripped_size: int = 48
        self.default_connection_id: int = 0x41727101980
        self.compact_value_num_bytes: int = 6
        self.tcp_only: bool = False

    def load(self):
        with open(self.path) as config:
            raw = config.read()

        config = json.loads(raw)
        for key, value in config.items():
            if key not in self.__dict__.keys() or key.startswith("_"):
                continue

            if type(self.__dict__[key]) is not type(value):
                try:
                    self_type = type(self.__dict__[key]).__name__
                    if self_type == "int":
                        value = globals()["__builtins__"][self_type](value, 0)
                    else:
                        value = globals()["__builtins__"][self_type](value)
                except BaseException as e:
                    raise InvalidConfigurationValue from e

            self.__dict__[key] = value


CONFIGURATION = Configuration()
CONFIGURATION.load()
