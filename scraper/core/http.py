import random
import threading
import time
from collections.abc  import Callable
from typing import Optional
from urllib.parse import urlparse

import requests

USER_AGENT = "NextCallClub-AgentScraper/1.0 (contact: victorsalazar@nextcallclub.com)"
DEFAULT_RATE_LIMIT = (1.0, 2.0) #seconds, per host
MAX_RETRIES = 3
REQUEST_TIMEOUT = 20 #seconds
RETRY_BACKOFF_BASE  = 2.0
WIDE_BLOCK_THRESHOLD = 5  # consecutive 403s per host before we hard-stop


class WideBlockError(Exception):
    """Raised when one host returns 403 for WIDE_BLOCK_THRESHOLD consecutive requests.

    Signals that a bot manager (typically Akamai or Cloudflare) has decided
    we're a bot and is wide-blocking us. Caller is expected to catch in
    main(), print a clean message, and exit non-zero — don't burn hours of
    fetches that will all fail.
    """
    def __init__(self, host: str, count: int):
        self.host = host
        self.count = count
        super().__init__(
            f"Wide-block: host '{host}' returned 403 on {count} consecutive requests."
        )


class PerDomainLimiter:
    """Thread-safe rate limiter that enforces a delay between requests to the same domain.

    Each host is tracked independently, fetching one agency's site while waiting on the rate-limit
    cooldown for the directory host is allowed.

    Also tracks per-host consecutive 403 counts so fetch_url can hard-stop
    when a host wide-blocks us."""

    def __init__(self, interval_range: tuple[float, float] = DEFAULT_RATE_LIMIT):
        self._range = interval_range
        self._next_allowed: dict[str, float] = {} #host -> monotonic timestamp of next allowed fetch
        self._consecutive_403s: dict[str, int] = {} #host -> count of recent 403s in a row
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        """Waits until it's safe to make a request to the given URL's domain."""
        host = urlparse(url).netloc
        with self._lock:
            now = time.monotonic()
            next_allowed = self._next_allowed.get(host, 0.0)
            sleep_for = max(0.0, next_allowed - now)
            interval = random.uniform(*self._range)
            # Reserve the slot before sleeping so concurrent callers don't
            # all see the same "last fetch" and pile up on the same host.
            self._next_allowed[host] = max(now, next_allowed) + interval
        if sleep_for > 0:
          time.sleep(sleep_for)

    def record_403(self, host: str) -> None:
        """Increment the host's consecutive-403 counter. Raises WideBlockError at threshold.

        Called from fetch_url after a 403 response. Strict-consecutive semantics:
        any non-403 response on the same host resets the counter via record_non_403.
        """
        with self._lock:
            new_count = self._consecutive_403s.get(host, 0) + 1
            self._consecutive_403s[host] = new_count
        if new_count >= WIDE_BLOCK_THRESHOLD:
            raise WideBlockError(host, new_count)

    def record_non_403(self, host: str) -> None:
        """Reset the host's consecutive-403 counter (got any non-403 response)."""
        with self._lock:
            self._consecutive_403s[host] = 0

def fetch_url(
        url: str,
        session: requests.Session,
        limiter: PerDomainLimiter,
        on_event: Callable[[str, str], None] | None = None,
) -> Optional[str]:
    
  """GET a URL politely. Returns response text on success, None on failure.

  - Calls limiter.wait(url) before every attempt (including retries).
  - Retries up to MAX_RETRIES on 429, 408, 5xx, or requests.RequestException
    with exponential backoff (RETRY_BACKOFF_BASE * 2**(attempt-1)).
  - Gives up immediately (returns None) on other 4xx (404, 403, etc.).
  - Tracks consecutive 403s per host via the limiter; raises WideBlockError
    after WIDE_BLOCK_THRESHOLD in a row so callers can hard-stop instead of
    burning hours of doomed retries.
  - Emits ('retry', {...}), ('fail', {...}), ('success', {...}) via on_event
    if provided. Callers handle their own logging — no print() in core.
  """
  headers = {"User-Agent": USER_AGENT}
  backoff = RETRY_BACKOFF_BASE
  host = urlparse(url).netloc

  for attempt in range(1, MAX_RETRIES + 1):
    limiter.wait(url)
    try:
      resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.SSLError as exc:
      # SSL errors aren't transient — cert is just bad. Fail-fast, no retries.
      _emit(on_event, 'fail', url=url, reason=f"SSL: {type(exc).__name__}")
      return None
    except requests.RequestException as exc:
      _emit(on_event, 'retry', url=url, attempt=attempt, reason=f"{type(exc).__name__}: {exc}")
      time.sleep(backoff)
      backoff *= 2
      continue

    if resp.status_code == 200:
      limiter.record_non_403(host)
      _emit(on_event, 'success', url=url, status=200, bytes=len(resp.text))
      return resp.text

    if resp.status_code in (429, 408) or resp.status_code >= 500:
      # Transient — leave the 403 counter alone (this isn't a block signal).
      _emit(on_event, 'retry', url=url, attempt=attempt, reason=f"HTTP {resp.status_code}")
      time.sleep(backoff)
      backoff *= 2
      continue

    # Other 4xx (403, 404, etc.) — fail, no retry. Emit before recording so
    # the user sees the failed URL even if record_403 raises WideBlockError.
    _emit(on_event, 'fail', url=url, attempt=attempt, reason=f"HTTP {resp.status_code}")
    if resp.status_code == 403:
      limiter.record_403(host)  # may raise WideBlockError
    else:
      limiter.record_non_403(host)
    return None

  _emit(on_event, 'fail', url=url, reason=f"Gave up after {MAX_RETRIES} retries")
  return None

def _emit(on_event: Optional[Callable[[str, str], None]], 
          name: str, 
          **data,
          ) -> None:
  if on_event is not None:
    on_event(name, data)

       

  
    

