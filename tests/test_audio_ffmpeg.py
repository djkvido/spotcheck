import array
import math
import random
import wave

import pytest

from spotcheck import analysis

pytestmark = pytest.mark.skipif(not analysis.ffmpeg_path(), reason="FFmpeg is not installed")
needs_chromaprint = pytest.mark.skipif(
    not analysis.ffmpeg_info()["chromaprint"], reason="FFmpeg is built without chromaprint"
)


def render(path, filtergraph, seconds):
    result = analysis.run_text(
        [
            analysis.ffmpeg(),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            filtergraph,
            "-t",
            str(seconds),
            "-ac",
            "2",
            "-b:a",
            "192k",
            str(path),
        ]
    )
    assert result.returncode == 0, result.stderr
    return path


@pytest.fixture()
def padded_tone(tmp_path):
    return render(
        tmp_path / "tone.mp3",
        "sine=frequency=440:sample_rate=44100,volume=enable='lt(t,3)+gt(t,13)':volume=0",
        15,
    )


def test_silence_edges_and_lossless_trim(padded_tone):
    length = analysis.duration(padded_tone)
    lead, tail = analysis.silence_edges(padded_tone, length)
    assert lead == pytest.approx(3.0, abs=0.15)
    assert tail == pytest.approx(2.0, abs=0.15)

    keep_lead, keep_tail = 0.3, 0.5
    assert analysis.trim(padded_tone, lead - keep_lead, length - (lead - keep_lead) - (tail - keep_tail))
    assert analysis.duration(padded_tone) == pytest.approx(10 + keep_lead + keep_tail, abs=0.1)
    lead, tail = analysis.silence_edges(padded_tone, analysis.duration(padded_tone))
    assert lead <= keep_lead + 0.1
    assert tail <= keep_tail + 0.1


def test_truncated_file_is_detected(padded_tone, tmp_path):
    data = padded_tone.read_bytes()
    truncated = tmp_path / "truncated.mp3"
    truncated.write_bytes(data[: len(data) // 2])
    errors, decoded = analysis.decode_check(padded_tone)
    assert errors == 0
    assert decoded == pytest.approx(15, abs=0.2)
    _, decoded = analysis.decode_check(truncated)
    assert decoded < analysis.duration(truncated) - 5


def melody(path, seconds, seed, rate=11025):
    rng = random.Random(seed)
    samples = array.array("h")
    phase = 0.0
    while len(samples) < seconds * rate:
        freq = 220 * 2 ** (rng.randrange(24) / 12)
        for _ in range(int(rate * rng.uniform(0.15, 0.45))):
            samples.append(int(12000 * math.sin(phase)))
            phase += 2 * math.pi * freq / rate
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return path


@needs_chromaprint
def test_preview_is_located_by_fingerprint(tmp_path):
    full = melody(tmp_path / "full.wav", 90, seed=7)
    preview = tmp_path / "preview.wav"
    analysis.run_text([analysis.ffmpeg(), "-v", "error", "-y", "-ss", "40", "-t", "30", "-i", str(full), str(preview)])
    match = analysis.best_alignment(full, analysis.raw_fingerprint(preview))
    assert match["ber"] < 0.15
    assert match["at"] == pytest.approx(40, abs=0.5)

    other = melody(tmp_path / "other.wav", 90, seed=99)
    assert analysis.best_alignment(other, analysis.raw_fingerprint(preview))["ber"] > 0.35
