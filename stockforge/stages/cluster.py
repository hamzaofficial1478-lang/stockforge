"""Stage 2 — collapse 5,000 assets into design families.

This is the single biggest cost lever in the project and it costs nothing to
run. An Etsy shop with 5,000 uploads does not have 5,000 designs; it has a few
hundred templates, each shipped in six colourways, three sizes and with the
names swapped. Analysing all 5,000 with a vision model would be paying full
price to learn the same layout forty times.

So: group first, analyse one representative per group, then generate the rest
of the family from the same spec with a different palette and different text.
Which is exactly what an editable vector file is *for*.
"""

from __future__ import annotations

from dataclasses import dataclass


def hamming(a: str, b: str) -> int:
    return sum(c1 != c2 for c1, c2 in zip(a, b))


@dataclass
class Member:
    asset_id: str
    phash: str
    aspect: float
    width: int


class _DisjointSet:
    def __init__(self, keys: list[str]):
        self.parent = {k: k for k in keys}

    def find(self, k: str) -> str:
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster(members: list[Member], max_distance: int = 12, aspect_tol: float = 0.04) -> dict[str, list[Member]]:
    """Union-find over perceptual hash distance, gated on aspect ratio.

    Aspect is a hard gate on purpose: a 5x7 invite and a 2000x2000 Instagram
    square built from the same artwork are genuinely different layouts and want
    separate specs, even though they look alike to a hash.
    """
    ds = _DisjointSet([m.asset_id for m in members])
    buckets: dict[int, list[Member]] = {}
    for m in members:
        buckets.setdefault(round(m.aspect / max(aspect_tol, 1e-6)), []).append(m)

    # only compare within neighbouring aspect buckets — O(n^2) over 5k is
    # 12.5m comparisons, which is fine, but bucketing keeps it near-instant
    for key, bucket in buckets.items():
        pool = bucket + buckets.get(key - 1, []) + buckets.get(key + 1, [])
        for i, a in enumerate(bucket):
            for b in pool[i + 1:]:
                if a.asset_id == b.asset_id:
                    continue
                if abs(a.aspect - b.aspect) > aspect_tol:
                    continue
                if hamming(a.phash, b.phash) <= max_distance:
                    ds.union(a.asset_id, b.asset_id)

    groups: dict[str, list[Member]] = {}
    by_id = {m.asset_id: m for m in members}
    for m in members:
        groups.setdefault(ds.find(m.asset_id), []).append(by_id[m.asset_id])
    return groups


def representative(group: list[Member]) -> Member:
    """Analyse the largest member — most pixels means the cleanest read of the
    type and the finest motif detail."""
    return max(group, key=lambda m: m.width)
