class BasicException(Exception):
    pass


class PeerConnectionFailed(BasicException):
    pass


class PeerDisconnected(BasicException):
    pass
