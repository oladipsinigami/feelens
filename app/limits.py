"""Who is allowed to spend the model budget.

The fee engine has no budget: pricing a quote is arithmetic on a local file, and
it can run a million times for nothing. The model is the only metered thing in
this app, and the free tier is **50 upstream requests per day, shared by every
visitor**. One explanation costs two or three of them.

That makes the public link a shared, finite resource with no owner. Without a
limit, the first visitor to click Explain ten times spends a fifth of the day's
budget, and every judge after them gets the template on the one feature the
submission is about. So there are two limits, and they protect different things:

- `VisitorLimiter` protects the budget from *one* visitor, by counting
  explanations per address in a sliding window.
- `ModelBudget` protects the account from *everyone*, by counting the upstream
  requests actually sent and refusing to start new work near the daily ceiling.

Neither limit touches a number. A refused request still returns the full quote
and a guardrail-clean template explanation; it only loses the prose.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Decision:
    """Whether the model may be consulted, and what to tell the visitor if not."""

    allowed: bool
    note: str = ""
    retry_after: int = 0


ALLOWED = Decision(True)


class VisitorLimiter:
    """A sliding window of explanations per client address, and an optional day.

    Sliding, not fixed, because a fixed window lets a visitor spend the whole
    allowance at 09:59 and the whole next one at 10:00. The window is short
    enough that this is a throttle rather than a punishment: a visitor who hits
    it waits a few minutes, and every quote stays available through `/api/quote`
    and through the cached explanations the app already holds.

    The window alone is not enough on a public link. Six explanations every ten
    minutes is unlimited over a day, and the day's whole model budget is about
    seventeen explanations — so one curious visitor clicking Explain for twenty
    minutes can leave every judge after them on the template. `max_per_day`
    bounds that: it is not a limit on curiosity, it is a guarantee that the
    budget is shared rather than first-come.
    """

    def __init__(
        self,
        max_explanations: int = 6,
        window_seconds: int = 600,
        max_per_day: int | None = None,
        max_visitors: int = 2048,
        clock=time.monotonic,
        day_clock=None,
    ):
        self._max = max(1, max_explanations)
        self._window = max(1, window_seconds)
        self._daily_max = max(1, max_per_day) if max_per_day is not None else None
        self._max_visitors = max(1, max_visitors)
        self._clock = clock
        self._day_clock = day_clock or _utc_today
        self._day = self._day_clock()
        self._seen: dict = {}
        self._per_day: dict = {}

    @property
    def window_minutes(self) -> int:
        return max(1, round(self._window / 60))

    @property
    def max_per_window(self) -> int:
        """The allowance itself, for reporting. Not used to enforce anything."""
        return self._max

    @property
    def max_per_day(self) -> int | None:
        return self._daily_max

    def _roll_day(self) -> None:
        today = self._day_clock()
        if today != self._day:
            self._day = today
            self._per_day.clear()

    def take(self, address: str) -> Decision:
        """Record one explanation attempt, or refuse it and say for how long."""
        now = self._clock()
        self._roll_day()
        address = address or "unknown"

        # The day is checked before the window, and before anything is counted.
        # A visitor refused by the window has not used a day's allowance, and
        # charging them for a refusal would make the day end early for no
        # reason.
        if self._daily_max is not None:
            used = self._per_day.get(address, 0)
            if used >= self._daily_max:
                return Decision(
                    allowed=False,
                    note=(
                        f"{used} of {self._daily_max} model explanations per "
                        "visitor per day, so the day's shared model budget lasts "
                        "for everyone. It resets at 00:00 UTC; the figures below "
                        "are unaffected."
                    ),
                )

        stamps = self._seen.setdefault(address, [])
        cutoff = now - self._window
        while stamps and stamps[0] <= cutoff:
            stamps.pop(0)

        if len(stamps) >= self._max:
            wait = int(stamps[0] + self._window - now) + 1
            minutes = max(1, round(wait / 60))
            unit = "minute" if minutes == 1 else "minutes"
            return Decision(
                allowed=False,
                note=(
                    f"{self._max} explanation{'s' if self._max != 1 else ''} per "
                    f"{self.window_minutes} "
                    f"minute{'s' if self.window_minutes != 1 else ''} per visitor; "
                    f"try again in about {minutes} {unit}. The figures below are "
                    "unaffected."
                ),
                retry_after=wait,
            )

        stamps.append(now)
        if self._daily_max is not None:
            self._per_day[address] = self._per_day.get(address, 0) + 1
        self._forget_stale(cutoff)
        return ALLOWED

    def _forget_stale(self, cutoff: float) -> None:
        """Keep the table from growing with every address that ever visited.

        A public link attracts scanners. Their addresses are worth forgetting as
        soon as their window is empty, and the cap is the backstop if they are
        not.

        A forgotten address loses its day count too. That is the price of not
        growing without bound, and it is only paid past `max_visitors` distinct
        addresses — a load at which the account's own request ceiling, not this
        table, is what actually protects the budget.
        """
        if len(self._seen) <= self._max_visitors:
            return
        for address in [a for a, s in self._seen.items() if not s or s[-1] <= cutoff]:
            self._seen.pop(address, None)
            self._per_day.pop(address, None)
        while len(self._seen) > self._max_visitors:
            stale = next(iter(self._seen))
            self._seen.pop(stale, None)
            self._per_day.pop(stale, None)

    def snapshot(self) -> dict:
        return {
            "explanations_per_visitor": self._max,
            "window_minutes": self.window_minutes,
            "per_visitor_per_day": self._daily_max,
        }


class ModelBudget:
    """The account's daily ceiling on upstream model requests.

    Counted where the request is actually sent — inside the client — rather than
    where it is requested, because one explanation is two or three requests and
    a repair or a fallback can add more. Counting explanations would be a guess
    about cost; counting requests is the cost.

    The limit defaults below the provider's 50 so the last few are never spent by
    a stranger, and the day rolls over at 00:00 UTC, which is when OpenRouter
    resets its own counter.
    """

    def __init__(self, limit: int = 44, clock=None):
        self._limit = max(0, limit)
        self._spent = 0
        self._clock = clock or _utc_today
        self._today = self._clock()

    def _roll(self) -> None:
        today = self._clock()
        if today != self._today:
            self._today = today
            self._spent = 0

    def record(self, count: int = 1) -> None:
        self._roll()
        self._spent += max(0, count)

    @property
    def spent(self) -> int:
        self._roll()
        return self._spent

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def remaining(self) -> int:
        return max(0, self._limit - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def decision(self) -> Decision:
        if self._limit == 0:
            return Decision(
                allowed=False,
                note=(
                    "the model is switched off for this deployment "
                    "(MODEL_DAILY_REQUEST_CAP=0). The figures below are unaffected."
                ),
            )
        if not self.exhausted:
            return ALLOWED
        return Decision(
            allowed=False,
            note=(
                f"this deployment has sent {self.spent} of its {self._limit} model "
                "requests since it started, and the daily allowance is spent. It "
                "resets at 00:00 UTC. The figures below are unaffected."
            ),
        )

    def snapshot(self) -> dict:
        """What was spent, and what actually enforces the ceiling.

        The count is per process: a restart starts from zero, because there is no
        shared store and inventing one for a hackathon demo would be worse than
        saying so. The account's real ceiling is OpenRouter's 50/day, which no
        restart can reset — this allowance exists to stay below it, not to replace
        it.
        """
        return {
            "requests_per_day": self._limit,
            "sent_since_start": self.spent,
            "remaining_allowance": self.remaining,
            "resets": "00:00 UTC",
            "enforced_by": (
                "this process; the account's hard ceiling is OpenRouter's 50/day"
            ),
        }


def _utc_today():
    return datetime.now(timezone.utc).date()
