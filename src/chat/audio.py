"""Validation of the voice turns the panel records (16-bit PCM WAV, mono)."""
import struct

from src.llm.types import Schema

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MIN_MS = 500
MAX_MS = 120_000
RATES = range(8000, 48001)


class WavInfo(Schema):
    sample_rate: int
    channels: int
    bits: int
    duration_ms: int


def parse_wav(data: bytes) -> WavInfo:
    """Header facts of a 16-bit mono PCM WAV; ValueError (pt-BR reason) otherwise."""
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("áudio grande demais")
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("o áudio precisa ser WAV")
    fmt: tuple[int, int, int, int] | None = None
    data_size: int | None = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4:pos + 8])
        body = pos + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(data):
                raise ValueError("cabeçalho WAV incompleto")
            audio_format, channels, rate, _, _, bits = struct.unpack(
                "<HHIIHH", data[body:body + 16]
            )
            fmt = (audio_format, channels, rate, bits)
        elif chunk_id == b"data":
            data_size = min(size, len(data) - body)
            break
        pos = body + size + (size % 2)
    if fmt is None or data_size is None:
        raise ValueError("cabeçalho WAV incompleto")
    audio_format, channels, rate, bits = fmt
    if audio_format != 1 or bits != 16:
        raise ValueError("o áudio precisa ser PCM de 16 bits")
    if channels != 1:
        raise ValueError("o áudio precisa ser mono")
    if rate not in RATES:
        raise ValueError("taxa de amostragem não suportada")
    duration_ms = data_size * 1000 // (rate * 2)
    if duration_ms < MIN_MS:
        raise ValueError("áudio curto demais")
    if duration_ms > MAX_MS:
        raise ValueError("áudio passa de 2 minutos")
    return WavInfo(sample_rate=rate, channels=channels, bits=bits, duration_ms=duration_ms)
