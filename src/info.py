import os

import bencodepy


class MetaInfo:
    def __init__(self, file, location=None, max_download=None, max_peers=None):
        self.file = file
        self.left = 0
        self.downloaded = 0
        self.uploaded = 0
        self.id = ""
        self.info_hash = ""
        self.peers = set()
        with open(self.file, "rb") as f:
            self.data = bencodepy.decode(f.read())

        self.num_pieces = int(len(self.data[b'info'][b'pieces']) / 20)

        if b'announce-list' in self.data:
            self.trackers = self.data[b'announce-list']
        else:
            self.trackers = []
            self.trackers.append(self.data[b'announce'])
            self.trackers = [self.trackers]
        self.mode = 1 if b'files' in self.data[b'info'].keys() else 0

        if self.mode == 0:
            self.length = self.data[b'info'][b'length']
        else:
            temp_len = 0
            for item in self.data[b'info'][b'files']:
                temp_len += item[b'length']
            self.length = temp_len

        if self.mode == 1:
            self.path = os.path.join(location if location else os.getcwd(), self.data[b'info'][b'name'].decode())
            os.mkdir(self.path)
        else:
            self.path = location if location else os.getcwd()

        self.files = []
        if self.mode == 1:
            for file in self.data[b'info'][b'files']:
                self.files.append(
                    {"path": file[b'path'], "length": file[b'length'], "downloaded": 0})
        else:
            self.files.append([self.data[b'info'][b'name']])

        self.max_download = max_download if max_download else 10000007
        self.max_peers = max_peers if max_peers else 10000007
