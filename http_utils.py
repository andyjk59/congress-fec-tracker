"""Shared HTTP GET wrapper with retry and a true hard wall-clock deadline.

Three compounding issues bit the FEC backfill: a request looking hung, with
zero checkpoint progress for 30-74+ minutes, despite the process being alive.

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
   deadline above. Fixed by never sharing a session across attempts.

3. Still not the whole story: on a 429/5xx response, the code slept for
   whatever value the server's Retry-After header specified, *uncapped* --
   the 300s cap only applied when that header was absent. If OpenFEC
   returns e.g. Retry-After: 3600 on an hourly-quota-exceeded response
   (plausible, and this is a sustained backfill that hits that quota by
   design), the code was correctly but silently sleeping for up to an hour
   in one blocking call -- indistinguishable from a hang from the outside
   (0% CPU, alive, zero progress), and explains why zero "SKIPPING" lines
   ever appeared: it never reached a failure, just a very patient wait.
   Capped Retry-After the same way as the backoff-only case.

If a fourth distinct cause turns up, the print statements below (flushed
immediately) exist so the log shows exactly which branch is being hit and
for how long, instead of guessing from external process state again.
"""

import concurrent.futures
import time

import requests

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="http-get")
MAX_WAIT_SECONDS = 300


def _get_once(url: str, params: dict, read_timeout: int):
    with requests.Session() as session:
        return session.get(url, params=params, timeout=read_timeout)


def get_with_retry(url: str, params: dict,
                    max_attempts: int = 8, read_timeout: int = 30, hard_timeout: int = 45) -> dict:
    resp = None
    for attempt in range(max_attempts):
        t0 = time.monotonic()
        try:
            future = _EXECUTOR.submit(_get_once, url, params, read_timeout)
            resp = future.result(timeout=hard_timeout)
        except (requests.exceptions.RequestException, concurrent.futures.TimeoutError) as e:
            print(f"[http_utils] attempt {attempt}: {type(e).__name__} after {time.monotonic() - t0:.1f}s: {e}",
                  flush=True)
            if attempt == max_attempts - 1:
                raise
            backoff = min(5 * 2 ** attempt, MAX_WAIT_SECONDS)
            print(f"[http_utils] attempt {attempt}: backing off {backoff}s", flush=True)
            time.sleep(backoff)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            # Cap Retry-After too -- an uncapped value from the server (e.g.
            # 3600s on an hourly-quota 429) would otherwise sleep for the
            # server's full requested duration in one blocking call.
            server_wait = resp.headers.get("Retry-After")
            try:
                wait = min(int(server_wait), MAX_WAIT_SECONDS) if server_wait else min(5 * 2 ** attempt, MAX_WAIT_SECONDS)
            except ValueError:
                # Retry-After can legally be an HTTP-date instead of seconds
                # (RFC 9110); don't let an unparsed date crash the run.
                wait = min(5 * 2 ** attempt, MAX_WAIT_SECONDS)
            print(f"[http_utils] attempt {attempt}: HTTP {resp.status_code} after {time.monotonic() - t0:.1f}s, "
                  f"server Retry-After={server_wait!r}, sleeping {wait}s", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}
