class BasicException(Exception):
    pass


class PeerConnectionFailed(BasicException):
    pass


class PeerDisconnected(BasicException):
    pass


class PieceIsPending(BasicException):
    pass


class PieceIsFull(BasicException):
    pass


class NoPeersHavePiece(BasicException):
    pass


class AllPeersChocked(BasicException):
    pass
