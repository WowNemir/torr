from typing import Literal
import io
from collections.abc import Generator

type BEncoded = int | bytes | list["BEncoded"] | dict[bytes | str, "BEncoded"] | str


class Decoder:
    def __init__(self, f: io.BufferedReader):
        self.position = 0
        self.content = bytes(f.read())

    def read_bytes(self, n: int) -> bytes:
        res = self.content[self.position:self.position+n]
        self.position += n
        return res

    def read_until(self, end: bytes) -> bytes:
        end_ind = self.content.find(end, self.position)

        if end_ind == -1:
            raise ValueError("Delimiter not found")

        res = self.content[self.position:end_ind]
        self.position = end_ind + len(end)
        return res

    def decode_items(self, mode: Literal['str', 'bytes']='bytes') -> Generator[BEncoded]:
        while head := self.read_bytes(1):
            if head == b'e':
                return
            if head == b"l":
                yield list(self.decode_items(mode=mode))
            elif head == b"d":
                it = self.decode_items(mode=mode)
                yield dict(zip(it, it, strict=True))  # type: ignore

            elif head == b"i":
                yield int(self.read_until(b"e"))

            elif head.isdigit():
                self.read_bytes(-1)
                length = int(self.read_until(b":"))
                if mode == 'str':
                    value = self.read_bytes(length)
                    try:
                        value = value.decode()
                    except UnicodeDecodeError:
                        pass

                    yield value
                else:
                    yield self.read_bytes(length)
            else:
                raise ValueError(f"Unknown token: {head}")




def bdecode(file: io.BufferedReader, mode: Literal['str', 'bytes'] = 'bytes'):
    return next(Decoder(file).decode_items(mode))


def bencode(obj) -> bytes:
    if isinstance(obj, int):
        return b'i' + str(obj).encode() + b'e'
    elif isinstance(obj, bytes):
        return str(len(obj)).encode() + b':' + obj
    elif isinstance(obj, str):
        return bencode(obj.encode())
    elif isinstance(obj, list):
        encoded_items = [bencode(item) for item in obj]
        return b'l' + b''.join(encoded_items) + b'e'
    elif isinstance(obj, dict):
        encoded_key_values = []
        for key, value in obj.items():

            encoded_key_values.append(bencode(key))
            encoded_key_values.append(bencode(value))

        return b'd' + b''.join(encoded_key_values) + b'e'
    else:
        return b''
