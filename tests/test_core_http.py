"""Tests for scraper.core.http — PerDomainLimiter and fetch_url."""

import threading
import time

import pytest
import requests
import responses

from scraper.core.http import (
    MAX_RETRIES,
    USER_AGENT,
    WIDE_BLOCK_THRESHOLD,
    PerDomainLimiter,
    WideBlockError,
    fetch_url,
)


# --- PerDomainLimiter (real time, small intervals) --------------------------

def test_limiter_no_wait_on_first_call_for_new_host():
    lim = PerDomainLimiter(interval_range=(0.05, 0.05))
    t0 = time.monotonic()
    lim.wait("https://example.com/a")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.02, f"first call should be ~immediate, took {elapsed}"


def test_limiter_delays_second_call_to_same_host():
    lim = PerDomainLimiter(interval_range=(0.05, 0.05))
    lim.wait("https://example.com/a")
    t0 = time.monotonic()
    lim.wait("https://example.com/b")
    elapsed = time.monotonic() - t0
    assert 0.04 <= elapsed <= 0.12, f"second call should wait ~50ms, took {elapsed}"


def test_limiter_independent_across_hosts():
    lim = PerDomainLimiter(interval_range=(0.05, 0.05))
    lim.wait("https://example.com/")
    t0 = time.monotonic()
    lim.wait("https://other.com/")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.02, f"different host should be immediate, took {elapsed}"


