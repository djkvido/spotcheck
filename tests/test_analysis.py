import random

from spotcheck.analysis import ber_curve, downsample_min


def naive_ber_curve(long_fp, short_fp):
    width = len(short_fp)
    return [
        sum(bin(a ^ b).count("1") for a, b in zip(long_fp[o : o + width], short_fp, strict=True)) / (32 * width)
        for o in range(len(long_fp) - width + 1)
    ]


def random_fp(n, seed):
    rng = random.Random(seed)
    return tuple(rng.getrandbits(32) for _ in range(n))


def test_ber_curve_matches_naive_implementation():
    long_fp, short_fp = random_fp(300, 1), random_fp(40, 2)
    fast, slow = ber_curve(long_fp, short_fp), naive_ber_curve(long_fp, short_fp)
    assert len(fast) == len(slow) == 261
    assert all(abs(a - b) < 1e-12 for a, b in zip(fast, slow, strict=True))


def test_embedded_preview_is_found_at_its_offset():
    long_fp = random_fp(500, 3)
    preview = long_fp[120:360]
    curve = ber_curve(long_fp, preview)
    assert curve.index(min(curve)) == 120
    assert min(curve) == 0.0
    assert 0.45 < sorted(curve)[len(curve) // 2] < 0.55


def test_argument_order_does_not_matter():
    a, b = random_fp(50, 4), random_fp(200, 5)
    assert ber_curve(a, b) == ber_curve(b, a)


def test_empty_input_gives_empty_curve():
    assert ber_curve((), random_fp(10, 6)) == []


def test_downsample_keeps_minimum_and_length():
    curve = [0.5] * 1000
    curve[637] = 0.02
    reduced = downsample_min(curve, points=160)
    assert len(reduced) == 160
    assert min(reduced) == 0.02


def test_downsample_short_curve_is_returned_rounded():
    assert downsample_min([0.12345, 0.5]) == [0.123, 0.5]
