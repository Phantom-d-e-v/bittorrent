import asyncio
import hashlib
import random
import socket
import ssl
import struct
import sys
import time
import urllib
from socket import *

import bencodepy

from block_handler import BlockHandler
from info import MetaInfo
from peer import Peer
from seed import Seeder

DOWNLOAD_REQUEST_SIZE = 2 ** 14


class Torrent:
    def __init__(self, file, location=None, max_download=None, max_peers=None):
        self.meta_info = MetaInfo(file, location, max_download, max_peers)
        self.downloading_peers = []
        self.block_handler = BlockHandler(self.meta_info)
        self.last_tracker_request = 0
        self.unchoked_peers = []
        self.seeder = Seeder(self, self.block_handler)

    async def top_four(self):
        while True:
            for peer in self.unchoked_peers:
                await peer.send_choke()
            temp_peers = [peer for peer in self.downloading_peers]
            temp_peers.sort(key=lambda x: x.statistics["download_speed"])
            top_peers = temp_peers[-4:]
            for peer in top_peers:
                await peer.send_unchoke()
            self.unchoked_peers = top_peers
            await asyncio.sleep(10.0)

    def add_peer(self, peer):
        self.downloading_peers.append(peer)

    def make_request(self, meta_info, url):
        parsed_url = urllib.parse.urlparse(self.meta_info.data[b'announce'].decode())
        try:
            host, port = parsed_url.netloc.split(":")
        except ValueError:
            host = parsed_url.netloc
            port = None

        if parsed_url.scheme == 'https' or parsed_url.scheme == 'http':
            client = socket(AF_INET, SOCK_STREAM)

            if parsed_url.scheme == 'https':
                try:
                    client.connect((parsed_url.netloc, 443))
                except Exception:
                    client.close()
                    raise Exception("Failed while connecting to tracker")
                client = ssl.wrap_socket(client, keyfile=None, certfile=None, server_side=False,
                                         cert_reqs=ssl.CERT_NONE,
                                         ssl_version=ssl.PROTOCOL_SSLv23)
            elif parsed_url.scheme == 'http':
                try:
                    client.connect((host, int(port) if port else 80))
                except Exception:
                    client.close()
                    raise Exception("Failed while connecting to tracker")
            msg = f"GET /announce?{meta_info} HTTP/1.1\r\nHost: {host}\r\nnConnection: close\r\n\r\n"
            client.send(msg.encode())
            response = b''
            while True:
                data = client.recv(65535)
                if not data:
                    client.close()
                    break
                response += data
            index = response.find(b'\r\n\r\n')
            if index >= 0:
                return response[index + 4:]
            return response
        elif parsed_url.scheme == 'udp':
            client = socket(AF_INET, SOCK_DGRAM)
            transaction_id = random.getrandbits(16)
            msg = struct.pack('!qii', 0x41727101980, 0, transaction_id)
            client.sendto(msg, (host, int(port)))
            client.settimeout(10.0)
            while True:
                try:
                    data = client.recvfrom(1024)
                    break
                except:
                    client.close()
                    raise Exception("UDP Timeout")
            client.settimeout(120)
            try:
                decoded_data = struct.unpack("!iiq", data[0])
                if int(decoded_data[1]) != int(transaction_id):
                    raise Exception("Transaction ID does not match")
                transaction_id = random.getrandbits(16)
                msg = struct.pack('!qii20s20sqqqiiiih', decoded_data[2], 1, transaction_id, url['info_hash'],
                                  url['peer_id'].encode(), url['downloaded'], url['left'], url['uploaded'], 0, 0,
                                  transaction_id, -1, url['port'])
                client.sendto(msg, (host, int(port)))
                old_time = client.gettimeout()
                client.settimeout(15)
                while True:
                    try:
                        announce_data = client.recvfrom(65535)
                        break
                    except (Exception,):
                        client.settimeout(15)
                        client.sendto(msg, (host, int(port)))
                        old_time -= 15
                        if old_time < 0:
                            client.close()
                            raise Exception("Announce timed out")

                decoded_announce_data = struct.unpack(f"!iiiii{len(announce_data[0]) - 20}s", announce_data[0])
                if transaction_id != decoded_announce_data[1]:
                    raise Exception("Transaction ID does not match")
                announce_data_dict = {'complete': decoded_announce_data[4], 'incomplete': decoded_announce_data[3],
                                      'interval': decoded_announce_data[2], 'peers': decoded_announce_data[5]}
                response = bencodepy.encode(announce_data_dict)
                return response
            except Exception as e:
                client.close()
                raise Exception(e)

    def create_url(self):
        info = self.meta_info.data[b'info']
        self.meta_info.left = self.meta_info.length if self.meta_info.left == 0 else \
            self.meta_info.length - self.block_handler.download_amount

        encoded_info = bencodepy.encode(info)
        info_hash = hashlib.sha1(encoded_info).digest()
        self.meta_info.info_hash = info_hash
        peer_id = '-PC3153-' + ''.join(
            [str(random.randint(0, 9)) for _ in range(12)]) if self.meta_info.id == "" else self.meta_info.id
        self.meta_info.id = peer_id
        url = {'info_hash': info_hash, 'peer_id': self.meta_info.id, 'port': 6885, 'left': self.meta_info.left,
               'uploaded': 0, 'downloaded': self.block_handler.download_amount, 'compact': 1}
        encoded_url = urllib.parse.urlencode(url)
        return encoded_url, url

    def parse_response(self, response_data):
        decoded_response = bencodepy.decode(response_data)
        peers = decoded_response[b'peers']
        offset = 0
        while offset < len(peers):
            ip_number = struct.unpack_from("!I", peers, offset)[0]
            ip = inet_ntoa(struct.pack("!I", ip_number))
            offset += 4
            port = struct.unpack_from("!H", peers, offset)[0]
            offset += 2
            self.meta_info.peers.add((ip, port))

    async def message_peers(self):
        for index, value in enumerate(self.meta_info.peers):
            if index < int(self.meta_info.max_peers):
                self.downloading_peers.append(
                    Peer(self.meta_info.id, self.meta_info.info_hash, self.meta_info.num_pieces, value[0], value[1],
                         self.block_handler))

        top_peers_task = asyncio.create_task(self.top_four())

        while True:
            await asyncio.sleep(5)
            for peer in self.downloading_peers:
                if peer.task.cancelled():
                    self.downloading_peers.remove(peer)
                    self.block_handler.remove_bitfield(peer.remote_id)
                    self.meta_info.peers.discard((peer.ip, peer.port))

            current_time = int(round(time.time() * 1000))
            if self.last_tracker_request + 120000 < current_time:
                await asyncio.to_thread(self.tracker_request)
            if self.block_handler.is_complete():
                top_peers_task.cancel()
                break

        print("COMPLETED")

    def tracker_request(self):
        self.last_tracker_request = int(round(time.time() * 1000))
        encoded_url, url = self.create_url()
        i = 0
        response_data = ""
        while i < len(self.meta_info.trackers):
            try:
                self.meta_info.data[b'announce'] = self.meta_info.trackers[i][0]
                response_data = self.make_request(encoded_url, url)
                break
            except (Exception,):
                i += 1
                if i == len(self.meta_info.trackers):
                    print("Failed to establish connection with trackers")
                    sys.exit(0)

        self.parse_response(response_data)

    async def torrent_start(self):
        self.tracker_request()
        await self.message_peers()
