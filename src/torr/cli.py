from argparse import ArgumentParser

from torr.client import TorrentClient


def main():
    parser = ArgumentParser(__package__)
    parser.add_argument("--torrent", type=str, help="Path of the Torrent file", required=True)
    parser.add_argument("--output-directory", default=".", type=str, help="Path to the output directory")
    args = parser.parse_args()

    torrent_client = TorrentClient(
        torrent=args.torrent,
        output_dir=args.output_directory,
    )

    torrent_client.start()


if __name__ == "__main__":
    main()
