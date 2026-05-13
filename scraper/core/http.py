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

class PerDomainLimiter:
    """Thread-safe rate limiter that enforces a delay between requests to the same domain.
    
    Each host is tracked independently, fetching one agency's site while waiting on the rate-limit
    cooldown for the directory host is allowed."""

    def __init__(self, interval_range: tuple[float, float] = DEFAULT_RATE_LIMIT):
        self._range = interval_range
        self._next_allowed: dict[str, float] = {} #host -> monotonic timestamp of next allowed fetch
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
  - Emits ('retry', {...}), ('fail', {...}), ('success', {...}) via on_event
    if provided. Callers handle their own logging — no print() in core.
  """
  headers = {"User-Agent": USER_AGENT}
  backoff = RETRY_BACKOFF_BASE

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
      _emit(on_event, 'success', url=url, status=200, bytes=len(resp.text))
      return resp.text
    
    if resp.status_code in (429, 408) or resp.status_code >= 500:
      _emit(on_event, 'retry', url=url, attempt=attempt, reason=f"HTTP {resp.status_code}")
      time.sleep(backoff)
      backoff *= 2
      continue

    _emit(on_event, 'fail', url=url, attempt=attempt, reason=f"HTTP {resp.status_code}")
    return None
  
  _emit(on_event, 'fail', url=url, reason=f"Gave up after {MAX_RETRIES} retries")
  return None

def _emit(on_event: Optional[Callable[[str, str], None]], 
          name: str, 
          **data,
          ) -> None:
  if on_event is not None:
    on_event(name, data)

       

  
    

