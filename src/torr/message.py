import enum
import struct
from abc import ABC, abstractmethod

from bitstring import BitArray


class MessageCode(enum.IntEnum):
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    NOT_INTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7
    CANCEL = 8
    PORT = 9

    HANDSHAKE = -1


class Message(ABC):
    @abstractmethod
    def to_bytes(self) -> bytes:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_bytes(cls, payload: bytes):
        raise NotImplementedError


class Choke(Message):
    def __init__(self):
        self.id = MessageCode.CHOKE
        self.length = 1

    def to_bytes(self) -> bytes:
        return struct.pack(">IB", self.length, self.id)

    @classmethod
    def from_bytes(cls, payload):
        return cls()


class Unchoke(Message):
    def __init__(self):
        self.id = MessageCode.UNCHOKE
        self.length = 1

    def to_bytes(self) -> bytes:
        return struct.pack(">IB", self.length, self.id)

    @classmethod
    def from_bytes(cls, payload):
        # The unchoke message contains no relevant values...
        return cls()


class BitField(Message):
    def __init__(self, bitfield):
        self.bitfield = BitArray(bitfield)

    @classmethod
    def from_bytes(cls, payload):
        # payload is the bitstring
        return cls(BitArray(payload))

    def to_bytes(self) -> bytes:
        raise NotImplementedError


class Handshake(Message):
    def __init__(self, peer_id: bytes, info_hash: bytes, protocol: str = "BitTorrent protocol"):
        self.id = MessageCode.HANDSHAKE
        self.peer_id = peer_id
        self.info_hash = info_hash
        self.protocol = protocol

    def to_bytes(self) -> bytes:
        protocol_len = len(self.protocol)
        handshake = struct.pack(
            f">B{protocol_len}s8s20s20s",
            protocol_len,
            self.protocol.encode(),
            b"\x00" * 8,
            self.info_hash,
            self.peer_id,
        )

        return handshake

    @classmethod
    def from_bytes(cls, payload: bytes) -> "Handshake":
        if len(payload) != 68:
            raise ValueError(f"Payload error: {payload}")
        protocol_len = struct.unpack(">B", payload[:1])[0]
        protocol, reserved, info_hash, peer_id = struct.unpack(f">{protocol_len}s8s20s20s", payload[1:])

        return Handshake(peer_id, info_hash, protocol)

    def __eq__(self, other):
        return self.info_hash == other.info_hash


class Request(Message):
    def __init__(self, index, offset, length):
        self.id = MessageCode.REQUEST
        self.index = index  # 4 byte
        self.begin = offset  # 4 bytes
        self.piece_length = length  # 4 bytes
        self.length = 13  # bytes

    def to_bytes(self) -> bytes:
        return struct.pack(">IBIII", self.length, self.id, self.index, self.begin, self.piece_length)

    @classmethod
    def from_bytes(cls, payload):
        _, _, index, begin, length = struct.unpack(">IBIII", payload)
        return cls(index, begin, length)


class PieceMessage(Message):
    def __init__(self, index, offset, data):
        self.index = index
        self.offset = offset
        self.data = data

    def __str__(self):
        return f"[index: {self.index}, offset: {self.offset}]"

    def to_bytes(self):
        pass

    @classmethod
    def from_bytes(cls, payload):
        index, offset = struct.unpack(">II", payload[:8])
        data = payload[8:]

        return cls(index, offset, data)


class HaveMessage(Message):
    def __init__(self, index):
        self.index = index

    @classmethod
    def from_bytes(cls, payload):
        index = struct.unpack(">I", payload)[0]

        return cls(index)

    def to_bytes(self):
        return b""


class UnknownMessage(Message):
    def __init__(self, _id):
        self.id = _id

    def to_bytes(self) -> bytes:
        return b""

    @classmethod
    def from_bytes(cls, payload):
        return cls(payload)


class KeepAlive(Message):
    def to_bytes(self) -> bytes:
        return struct.pack("I", 0)

    @classmethod
    def from_bytes(cls, payload):
        return cls()


# Used for typing
MessageTypes = Message | Handshake | Request | PieceMessage | BitField | HaveMessage | Unchoke | Choke | UnknownMessage


class MessageFactory:
    @staticmethod
    def create_message(payload) -> Message:
        if len(payload) == 0:
            return KeepAlive()

        _id = payload[0]
        if _id not in messages_creators:
            return UnknownMessage(_id)

        return messages_creators[_id](payload[1:])  # Delete the message id byte

    @staticmethod
    def create_handshake_message(payload):
        return Handshake.from_bytes(payload)

    @staticmethod
    def create_bitfield_message(payload):
        return BitField.from_bytes(payload)

    @staticmethod
    def create_choke_message(payload):
        return Choke.from_bytes(payload)

    @staticmethod
    def create_unchoke_message(payload):
        return Unchoke.from_bytes(payload)

    @staticmethod
    def create_piece_message(payload):
        return PieceMessage.from_bytes(payload)

    @staticmethod
    def create_have_message(payload):
        return HaveMessage.from_bytes(payload)


messages_creators = {
    MessageCode.BITFIELD: MessageFactory.create_bitfield_message,
    MessageCode.CHOKE: MessageFactory.create_choke_message,
    MessageCode.UNCHOKE: MessageFactory.create_unchoke_message,
    MessageCode.PIECE: MessageFactory.create_piece_message,
    MessageCode.HAVE: MessageFactory.create_have_message,
}
