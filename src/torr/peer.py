import ipaddress
import logging
import math
import random
import select
import socket
import struct
import threading

from bitstring import BitArray

from torr.configuration import CONFIGURATION
from torr.exceptions import (
    AllPeersChocked,
    NoPeersHavePiece,
    PeerConnectionFailed,
    PeerDisconnected,
)
from torr.message import BitField, Handshake, HaveMessage, Message, MessageTypes
from torr.message_factory import MessageFactory


class Peer:
    def __init__(self, ip: str, port: int, _id: str = "00000000000000000000"):
        self.ip = ip
        self.port = port
        self.id = _id
        self.connected = False  # only after handshake this will be true
        self.handshake = None  # Handshake still have not happened
        self.is_choked = True  # By default the client is choked
        self.bitfield: BitArray = BitArray()

        if type(ipaddress.ip_address(ip)) is ipaddress.IPv6Address:
            self.socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        else:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self.socket.settimeout(CONFIGURATION.timeout)

    def __str__(self):
        return f"{self.id} {self.ip}:{self.port}"

    def connect(self):
        """
        Connect to the target client
        """
        try:
            self.socket.connect((self.ip, self.port))
        except OSError as e:
            raise PeerConnectionFailed(f"Failed to connect: {str(e)}") from e

    def do_handshake(self, my_id, info_hash):
        """
        Do handshake with fellow peer
        """
        self.handshake = Handshake(my_id, info_hash)
        handshake_bytes = self.handshake.to_bytes()

        self.socket.send(handshake_bytes)

        response = self.receive_message()
        self.verify_handshake(response)

    def verify_handshake(self, message) -> bool:
        if self.handshake == message:
            self.connected = True
            return True
        return False

    def set_bitfield(self, bitfield: BitField):
        self.bitfield = bitfield.bitfield

    def set_have(self, have: HaveMessage):
        if have.index < self.bitfield.length:
            self.bitfield[have.index] = True
        else:
            logging.getLogger("BitTorrent").info(f"Have message {have.index} smaller then {self.bitfield.length}")

    def receive_message(self) -> Message:
        # After handshake
        # myid = random.randint(0, 65536)
        try:
            packet_length = self.socket.recv(1)

        except OSError as e:
            raise PeerDisconnected from e

        if packet_length == b"":
            logging.getLogger("BitTorrent").debug(f"Client in ip {self.ip} with id {self.id} disconnected")
            self.socket.close()
            raise PeerDisconnected

        if self.connected:
            packet_length = packet_length + self.socket.recv(3)
            while len(packet_length) < 4:
                odd = 4 - len(packet_length)
                packet_length = packet_length + self.socket.recv(odd)
                logging.getLogger("BitTorrent").error(f"Setting size again in {self}, length: {packet_length}")

            try:
                length = struct.unpack(">I", packet_length)[0]  # Big endian integer
            except struct.error as e:
                raise struct.error from e
            data = self.socket.recv(length)

            while len(data) != length:
                odd = length - len(data)
                data += self.socket.recv(odd)

            return MessageFactory.create_message(data)

        else:
            protocol_len: int = struct.unpack(">B", packet_length)[0]
            handshake_bytes = self.socket.recv(protocol_len + CONFIGURATION.handshake_stripped_size)

            return MessageFactory.create_handshake_message(packet_length + handshake_bytes)

    def send_message(self, message: Message):
        # logging.getLogger('BitTorrent').debug(f'Sending message {type(message)} to {self}')
        if not self.connected:
            pass
        message_bytes = message.to_bytes()
        try:
            self.socket.send(message_bytes)
        except OSError as e:
            raise PeerDisconnected from e

    def set_choked(self):
        self.is_choked = True

    def set_unchoked(self):
        self.is_choked = False

    def have_piece(self, piece):
        if piece.index < self.bitfield.length:
            return self.bitfield[piece.index]
        else:
            return False


