"""Small statistical primitives shared by evaluation modules."""

import math


def wilson_interval(
    successes: int, sample_size: int, z: float = 1.96
) -> tuple[float, float] | None:
    if sample_size <= 0:
        return None
    proportion = successes / sample_size
    denominator = 1 + z * z / sample_size
    centre = proportion + z * z / (2 * sample_size)
    margin = z * math.sqrt(
        proportion * (1 - proportion) / sample_size
        + z * z / (4 * sample_size * sample_size)
    )
    return ((centre - margin) / denominator, (centre + margin) / denominator)
