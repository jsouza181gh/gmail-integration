import pytest

from src.chat.audio import MAX_AUDIO_BYTES, parse_wav
from tests.chat_fakes import wav


def test_parse_wav_accepts_16k_mono() -> None:
    info = parse_wav(wav(2.5))
    assert (info.sample_rate, info.channels, info.bits, info.duration_ms) == (16000, 1, 16, 2500)
    assert MAX_AUDIO_BYTES == 4 * 1024 * 1024


@pytest.mark.parametrize(
    "data",
    [
        b"OggS" + b"\x00" * 100,
        wav(1, channels=2),
        wav(1, bits=24),
        wav(0.2),
        wav(121, rate=8000),
        wav(1)[:30],
        wav(1, fmt=3),
        wav(1, rate=96000),
    ],
)
def test_parse_wav_rejects(data: bytes) -> None:
    with pytest.raises(ValueError):
        parse_wav(data)
