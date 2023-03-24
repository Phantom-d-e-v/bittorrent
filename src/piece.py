import hashlib
import math


class Piece:
    def __init__(self, index, hash_value, size):
        self.hash = hash_value
        self.index = index
        self.blocks = []
        self.size = size

        num_blocks = math.ceil(size / 2 ** 14)
        # 0 - Not downloaded, 1 - Pending, 2 - Downloaded
        # block - [status, index, offset, length, data]

        remaining_size = size
        for i in range(num_blocks):
            self.blocks.append([0, index, i * (2 ** 14), 2 ** 14, b''])
            remaining_size -= 2 ** 14
        if size % (2 ** 14) != 0:
            last_block = self.blocks[-1]
            last_block[3] = size % (2 ** 14)
            self.blocks[-1] = last_block

    def request_block_download(self):
        blocks_list = []
        for block in self.blocks:
            if block[0] == 0:
                blocks_list.append(block)

        if blocks_list:
            block = blocks_list[0]
            block[0] = 1
            return block

        return None

    def block_received(self, offset, data):
        block = None
        for i in self.blocks:
            if i[2] == offset:
                block = i
                break

        if block is not None:
            block[4] = data
            block[0] = 2

    def completed(self):
        for i in self.blocks:
            if i[0] != 2:
                return False

        return True

    def verify_piece(self):
        piece_data = b''
        for i in self.blocks:
            piece_data += i[4]

        piece_hash = hashlib.sha1(piece_data).digest()

        return piece_hash == self.hash

    def get_data(self):
        piece_data = b''
        for i in self.blocks:
            piece_data += i[4]

        return piece_data

    def clear_data(self):
        for i in self.blocks:
            i[4] = b''
