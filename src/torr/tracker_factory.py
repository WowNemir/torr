from urllib.parse import urlparse

from torr.configuration import CONFIGURATION
from torr.http_tracker import HTTPTracker
from torr.tracker import Tracker
from torr.udp_tracker import UDPTracker


class TrackerFactory:
    @staticmethod
    def create_tracker(url: str):
        """
        Check the scheme of the url,
        and decide which type of tracker to create.
        :param url: url of the tracker (HTTP/UDP)
        :return: Tracker
        """
        parsed = urlparse(url)
        if "http" in parsed.scheme.lower():
            return HTTPTracker(url)
        elif "udp" in parsed.scheme.lower():
            if not CONFIGURATION.tcp_only:
                return UDPTracker(url)
        else:
            raise NotImplementedError("Unsupported protocol")

    @staticmethod
    def create_trackers(urls: list[str]) -> list[Tracker]:
        """
        Create trackers from the given url list.
        Current options are HTTP/UDP.
        """
        trackers = []
        for url in urls:
            tracker = TrackerFactory.create_tracker(url[0])
            trackers.append(tracker)

        return trackers
