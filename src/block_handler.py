import asyncio
import math
import os
import sys
import time
from collections import defaultdict

import aiofiles
import bitstring

from piece import Piece
from utils import get_file


class BlockHandler:
    def __init__(self, meta_info):
        self.num_pieces = meta_info.num_pieces
        self.my_bitfield = bitstring.BitArray(length=self.num_pieces)
        self.block_size = 2 ** 14
        self.piece_size = meta_info.data[b'info'][b'piece length']
        self.path = meta_info.path
        self.bitfields = {}
        self.block_per_piece = math.ceil(self.piece_size / self.block_size)
        self.length = meta_info.length
        self.torrent_hash = meta_info.data[b'info'][b'pieces']
        self.mode = meta_info.mode
        self.file_names = meta_info.files
        self.pending_pieces = self.piece_initialise()
        self.downloading_pieces = []
        self.downloading_blocks = []
        self.download_amount = 0
        #
        self.download_during_duration = 0
        self.download_speed = 0
        self.download_limit = meta_info.max_download
        #
        self.download_progress(self.download_amount)
        loop = asyncio.get_event_loop()
        self.download_task = loop.create_task(self.calculate_download_speed())

    def piece_initialise(self):
        tmp = []
        hash_offset = 0
        tmp_length = self.length
        for i in range(self.num_pieces):
            if tmp_length > self.piece_size:
                tmp.append(Piece(i, self.torrent_hash[hash_offset:hash_offset + 20], self.piece_size))
            else:
                tmp.append(Piece(i, self.torrent_hash[hash_offset:hash_offset + 20], tmp_length))
            hash_offset += 20
            tmp_length -= self.piece_size

        self.initialise_files()

        return tmp

    async def calculate_download_speed(self):
        while not self.is_complete():
            self.download_speed = max(round(self.download_during_duration / 1024, 2), 0)
            self.download_during_duration = 0
            await asyncio.sleep(1)

    def initialise_files(self):
        if self.mode == 1:
            self.file_names[0]["start"] = 0
            for i in range(1, len(self.file_names)):
                self.file_names[i]["start"] = self.file_names[i - 1]["length"] + self.file_names[i - 1]["start"]

    def add_bitfield(self, peer_id, bitfield):
        self.bitfields[peer_id] = bitfield

    def remove_bitfield(self, peer_id):
        if peer_id in self.bitfields.keys():
            del self.bitfields[peer_id]

    def update_bitfield(self, peer_id, index):
        if peer_id in self.bitfields:
            self.bitfields[peer_id][index] = 1

    def find_block(self, peer_id):
        if peer_id not in self.bitfields.keys():
            return None

        expired_block = self.get_expired(peer_id)
        if expired_block is None:
            block = self.get_next_block_from_piece(peer_id)
            if not block:
                index = self.rarest_piece_algorithm(peer_id)
                if index is not None:
                    block = self.pending_pieces[index].request_block_download()
                    if block:
                        self.downloading_blocks.append([block, int(round(time.time() * 1000))])
            return block
        else:
            return expired_block

    def rarest_piece_algorithm(self, peer_id):
        piece_count = defaultdict(int)
        for i in range(self.num_pieces):
            if self.my_bitfield[i] or not self.bitfields[peer_id][i] or i in self.downloading_pieces:
                continue
            for p in self.bitfields:
                if self.bitfields[p][i]:
                    piece_count[i] += 1

        if not piece_count:
            return None

        rarest_piece = min(piece_count, key=lambda q: piece_count[q])
        self.downloading_pieces.append(rarest_piece)
        return rarest_piece

    def get_next_block_from_piece(self, peer_id):
        for i in self.downloading_pieces:
            if self.bitfields[peer_id][i]:
                next_block = self.pending_pieces[i].request_block_download()
                if next_block:
                    self.downloading_blocks.append([next_block, int(round(time.time() * 1000))])
                    return next_block
        return None

    async def block_received(self, index, begin, data):
        for i in range(len(self.downloading_blocks)):
            if self.downloading_blocks[i][0][1] == index and self.downloading_blocks[i][0][2] == begin:
                del self.downloading_blocks[i]
                break

        piece = None
        for i in self.downloading_pieces:
            if i == index:
                piece = i
                break
        if piece is not None:
            self.download_amount += len(data)
            self.download_during_duration += len(data)
            self.download_progress(self.download_amount)

            self.pending_pieces[index].block_received(begin, data)
            if self.pending_pieces[index].completed():
                if self.pending_pieces[index].verify_piece():
                    await self.write(self.pending_pieces[index])
                    self.downloading_pieces.remove(index)
                    self.my_bitfield.set(1, index)
                    self.pending_pieces[index].clear_data()
                    if self.is_complete():
                        print("\n")

    def get_expired(self, peer_id):
        now = int(round(time.time() * 1000))
        for i in self.downloading_blocks:
            if self.bitfields[peer_id][i[0][1]]:
                if i[1] + 5000 < now:
                    i[1] = int(round(time.time() * 1000))
                    return i[0]
        return None

    async def read(self, index, begin, length):
        piece = self.pending_pieces[index]
        async with aiofiles.open(self.file_names[0][0], 'rb') as fd:
            pos = piece.index * self.piece_size + begin
            await fd.seek(pos, 0)
            await fd.read(length)

    async def write(self, piece):
        if self.mode == 0:
            path = os.path.join(self.path, self.file_names[0][0].decode())
            if not os.path.exists(path):
                with open(path, 'w'):
                    pass
            async with aiofiles.open(path, 'rb+') as fd:
                pos = piece.index * self.piece_size
                await fd.seek(pos, 0)
                await fd.write(piece.get_data())
        else:
            try:
                path = self.path
                data = piece.get_data()
                file_index = get_file(piece, self.file_names, self.piece_size)
                offset = 0
                while len(data) > 0:
                    temp_file = self.file_names[file_index + offset]
                    ###
                    length_to_write = min(len(data), temp_file['length'] - temp_file['downloaded'],
                                          abs(temp_file["start"] + temp_file["length"] - piece.index * self.piece_size),
                                          temp_file["length"])
                    try:
                        os.makedirs(os.path.join(path, b"/".join(temp_file["path"][:-1]).decode()), exist_ok=True)
                    except (Exception,):
                        pass
                    fd = os.open(os.path.join(path, b"/".join(temp_file["path"]).decode()), os.O_RDWR | os.O_CREAT)
                    pos = max(temp_file["start"], piece.index * self.piece_size) - temp_file["start"]
                    os.lseek(fd, pos, os.SEEK_SET)
                    os.write(fd, data[:length_to_write])
                    ###
                    self.file_names[file_index + offset]['downloaded'] += length_to_write
                    data = data[length_to_write:]
                    offset += 1
                    os.close(fd)
            except Exception as e:
                print(e)

    def is_complete(self):
        for i in self.my_bitfield:
            if not i:
                return False

        return True

    def download_progress(self, downloaded):
        done = int(50 * downloaded / self.length)
        percent = str(round(100 * downloaded / self.length, 2))
        download = f"{str(round(self.download_amount / 1024 ** 2, 2))} out of {str(round(self.length / 1024 ** 2, 2))}"
        progress_string = f"\r[{'█' * done}{'.' * (50 - done)}]   {percent}% ({download} MB) @ {round(self.download_speed, 2)} KB/s"
        sys.stdout.write(progress_string)
        sys.stdout.flush()
