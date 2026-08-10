"""Shared HTTP GET wrapper with retry and a true hard wall-clock deadline.

Two compounding issues bit the FEC backfill three times: a request sitting
ESTABLISHED with 0% CPU and zero checkpoint progress for 30-55+ minutes.

1. requests' timeout=N only bounds time *between* reads, not total request
   duration -- a connection that keeps the socket open while trickling data
   (or nothing) can hang indefinitely without ever tripping it. Fixed by
   running the call in a worker thread and bounding future.result() with
   its own timeout, giving an actual wall-clock ceiling.

2. That fix alone wasn't enough: the caller was reusing a single
   requests.Session() for an entire multi-hour run. If one hung request
   poisons that session's connection pool (holds a checked-out connection
   forever), every *subsequent* attempt -- even fresh ones from a new retry
   -- can block trying to acquire a connection from the same exhausted
   pool, which bypasses both the per-request timeout and the wall-clock
   deadline above (evidence: zero "SKIPPING" log lines despite the retry
   math predicting a skip within ~16 minutes worst-case). Fixed by never
   sharing a session across attempts -- each attempt gets its own,
   discarded afterward. This costs a fresh TCP+TLS handshake per request
   instead of connection-pool reuse, which is a small, acceptable price
   next to a process silently hanging for the better part of an hour.
"""

import concurrent.futures
import time

import requests

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="http-get")


def _get_once(url: str, params: dict, read_timeout: int):
    with requests.Session() as session:
        return session.get(url, params=params, timeout=read_timeout)


def get_with_retry(url: str, params: dict,
                    max_attempts: int = 8, read_timeout: int = 30, hard_timeout: int = 45) -> dict:
    resp = None
    for attempt in range(max_attempts):
        try:
            future = _EXECUTOR.submit(_get_once, url, params, read_timeout)
            resp = future.result(timeout=hard_timeout)
        except (requests.exceptions.RequestException, concurrent.futures.TimeoutError):
            if attempt == max_attempts - 1:
                raise
            time.sleep(min(5 * 2 ** attempt, 300))
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = int(resp.headers.get("Retry-After", min(5 * 2 ** attempt, 300)))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}
