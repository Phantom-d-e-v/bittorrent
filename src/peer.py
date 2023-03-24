import asyncio
import struct
import time

import bitstring

from utils import send_interested_message


class Peer:
    def __init__(self, peer_id, info_hash, num_pieces, ip, port, block_handler, peer=None):
        self.ip = ip
        self.port = port
        self.am_choking = 1
        self.am_interested = 0
        self.peer_choking = 1
        self.peer_interested = 0
        self.downloading = 0
        self.__info_hash = info_hash
        self.__id = peer_id
        self.remote_id = ''
        self.num_pieces = num_pieces
        self.block_handler = block_handler
        self.task = asyncio.ensure_future(self.start())
        self.data_downloaded = 0
        self.statistics = {"send_time": 0, "receive_time": 0, "download_speed": 0, "prev_download_speed": 0}
        self.reader = peer[0] if peer else None
        self.writer = peer[1] if peer else None

    async def handshake(self, reader, writer):
        msg = struct.pack("!b19sq20s20s", 19, "BitTorrent protocol".encode(), 0, self.__info_hash, self.__id.encode())

        writer.write(msg)
        await writer.drain()

        resp_buffer = b''
        tries = 0
        while len(resp_buffer) < 68 and tries < 10:
            data = await reader.read(65535)
            if data:
                resp_buffer += data
            tries += 1

        if tries >= 10:
            writer.close()
            self.task.cancel()
            return None

        decoded_msg = struct.unpack("!b19sq20s20s", resp_buffer[:68])
        if decoded_msg[3] != self.__info_hash:
            writer.close()
            self.task.cancel()
            return None

        self.remote_id = decoded_msg[4]
        return resp_buffer[68:]

    async def send_unchoke(self):
        if self.peer_interested == 1:
            self.am_choking = 0
            msg = struct.pack("!ib", 1, 1)
            self.writer.write(msg)
            await self.writer.drain()

    async def send_choke(self):
        self.am_choking = 1
        msg = struct.pack("!ib", 1, 0)
        self.writer.write(msg)
        await self.writer.drain()

    def download_speed(self):
        self.statistics["prev_download_speed"] = self.statistics["prev_download_speed"] if \
            self.statistics["download_speed"] == 0 else self.statistics["download_speed"]

        self.statistics["download_speed"] = max(0.0, self.data_downloaded / (
                self.statistics["receive_time"] - self.statistics["send_time"]))

    async def send_bitfield(self):
        bitfield = self.block_handler.my_bitfield
        bitfield_bin = bitfield.bin
        for i in range(len(bitfield_bin) % 8):
            bitfield_bin.append('0b0')
        bitfield_bytes = bitfield_bin.bytes

        msg = struct.pack("!ib", 1 + len(bitfield_bin), 5)
        msg += bitfield_bytes
        self.writer.write(msg)
        await self.writer.drain()

    async def start(self):
        try:
            resp_buffer = b''
            if not self.reader:
                self.reader, self.writer = await asyncio.open_connection(self.ip, self.port)

                resp_buffer = await self.handshake(self.reader, self.writer)
                if resp_buffer is None:
                    self.writer.close()
                    self.task.cancel()
                    return None

                await send_interested_message(self.writer)
                self.am_interested = 1
            else:
                tries = 0
                while len(resp_buffer) < 68 and tries < 10:
                    data = await self.reader.read(65535)
                    if data:
                        resp_buffer += data
                    tries += 1

                if tries >= 10:
                    self.writer.close()
                    self.task.cancel()
                    return None

                decoded_msg = struct.unpack("!b19sq20s20s", resp_buffer[:68])
                if decoded_msg[3] != self.__info_hash:
                    self.writer.close()
                    self.task.cancel()
                    return None

                self.remote_id = decoded_msg[4]
                msg = struct.pack("!b19sq20s20s", 19, "BitTorrent protocol".encode(), 0, self.__info_hash,
                                  self.__id.encode())

                self.writer.write(msg)
                await self.writer.drain()
                await self.send_bitfield()
                resp_buffer = resp_buffer[68:]

            while True:
                try:
                    recv_data = await asyncio.wait_for(self.reader.read(65535), 10.0)
                    if recv_data:
                        resp_buffer += recv_data
                    if not resp_buffer and not recv_data:
                        self.writer.close()
                        self.task.cancel()
                        return None
                    while len(resp_buffer) > 4:
                        msg_len = struct.unpack("!i", resp_buffer[:4])[0]
                        msg_id = int(resp_buffer[4])
                        if msg_len == 0:
                            resp_buffer = resp_buffer[4:]
                        elif msg_len == 1:
                            if msg_id == 0:
                                self.peer_choking = 1
                            elif msg_id == 1:
                                if self.peer_choking == 1:
                                    self.peer_choking = 0
                            elif msg_id == 2:
                                self.peer_interested = 1
                            elif msg_id == 3:
                                self.peer_interested = 0
                            resp_buffer = resp_buffer[4 + msg_len:]
                        elif msg_len == 5:
                            if len(resp_buffer) >= 4 + msg_len:
                                if msg_id == 4:
                                    piece_index = struct.unpack("!i", resp_buffer[5:9])[0]
                                    self.block_handler.update_bitfield(self.remote_id, piece_index)
                                    resp_buffer = resp_buffer[4 + msg_len:]
                            else:
                                break
                        elif msg_len == 13 and msg_id == 8:
                            print(f"{self.remote_id} Cancel")
                            self.downloading = 0
                            resp_buffer = resp_buffer[4 + msg_len:]
                        elif msg_len == 13 and msg_id == 6:
                            print(f"Received request from {self.remote_id}")
                            resp = struct.unpack("!ibiii", resp_buffer[:4 + msg_len])
                            data = await self.block_handler.read(resp[2], resp[3], resp[4])
                            msg = struct.pack("!ibii", 9 + len(data), resp[2], resp[3])
                            msg += data
                            self.writer.write(msg)
                            await self.writer.drain()
                            resp_buffer = resp_buffer[4 + msg_len:]
                        elif msg_id == 5:
                            if len(resp_buffer) >= 4 + msg_len:
                                recv_bitfield = resp_buffer[5:5 + msg_len]
                                resp_buffer = resp_buffer[4 + msg_len:]
                                bitfield = bitstring.BitArray(recv_bitfield)
                                self.block_handler.add_bitfield(self.remote_id, bitfield)
                            else:
                                break
                        elif msg_id == 7:
                            if len(resp_buffer) >= 4 + msg_len:
                                self.statistics["receive_time"] = int(round(time.time() * 1000))
                                self.data_downloaded = len(resp_buffer[13:msg_len + 4])
                                self.download_speed()
                                block_index, block_begin = struct.unpack("!ii", resp_buffer[5:13])
                                await self.block_handler.block_received(block_index, block_begin,
                                                                        resp_buffer[13:msg_len + 4])
                                self.downloading = 0
                                resp_buffer = resp_buffer[msg_len + 4:]
                            else:
                                break
                        elif msg_len == 3 and msg_id == 9:
                            resp_buffer = resp_buffer[4 + msg_len:]

                        if self.peer_choking == 0 and self.am_interested == 1 and self.downloading == 0 and \
                                self.block_handler.download_speed <= int(self.block_handler.download_limit):
                            block = self.block_handler.find_block(self.remote_id)
                            if block:
                                self.statistics["send_time"] = int(round(time.time() * 1000))
                                self.downloading = 1
                                request_msg = struct.pack("!ibiii", 13, 6, block[1], block[2], block[3])
                                self.writer.write(request_msg)
                                await self.writer.drain()
                            else:
                                break
                except ConnectionResetError:
                    self.writer.close()
                    await self.writer.wait_closed()
                    self.task.exception()
                    self.task.cancel()
                    return None
                except (Exception,):
                    self.task.exception()
                    pass

        except (Exception,):
            self.task.exception()
            self.task.cancel()
            return None
