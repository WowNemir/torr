import ipaddress
import logging
import socket
import struct

from bitstring import BitArray

from torr.config import CONFIGURATION
from torr.message import BitField, Handshake, HaveMessage, Message, MessageFactory


class Peer:
    def __init__(self, ip: str, port: int):
        self.ip = ipaddress.ip_address(ip)
        self.port = port

    def __repr__(self):
        ip_and_port = f"{self.ip}:{self.port}"
        return f"Peer({ip_and_port})"


class Session:
    def __init__(self, client_id: bytes, peer: Peer, info_hash):
        self.peer_id = b"" * 20
        self.info_hash = info_hash
        self.client_id = client_id
        self.peer = peer
        self.connected = False  # only after handshake this will be true
        self.handshake = None  # Handshake still have not happened
        self.is_choked = True  # By default the client is choked
        self.bitfield: BitArray = BitArray()
        self.socket = socket.socket(
            family=socket.AF_INET if self.peer.ip.version == 4 else socket.AF_INET6, type=socket.SOCK_STREAM
        )

        self.socket.settimeout(CONFIGURATION.timeout)

    def _handshake(self, my_id, info_hash):
        self.handshake = Handshake(my_id, info_hash)
        handshake_bytes = self.handshake.to_bytes()

        self.socket.send(handshake_bytes)
        response: Handshake | None = self.receive_message()
        if response is None:
            return False
        assert isinstance(response, Handshake)
        self.verify_handshake(response)
        self.peer_id = response.peer_id
        return True

    def verify_handshake(self, message) -> bool:
        if self.handshake == message:
            self.connected = True

        return self.connected

    def set_bitfield(self, bitfield: BitField):
        self.bitfield = bitfield.bitfield

    def set_have(self, have: HaveMessage):
        if have.index < self.bitfield.length:
            self.bitfield[have.index] = True
        else:
            logging.getLogger("BitTorrent").info(f"Have message {have.index} smaller then {self.bitfield.length}")

    def receive_message(self) -> Message | None:
        # After handshake
        # myid = random.randint(0, 65536)
        try:
            packet_length = self.socket.recv(1)

        except OSError:
            return None

        if packet_length == b"":
            logging.getLogger("BitTorrent").debug("%s disconnected", self)
            self.socket.close()
            return None

        if self.connected:
            packet_length = packet_length + self.socket.recv(3)
            while len(packet_length) < 4:
                odd = 4 - len(packet_length)
                packet_length = packet_length + self.socket.recv(odd)
                logging.getLogger("BitTorrent").error(f"Setting size again in {self}, length: {packet_length}")

            length = struct.unpack(">I", packet_length)[0]  # Big endian integer
            data = self.socket.recv(length)

            while len(data) != length:
                odd = length - len(data)
                data += self.socket.recv(odd)

            return MessageFactory.create_message(data)

        else:
            protocol_len: int = struct.unpack(">B", packet_length)[0]
            handshake_bytes = self.socket.recv(protocol_len + CONFIGURATION.handshake_stripped_size)

            return Handshake.from_bytes(packet_length + handshake_bytes)

    def send_message(self, message: Message) -> bool:
        # logging.getLogger('BitTorrent').debug(f'Sending message {type(message)} to {self}')
        message_bytes = message.to_bytes()
        try:
            self.socket.send(message_bytes)
        except OSError:
            return False
        else:
            return True

    def have_piece(self, piece):
        return piece.index < self.bitfield.length and self.bitfield[piece.index]
