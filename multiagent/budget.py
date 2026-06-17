import time
import threading
from collections import deque


class Budget:
    """Tracks token spending and request rate.

    Three behaviors:
      1. Live cost: snapshot() returns current totals.
      2. Soft warning: fires ONCE when usage crosses warn_threshold (e.g. 80%
         of max_tokens or max_requests_per_minute). Re-arms if the user
         raises the cap.
      3. Hard cap: check_and_record() returns (False, reason) when usage
         would exceed the cap. The provider raises a RuntimeError on that,
         which main.py catches gracefully.

    Both caps are tunable in real time via set_max_tokens / set_rate_limit.
    Thread-safe via an internal lock.
    """

    # Cross this fraction of either cap and a one-time warning prints.
    DEFAULT_WARN_THRESHOLD = 0.8

    def __init__(self, max_tokens, max_requests_per_minute,
                 warn_threshold=DEFAULT_WARN_THRESHOLD):
        self.max_tokens = max_tokens
        self.used_tokens = 0
        self.max_requests_per_minute = max_requests_per_minute
        self._request_times = deque()  # timestamps of recent requests
        self._posting_enabled = True
        self.warn_threshold = warn_threshold
        # Each warning fires once until the cap is raised.
        self._token_warned = False
        self._rate_warned = False
        self._lock = threading.Lock()

    def _print_warning(self, kind, used, cap, pct):
        """Banner-style warning so it stands out in scrolling output."""
        border = "!" * 70
        print()
        print(border)
        print(f" BUDGET WARNING — {kind}")
        print(f" usage: {used} / {cap}  ({pct:.0%} of cap)")
        print(f" the hard cap will stop the run at 100%. "
              f"raise the cap with 'tokens N' or 'rate N' if needed.")
        print(border)

    def _maybe_warn_locked(self):
        """Called with _lock already held. Fires one-time warnings when
        either usage metric crosses warn_threshold."""
        # Token threshold
        if not self._token_warned and self.max_tokens > 0:
            pct = self.used_tokens / self.max_tokens
            if pct >= self.warn_threshold:
                self._token_warned = True
                self._print_warning("tokens", self.used_tokens,
                                    self.max_tokens, pct)

        # Rate threshold
        if not self._rate_warned and self.max_requests_per_minute > 0:
            now_count = len(self._request_times)
            pct = now_count / self.max_requests_per_minute
            if pct >= self.warn_threshold:
                self._rate_warned = True
                self._print_warning("requests per minute", now_count,
                                    self.max_requests_per_minute, pct)

    def check_and_record(self, estimated_tokens=0):
        """Check whether a new request is allowed under both caps.
        Returns (allowed: bool, reason: str).
        If allowed, records the request time. Token usage is added later via add_usage()."""
        with self._lock:
            # Token cap check
            if self.used_tokens + estimated_tokens > self.max_tokens:
                return False, (
                    f"token cap reached "
                    f"({self.used_tokens}/{self.max_tokens})"
                )

            # Rate limit check: count requests in the last 60 seconds
            now = time.time()
            while self._request_times and now - self._request_times[0] > 60:
                self._request_times.popleft()
            if len(self._request_times) >= self.max_requests_per_minute:
                return False, (
                    f"rate limit reached "
                    f"({len(self._request_times)}/{self.max_requests_per_minute} per minute)"
                )

            # Allowed; record this request's time
            self._request_times.append(now)

            # Rate may have just crossed the warn threshold.
            self._maybe_warn_locked()
            return True, ""

    def add_usage(self, tokens):
        """Record actual tokens consumed by a completed call."""
        with self._lock:
            self.used_tokens += tokens
            # Token usage may have just crossed the warn threshold.
            self._maybe_warn_locked()

    def set_max_tokens(self, new_max):
        """Raise (or lower) the token cap. If raised, re-arm the warning so
        the user gets notified again when they approach the new threshold."""
        with self._lock:
            old = self.max_tokens
            self.max_tokens = new_max
            if new_max > old:
                self._token_warned = False
                print(f"[budget] token cap raised {old} -> {new_max}; warning re-armed")
            else:
                # Lowered — check immediately in case we're already over warn.
                self._maybe_warn_locked()

    def set_rate_limit(self, new_rate):
        """Raise or lower the per-minute request rate. Re-arms warning on raise."""
        with self._lock:
            old = self.max_requests_per_minute
            self.max_requests_per_minute = new_rate
            if new_rate > old:
                self._rate_warned = False
                print(f"[budget] rate cap raised {old} -> {new_rate}; warning re-armed")
            else:
                self._maybe_warn_locked()

    def snapshot(self):
        """Return a dict of current state, for printing."""
        with self._lock:
            token_pct = (self.used_tokens / self.max_tokens) if self.max_tokens else 0
            rate_pct = (len(self._request_times) / self.max_requests_per_minute
                        if self.max_requests_per_minute else 0)
            return {
                "used_tokens": self.used_tokens,
                "max_tokens": self.max_tokens,
                "tokens_pct": f"{token_pct:.0%}",
                "requests_last_minute": len(self._request_times),
                "max_requests_per_minute": self.max_requests_per_minute,
                "requests_pct": f"{rate_pct:.0%}",
                "posting_enabled": self._posting_enabled,
            }

    def disable_posting(self, reason):
        with self._lock:
            if self._posting_enabled:
                self._posting_enabled = False
                print(f"[budget] posting disabled: {reason}")

    def is_posting_enabled(self):
        with self._lock:
            return self._posting_enabled