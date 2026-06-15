from __future__ import annotations

import json
import struct
from typing import Any


Frame = dict[str, Any]
MAX_FRAME_SIZE = 65536
FILE_CHUNK_SIZE = 45 * 1024  # ~60KB after base64, fits in MAX_FRAME_SIZE


def encode_frame(frame: Frame) -> bytes:
    body = json.dumps(frame, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_FRAME_SIZE:
        raise ValueError(f"frame too large: {len(body)} > {MAX_FRAME_SIZE}")
    return struct.pack("!I", len(body)) + body


def decode_frame(data: bytes) -> Frame:
    return json.loads(data.decode("utf-8"))


async def read_frame(reader) -> Frame:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    if length > MAX_FRAME_SIZE:
        raise ValueError(f"frame size {length} exceeds maximum {MAX_FRAME_SIZE}")
    payload = await reader.readexactly(length)
    return decode_frame(payload)


async def write_frame(writer, frame: Frame) -> None:
    writer.write(encode_frame(frame))
    await writer.drain()
