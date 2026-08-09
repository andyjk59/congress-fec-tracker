"""Shared HTTP GET wrapper with retry and a true hard wall-clock deadline.

requests' timeout=N only bounds time *between* reads, not total request
duration -- a connection that keeps the socket open while trickling data
(or nothing) can hang indefinitely without ever tripping it. This bit the
FEC backfill twice: a request sat ESTABLISHED with 0% CPU and zero
checkpoint progress for 30-55+ minutes before being noticed and killed
manually. Running the call in a worker thread and bounding
future.result() with its own timeout gives an actual ceiling -- if it's
exceeded, the abandoned thread is left to die on its own (Python can't
kill a thread), but the caller stops waiting and retries with a fresh
connection instead of hanging forever.
"""

import concurrent.futures
import time

import requests

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="http-get")


def get_with_retry(session: requests.Session, url: str, params: dict,
                    max_attempts: int = 8, read_timeout: int = 30, hard_timeout: int = 45) -> dict:
    resp = None
    for attempt in range(max_attempts):
        try:
            future = _EXECUTOR.submit(session.get, url, params=params, timeout=read_timeout)
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
