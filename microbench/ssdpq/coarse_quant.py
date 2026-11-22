#!/usr/bin/env python3
"""Coarse vector codes for an SSD-side distance estimate (numpy only, no faiss).

Quantizers (SIFT, D = 128, uint8 base):
  PQ{M}x{b}   product quantization, M equal sub-spaces of D/M dims, 2^b centroids each; code = M*b/8 bytes.
              Distances by asymmetric lookup (query exact, base coded), as DiskANN does on the host.
  RBQ         1-bit code after a random rotation (RaBitQ-style estimator): 16 B of sign bits + 2 fp16 factors
              (|x - c| and <xbar, o>) = 20 B.
  exact       full-precision L2 (upper bound).
All distances are squared L2 estimates, so they are on the same scale as DiskANN's PQ distances.
"""
import numpy as np

BASE = '/home/emmanuel/projects/data/sift100m/base.100M.u8bin'
QUERY = '/home/emmanuel/projects/data/sift100m/bigann_query.u8bin'


def load_base():
    n, d = np.fromfile(BASE, dtype=np.uint32, count=2)
    return np.memmap(BASE, dtype=np.uint8, offset=8, mode='r', shape=(int(n), int(d)))


def load_query():
    n, d = np.fromfile(QUERY, dtype=np.uint32, count=2)
    return np.fromfile(QUERY, dtype=np.uint8, offset=8).reshape(int(n), int(d)).astype(np.float32)


def gather(base, ids, batch=1 << 20):
    """Rows of the memmap for sorted-unique ids (sorted access is much faster on a page-cached memmap)."""
    ids = np.asarray(ids, dtype=np.int64)
    out = np.empty((len(ids), base.shape[1]), dtype=np.float32)
    for s in range(0, len(ids), batch):
        out[s:s + batch] = base[ids[s:s + batch]]
    return out


def _assign(x, c, cn):
    # argmin_j |x - c_j|^2 = argmin_j (|c_j|^2 - 2 x.c_j)
    return np.argmin(cn[None, :] - 2.0 * x @ c.T, axis=1)


def kmeans(x, k, iters=20, seed=0):
    rng = np.random.default_rng(seed)
    c = x[rng.choice(len(x), k, replace=False)].copy()
    for _ in range(iters):
        a = _assign(x, c, (c * c).sum(1))
        cnt = np.bincount(a, minlength=k).astype(np.float32)
        s = np.zeros_like(c)
        np.add.at(s, a, x)
        empty = cnt == 0
        c[~empty] = s[~empty] / cnt[~empty, None]
        if empty.any():                                   # reseed empty clusters
            c[empty] = x[rng.choice(len(x), int(empty.sum()), replace=False)]
    return c


class PQ:
    def __init__(self, M, bits):
        self.M, self.bits, self.k = M, bits, 1 << bits
        self.name = f'PQ{M}x{bits}'
        self.bytes = M * bits / 8

    def train(self, x, iters=20):
        self.ds = x.shape[1] // self.M
        self.cb = np.stack([kmeans(x[:, m * self.ds:(m + 1) * self.ds], self.k, iters, seed=m) for m in range(self.M)])
        return self

    def encode(self, x, batch=1 << 18):
        codes = np.empty((len(x), self.M), dtype=np.uint8)
        for m in range(self.M):
            c = self.cb[m]; cn = (c * c).sum(1)
            for s in range(0, len(x), batch):
                codes[s:s + batch, m] = _assign(x[s:s + batch, m * self.ds:(m + 1) * self.ds], c, cn)
        self.codes = codes
        return self

    def dist(self, q, rows):
        """q: (D,) query; rows: indices into self.codes. Asymmetric squared L2."""
        lut = ((self.cb - q.reshape(self.M, 1, self.ds)) ** 2).sum(2)      # (M, k)
        return lut[np.arange(self.M)[None, :], self.codes[rows]].sum(1)


class RBQ:
    """1-bit RaBitQ-style code around the data centroid c, after a random orthogonal rotation P.
    o = P(x - c)/|x - c|, xbar = sign(o)/sqrt(D); store |x - c| and <xbar, o>.
    <o, qr> ~= <xbar, qr> / <xbar, o>, qr = P(q - c) (exact query); |q - x|^2 = |q-c|^2 + |x-c|^2 - 2|x-c|<o, q-c>."""
    name, bytes = 'RBQ1bit', 20

    def train(self, x, seed=0):
        D = x.shape[1]
        self.c = x.mean(0)
        self.P = np.linalg.qr(np.random.default_rng(seed).standard_normal((D, D)))[0].astype(np.float32)
        return self

    def encode(self, x):
        r = (x - self.c) @ self.P.T
        nr = np.linalg.norm(r, axis=1)
        o = r / np.maximum(nr, 1e-9)[:, None]
        s = np.where(o >= 0, 1.0, -1.0).astype(np.float32)
        D = x.shape[1]
        self.sign = np.packbits(s > 0, axis=1)
        self.nr = nr.astype(np.float16).astype(np.float32)
        self.xo = ((s * o).sum(1) / np.sqrt(D)).astype(np.float16).astype(np.float32)
        return self

    def dist(self, q, rows):
        D = len(q)
        qr = (q - self.c) @ self.P.T
        s = np.unpackbits(self.sign[rows], axis=1)[:, :D].astype(np.float32) * 2 - 1
        ip = (s @ qr) / np.sqrt(D) / np.maximum(self.xo[rows], 1e-6)
        return (qr * qr).sum() + self.nr[rows] ** 2 - 2 * self.nr[rows] * ip


class Exact:
    name, bytes = 'exact', 128

    def train(self, x):
        return self

    def encode(self, x):
        self.x = x
        return self

    def dist(self, q, rows):
        return ((self.x[rows] - q) ** 2).sum(1)


def make(spec):
    if spec == 'exact': return Exact()
    if spec == 'RBQ': return RBQ()
    m, b = spec[2:].split('x')
    return PQ(int(m), int(b))
