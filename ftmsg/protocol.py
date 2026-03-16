from __future__ import annotations

import json
import struct
from typing import Any


Frame = dict[str, Any]


def encode_frame(frame: Frame) -> bytes:
    body = json.dumps(frame, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(body)) + body


def decode_frame(data: bytes) -> Frame:
    return json.loads(data.decode("utf-8"))


async def read_frame(reader) -> Frame:
    header = await reader.readexactly(4)
    length = struct.unpack("!I", header)[0]
    payload = await reader.readexactly(length)
    return decode_frame(payload)


async def write_frame(writer, frame: Frame) -> None:
    writer.write(encode_frame(frame))
    await writer.drain()
