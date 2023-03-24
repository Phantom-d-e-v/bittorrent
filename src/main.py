import argparse
import asyncio
import sys

from torrent import Torrent


def exception_handler(loop, context):
    # Log errors here
    pass


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='BitTorrent Client')
    parser.add_argument("file", help="Define location to store")
    parser.add_argument("-l", "--location", help="Define location to store")
    parser.add_argument("-md", "--max-download", help="Define location to store")
    parser.add_argument("-mp", "--max-peers", help="Define location to store")

    args = parser.parse_args()

    a = Torrent(args.file, args.location, args.max_download, args.max_peers)
    event_loop = asyncio.get_event_loop()
    task = event_loop.create_task(a.torrent_start())

    try:
        event_loop.set_exception_handler(exception_handler)
        event_loop.run_until_complete(task)
    except asyncio.CancelledError:
        print('Event loop was canceled')