def test_limiter_thread_safe_under_concurrent_calls():
    lim = PerDomainLimiter(interval_range=(0.02, 0.02))
    n = 5
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        lim.wait("https://example.com/")

    threads = [threading.Thread(target=worker) for _ in range(n)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0
    # (n-1) * interval = 4 * 0.02 = 0.08s. Generous tolerance for CI flake.
    assert 0.06 <= elapsed <= 0.25, f"5 concurrent calls should serialize to ~80ms, took {elapsed}"


# --- fetch_url (mocked HTTP + mocked sleep) ---------------------------------

@pytest.fixture
def no_sleep(monkeypatch):
    """Make time.sleep a no-op so retry backoff doesn't actually wait."""
    monkeypatch.setattr(time, "sleep", lambda s: None)


@pytest.fixture
def session():
    return requests.Session()


@pytest.fixture
def limiter():
    return PerDomainLimiter(interval_range=(0.0, 0.0))


@responses.activate
def test_fetch_url_returns_text_on_200(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", body="hello world", status=200)
    assert fetch_url("https://example.com/", session, limiter) == "hello world"


@responses.activate
def test_fetch_url_emits_success_event(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", body="hi", status=200)
    events = []
    fetch_url("https://example.com/", session, limiter,
              on_event=lambda n, d: events.append((n, d)))
    assert [e[0] for e in events] == ["success"]
    assert events[0][1]["status"] == 200
    assert events[0][1]["bytes"] == 2


@responses.activate
def test_fetch_url_retries_on_500_then_succeeds(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", status=500)
    responses.add(responses.GET, "https://example.com/", status=500)
    responses.add(responses.GET, "https://example.com/", body="ok", status=200)
    events = []
    result = fetch_url("https://example.com/", session, limiter,
                       on_event=lambda n, d: events.append((n, d)))
    assert result == "ok"
    assert [e[0] for e in events] == ["retry", "retry", "success"]


@responses.activate
def test_fetch_url_retries_on_429(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", status=429)
    responses.add(responses.GET, "https://example.com/", body="ok", status=200)
    assert fetch_url("https://example.com/", session, limiter) == "ok"


@responses.activate
def test_fetch_url_retries_on_connection_error(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/",
                  body=requests.ConnectionError("boom"))
    responses.add(responses.GET, "https://example.com/", body="ok", status=200)
    assert fetch_url("https://example.com/", session, limiter) == "ok"


@responses.activate
def test_fetch_url_gives_up_after_max_retries(no_sleep, session, limiter):
    for _ in range(MAX_RETRIES):
        responses.add(responses.GET, "https://example.com/", status=500)
    events = []
    result = fetch_url("https://example.com/", session, limiter,
                       on_event=lambda n, d: events.append((n, d)))
    assert result is None
    names = [e[0] for e in events]
    assert names.count("retry") == MAX_RETRIES
    assert names[-1] == "fail"


@responses.activate
def test_fetch_url_does_not_retry_on_404(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", status=404)
    events = []
    result = fetch_url("https://example.com/", session, limiter,
                       on_event=lambda n, d: events.append((n, d)))
    assert result is None
    assert [e[0] for e in events] == ["fail"]


@responses.activate
def test_fetch_url_sends_custom_user_agent(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", body="hi", status=200)
    fetch_url("https://example.com/", session, limiter)
    assert len(responses.calls) == 1
    assert responses.calls[0].request.headers["User-Agent"] == USER_AGENT


# --- WideBlockError 403 guard ----------------------------------------------

@responses.activate
def test_fetch_url_increments_403_counter_on_403(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", status=403)
    fetch_url("https://example.com/", session, limiter)
    assert limiter._consecutive_403s["example.com"] == 1


@responses.activate
def test_fetch_url_does_not_increment_403_counter_on_404(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", status=404)
    fetch_url("https://example.com/", session, limiter)
    # 404 is "not found", not "blocked" — counter must not tick.
    assert limiter._consecutive_403s.get("example.com", 0) == 0


@responses.activate
def test_fetch_url_resets_403_counter_on_200(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", status=403)
    responses.add(responses.GET, "https://example.com/", body="ok", status=200)
    fetch_url("https://example.com/", session, limiter)  # counter -> 1
    fetch_url("https://example.com/", session, limiter)  # 200 resets to 0
    assert limiter._consecutive_403s["example.com"] == 0


@responses.activate
def test_fetch_url_resets_403_counter_on_404(no_sleep, session, limiter):
    responses.add(responses.GET, "https://example.com/", status=403)
    responses.add(responses.GET, "https://example.com/", status=404)
    fetch_url("https://example.com/", session, limiter)  # counter -> 1
    fetch_url("https://example.com/", session, limiter)  # 404 resets to 0
    assert limiter._consecutive_403s["example.com"] == 0


@responses.activate
def test_fetch_url_raises_wideblockerror_at_threshold(no_sleep, session, limiter):
    for _ in range(WIDE_BLOCK_THRESHOLD):
        responses.add(responses.GET, "https://example.com/", status=403)
    # First (THRESHOLD - 1) fetches return None and increment the counter.
    for _ in range(WIDE_BLOCK_THRESHOLD - 1):
        assert fetch_url("https://example.com/", session, limiter) is None
    # The fetch that pushes the counter to THRESHOLD raises.
    with pytest.raises(WideBlockError) as exc_info:
        fetch_url("https://example.com/", session, limiter)
    assert exc_info.value.host == "example.com"
    assert exc_info.value.count == WIDE_BLOCK_THRESHOLD


@responses.activate
def test_wideblockerror_is_per_host(no_sleep, session, limiter):
    # Trip the wide-block on host A.
    for _ in range(WIDE_BLOCK_THRESHOLD):
        responses.add(responses.GET, "https://blocked.com/", status=403)
    for _ in range(WIDE_BLOCK_THRESHOLD - 1):
        fetch_url("https://blocked.com/", session, limiter)
    with pytest.raises(WideBlockError):
        fetch_url("https://blocked.com/", session, limiter)
    # Host B is unaffected — its counter never incremented, requests still succeed.
    responses.add(responses.GET, "https://ok.com/", body="hi", status=200)
    assert fetch_url("https://ok.com/", session, limiter) == "hi"
    assert limiter._consecutive_403s.get("ok.com", 0) == 0