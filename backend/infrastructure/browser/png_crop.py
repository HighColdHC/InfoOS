"""Crop Chrome's 8-bit RGB/RGBA viewport PNG using only the standard library."""
import struct
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def crop_png(data: bytes, rect: tuple[float, float, float, float], viewport_width: float) -> bytes:
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('invalid_png')
    chunks, offset = {}, 8
    while offset < len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        if len(payload) != length:
            raise ValueError('invalid_png')
        chunks[kind] = chunks.get(kind, b'') + payload
        offset += length + 12
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunks[b'IHDR'])
    if depth != 8 or color not in (2, 6) or compression or filtering or interlace or viewport_width <= 0:
        raise ValueError('unsupported_png')
    bpp = 3 if color == 2 else 4
    stride = width * bpp
    raw = zlib.decompress(chunks[b'IDAT'])
    if len(raw) != height * (stride + 1):
        raise ValueError('invalid_png')
    scale = width / viewport_width
    x, y, w, h = rect
    left, top = max(0, int((x - 8) * scale)), max(0, int((y - 8) * scale))
    right, bottom = min(width, int((x + w + 8) * scale)), min(height, int((y + h + 8) * scale))
    if w < 40 or h < 40 or left >= right or top >= bottom:
        raise ValueError('invalid_qr_rectangle')
    previous, cropped = bytearray(stride), bytearray()
    for row in range(height):
        start = row * (stride + 1)
        mode, pixels = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        if mode not in range(5):
            raise ValueError('invalid_png_filter')
        for i in range(stride):
            a, b, c = pixels[i - bpp] if i >= bpp else 0, previous[i], previous[i - bpp] if i >= bpp else 0
            predictor = 0
            if mode == 1:
                predictor = a
            elif mode == 2:
                predictor = b
            elif mode == 3:
                predictor = (a + b) // 2
            elif mode == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
            pixels[i] = (pixels[i] + predictor) & 255
        if top <= row < bottom:
            cropped.extend(b'\x00' + pixels[left * bpp:right * bpp])
        previous = pixels
    header = struct.pack('>IIBBBBB', right - left, bottom - top, 8, color, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header) + _chunk(b'IDAT', zlib.compress(bytes(cropped))) + _chunk(b'IEND', b'')
