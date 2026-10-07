"""Wait, without holding any lock, until the app's music admission rule would pass.

Usage: wait_admit.py MODEL(turbo|xl) [--planner] [--timeout-s N]. Exit 0 when admissible, 1 on timeout.
"""
import argparse
import sys
import time

from thundertalk.core import memory_policy, music
from thundertalk.core.music_process import ADMIT_FREE_PERCENT, ADMIT_LEFT_FRACTION, total_memory_mb


def required_percent(need_mb: int, total_mb: int) -> float:
    return max(ADMIT_FREE_PERCENT, 100 * (need_mb + ADMIT_LEFT_FRACTION * total_mb) / total_mb)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('model', choices=['turbo', 'xl'])
    ap.add_argument('--planner', action='store_true')
    ap.add_argument('--timeout-s', type=float, default=4 * 3600)
    a = ap.parse_args()
    m = music.TURBO if a.model == 'turbo' else music.XL
    total = total_memory_mb()
    # Two points of margin so free memory moving between this check and admit() rarely refuses.
    want = required_percent(music.ram_need_mb(m, a.planner), total) + 2
    start = time.monotonic()
    last = -1.0
    while True:
        free = memory_policy.free_percent()
        if free is None or free >= want:
            print(f'[wait_admit] {a.model} admissible: free {free}% >= {want:.1f}%', flush=True)
            return 0
        waited = time.monotonic() - start
        if waited > a.timeout_s:
            print(f'[wait_admit] timeout after {waited:.0f} s: free {free}% < {want:.1f}%', flush=True)
            return 1
        if waited - last >= 300:
            print(f'[wait_admit] waiting: free {free}% < {want:.1f}% ({waited:.0f} s)', flush=True)
            last = waited
        time.sleep(15)


if __name__ == '__main__':
    sys.exit(main())
