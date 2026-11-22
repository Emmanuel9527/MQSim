"""DiskANN expansion-log (explog) helpers shared by several studies.

Moved out of server_sweeps/analyze_prefetch_prediction.py (2026-11-22, code unchanged):
  REC             record dtype of expansions.bin written by server_sweeps/diskann_policy_sweep.sh
  node_sector_fn  infer nodes-per-sector of the default layout from a trace access log
  CandList        replica of diskann::NeighborPriorityQueue (capacity L, ordered by (distance, id))
Scripts in another folder add microbench/common to sys.path and `from explog import ...`.
"""
import bisect
import numpy as np

REC = np.dtype([('L', '<u4'), ('q', '<u4'), ('it', '<u4'), ('parent', '<u4'), ('nbr', '<u4'), ('d', '<f4'), ('f', 'u1')])


def node_sector_fn(access):
    """Default DiskANN layout: sector = 1 + id // nodes_per_sector. Infer nodes_per_sector from the access log."""
    ids = np.array([a[0] for a in access]); secs = np.array([a[1] for a in access])
    m = secs > 1
    for n in range(1, 257):
        if np.all(1 + ids[m] // n == secs[m]):
            return n
    raise SystemExit('Could not infer nodes_per_sector (non-default layout?)')


class CandList:
    """Replica of diskann::NeighborPriorityQueue for unvisited inserts (ordered by (distance, id), capacity L)."""

    def __init__(self, L):
        self.L, self.keys, self.exp = L, [], []

    def insert(self, d, i):
        k = (d, i)
        if len(self.keys) == self.L and self.keys[-1] < k:
            return False
        pos = bisect.bisect_left(self.keys, k)
        self.keys.insert(pos, k); self.exp.insert(pos, False)
        if len(self.keys) > self.L:
            self.keys.pop(); self.exp.pop()
        return pos < self.L

    def unexpanded(self, n, skip=0):
        out = []
        for (d, i), e in zip(self.keys, self.exp):
            if not e:
                if skip: skip -= 1; continue
                out.append(i)
                if len(out) == n: break
        return out

    def take(self, W):
        out = []
        for j, e in enumerate(self.exp):
            if not e:
                self.exp[j] = True; out.append(self.keys[j][1])
                if len(out) == W: break
        return out
