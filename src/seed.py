import asyncio

from peer import Peer


class Seeder:
    def __init__(self, torrent, block_handler):
        self.torrent = torrent
        self.block_handler = block_handler
        self.clients = []
        self.server = asyncio.start_server(client_connected_cb=self.client_connected, host="0.0.0.0", port=6885)

    async def client_connected(self, reader, writer):
        address = writer.get_extra_info('peername')
        peer = Peer(self.torrent.meta_info.id, self.torrent.meta_info.info_hash, self.torrent.meta_info.num_pieces,
                    address[0], address[1], self.block_handler, (reader, writer))
        self.clients.append(peer)
        self.torrent.add_peer(peer)
