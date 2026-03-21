"""Application-wide constants."""

import os

CPU_COUNT = os.cpu_count() or 4

# I/O-bound workloads benefit from more threads than CPU cores.
# 4× is a common heuristic for network-heavy tasks, capped at 64.
IO_WORKERS = min(64, CPU_COUNT * 4)
