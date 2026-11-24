"""One gate for everything Ayre says on her own, shared by all her skills.

Wingman cuts off whatever is playing when a new line starts, and "no interrupt" lines wait in
line and arrive late. So her skills never call play_to_user directly; they go through here:

- SAFETY (assist aborted, can't see the screen): always spoken, may cut her off.
- CALLOUT (fifty, thirty, hold): spoken when she is quiet; while she talks only the newest
  callout is kept, and it is dropped if it is older than max_age by the time she is free.
- CHATTER (log events, online, quips): only when she has been quiet a while, at most one
  every chatter_gap seconds, never queued.

The gate lives on the wingman object, so all three skills share one. Thread safe: flight
speaks from its assist thread, ship and eyes from the event loop. Zero cost while idle.
"""
from __future__ import annotations

import asyncio
import threading
import time

SAFETY, CALLOUT, CHATTER = 3, 2, 1


class SpeechGate:
    def __init__(self, wingman, loop, clock=time.monotonic, chatter_gap=10.0, quiet_before_chatter=3.0,
                 callout_gap=1.2, max_age=1.5, retry_s=0.15):
        self.wingman, self.loop, self.clock = wingman, loop, clock
        self.chatter_gap, self.quiet_before_chatter = chatter_gap, quiet_before_chatter
        self.callout_gap, self.max_age, self.retry_s = callout_gap, max_age, retry_s
        self.lock = threading.Lock()
        self.last_any = self.last_callout = self.last_chatter = -1e9
        self.next_callout: tuple[str, float] | None = None  # newest waiting callout, (text, when)
        self.inflight_until = 0.0  # our own line was sent; treat as busy until playback reports
        self.timer: threading.Timer | None = None
        self.spoken: list[tuple[float, int, str]] = []  # for tests and the status report

    @classmethod
    def get(cls, wingman, loop=None) -> "SpeechGate":
        gate = getattr(wingman, "_ayre_gate", None)
        if gate is None:
            gate = cls(wingman, loop or asyncio.get_event_loop())
            wingman._ayre_gate = gate
        return gate

    def busy(self) -> bool:
        player = getattr(self.wingman, "audio_player", None)
        return bool(getattr(player, "is_playing", False)) or self.clock() < self.inflight_until

    def say(self, text: str, priority: int = CALLOUT) -> bool:
        """Speak now, keep for later (newest callout only) or drop. Returns True if sent now."""
        with self.lock:
            now = self.clock()
            if priority == SAFETY:
                self.next_callout = None  # whatever was waiting is out of date now
                return self._send(text, priority, interrupt=True)
            if priority == CHATTER:
                if (self.busy() or now - self.last_any < self.quiet_before_chatter
                        or now - self.last_chatter < self.chatter_gap or self.next_callout):
                    return False
                return self._send(text, priority)
            if self.busy() or now - self.last_callout < self.callout_gap:
                self.next_callout = (text, now)  # replaces any older waiting callout
                self._arm_retry()
                return False
            return self._send(text, priority)

    def _send(self, text: str, priority: int, interrupt: bool = False) -> bool:
        now = self.clock()
        self.last_any = now
        if priority == CALLOUT:
            self.last_callout = now
        if priority == CHATTER:
            self.last_chatter = now
        # a short line takes ~0.4 s per word to say; busy until playback picks it up or that passes
        self.inflight_until = now + 0.3 + 0.4 * len(text.split())
        self.spoken.append((now, priority, text))
        del self.spoken[:-50]
        coro = self.wingman.play_to_user(text, not interrupt)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        return True

    def _arm_retry(self) -> None:
        if self.timer is None:
            self.timer = threading.Timer(self.retry_s, self._retry)
            self.timer.daemon = True
            self.timer.start()

    def _retry(self) -> None:
        with self.lock:
            self.timer = None
            if not self.next_callout:
                return
            text, when = self.next_callout
            now = self.clock()
            if now - when > self.max_age:
                self.next_callout = None  # too late to be useful
                return
            if self.busy() or now - self.last_callout < self.callout_gap:
                self._arm_retry()
                return
            self.next_callout = None
            self._send(text, CALLOUT)

    def playback_finished(self) -> None:
        """Hook for the playback 'finished' event: she is free, a waiting callout may go now."""
        with self.lock:
            self.inflight_until = 0.0