class PeersManager:
    def __init__(self, max_peers):
        self.max_peers = max_peers
        self.peers: list[Peer] = []
        self.connected_peers: list[Peer] = []

    def add_peers(self, peers: list[Peer]):
        """
        Add peer to the list (still not connected)
        """
        self.peers += peers

    def remove_peer(self, peer):
        """
        Remove peer from the 'connected_peers' list.
        The reason why twin-like function not exists for the
        'peers' list resides in the fact we don't really care
        from this list, while we are very care form the connected_peers
        list, because we use in the receive_message function later.
        """
        if peer in self.connected_peers:
            self.connected_peers.remove(peer)

    def add_peer(self, peer: Peer):
        """
        Add peer to the list (still not connected)
        """
        self.peers.append(peer)

    def _send_handshake(self, my_id, info_hash, peer):
        """
        Send handshake to the given peer.
        NOTE: this function is BLOCKING.
        it waits until handshake response received, and failed otherwise.
        """
        try:
            peer.connect()
        except PeerConnectionFailed:
            return
        try:
            # Send the handshake to peer
            logging.getLogger("BitTorrent").info(f"Trying handshake with peer {peer.ip}")

            peer.do_handshake(my_id, info_hash)

            # Consider it as connected client
            self.connected_peers.append(peer)

            logging.getLogger("BitTorrent").debug(
                f"Adding peer {peer} which is {len(self.connected_peers)}/{self.max_peers}"
            )

        except (OSError, PeerDisconnected):
            pass

    def send_handshakes(self, my_id, info_hash):
        """
        Send handshake to all clients by create polls of threads
        That each one of them sending handshake to a constant number
        of peers. MAX_HANDSHAKE_THREADS decide the max peers to send
        handshake in each thread. big value will cause long run time
        for each thread and less threads, small value will cause for
        less run time to each thread and bigger number of threads.
        """
        # Create handshake thread for each peer
        handshake_threads = []
        for peer in self.peers:
            thread = threading.Thread(target=self._send_handshake, args=(my_id, info_hash, peer))
            handshake_threads.append(thread)

        number_of_polls = int(len(handshake_threads) / CONFIGURATION.max_handshake_threads) + 1

        for i in range(1, number_of_polls + 1):
            logging.getLogger("BitTorrent").debug(f"Poll number {i}/{number_of_polls}")
            poll = handshake_threads[: CONFIGURATION.max_handshake_threads]

            # Execute threads
            for thread in poll:
                thread.start()

            # Wait for them to finish
            for thread in poll:
                thread.join()

            if len(self.connected_peers) >= self.max_peers:
                logging.getLogger("BitTorrent").info(f"Reached max connected peers of {self.max_peers}")
                break

            # Slice the handshake threads
            del handshake_threads[: CONFIGURATION.max_handshake_threads]

        logging.getLogger("BitTorrent").info(f"Total peers connected: {len(self.connected_peers)}")

    def receive_messages(self) -> dict[Peer, MessageTypes]:
        """
        Receive new messages from clients
        """

        # Check for new readable sockets from the connected peers
        sockets = [peer.socket for peer in self.connected_peers]  # The bug resides in here...
        readable, _, _ = select.select(sockets, [], [])

        peers_to_message = {}
        # Extract peer from given sockets
        for _peer in self.connected_peers:
            for should_read in readable:
                if _peer.socket == should_read:
                    peers_to_message[_peer] = None

        # Receive messages from all the given peers
        for peer in peers_to_message:
            try:
                message = peer.receive_message()
                peers_to_message[peer] = message
            except PeerDisconnected:
                logging.getLogger("BitTorrent").debug(f"Peer {peer} while waiting for message")
                self.remove_peer(peer)
                return self.receive_messages()

        return peers_to_message

    def get_random_peer_by_piece(self, piece):
        """
        Get random peer having the given piece
        Will check at the beginning if all peers are choked,
        And choose randomly one of the peers that have the
        piece (By looking at each peer bitfiled).
        """
        peers_have_piece = []

        # Check if all the peers choked
        all_is_chocked = math.prod([peer.is_choked for peer in self.connected_peers])
        if all_is_chocked:
            raise AllPeersChocked  # If they are, then even if they have the piece it's not relevant

        # Check from all the peers who have the piece
        for peer in self.connected_peers:
            if peer.have_piece(piece) and not peer.is_choked:
                peers_have_piece.append(peer)

        # If we left with any peers, shuffle from them
        if peers_have_piece:
            return random.choice(peers_have_piece)

        # If we reached so far... then no peer founded
        raise NoPeersHavePiece

    @property
    def num_of_unchoked(self):
        """
        Count the number of unchoked peers
        """
        count = 0
        for peer in self.connected_peers:
            if not peer.is_choked:
                count += 1

        return count
