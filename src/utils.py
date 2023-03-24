import struct


async def send_interested_message(writer) -> None:
    interested_msg = struct.pack("!Ib", 1, 2)
    writer.write(interested_msg)
    await writer.drain()


def get_file(piece, files, piece_size) -> int:
    piece_start = piece.index * piece_size
    file_index = 0
    for index, file in enumerate(files):
        if file['start'] <= piece_start:
            file_index = index
        else:
            break
    return file_index
