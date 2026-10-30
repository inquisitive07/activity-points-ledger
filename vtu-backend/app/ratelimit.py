"""Login brute-force guard: 5 failures per (email, IP) per 5 minutes. In-memory, so it applies per
API process and resets on restart; a shared store would be needed when running several API instances."""
import time
from collections import defaultdict, deque

from fastapi import HTTPException

WINDOW_SECONDS, MAX_FAILURES = 300, 5
_fails: dict[str, deque] = defaultdict(deque)


def _prune(key: str):
    q = _fails[key]
    while q and q[0] < time.monotonic() - WINDOW_SECONDS:
        q.popleft()
    return q


def check(key: str):
    if len(_prune(key)) >= MAX_FAILURES:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")


def record_failure(key: str):
    _prune(key).append(time.monotonic())


def reset(key: str):
    _fails.pop(key, None)


def clear():
    _fails.clear()
