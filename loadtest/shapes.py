"""Generic, app-independent part of the load test: configuration and load shapes.

Everything is read from environment variables (run.sh loads config.env and
passes them into the container). Defaults here match config.env, so Locust also
works without run.sh. Keep this file unchanged when reusing the kit for another app.
"""
import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from locust import LoadTestShape

SCENARIOS = ("steady", "burst", "ramp", "login_storm", "custom", "write_storm", "write_ramp")
WRITE_SCENARIOS = ("write_storm", "write_ramp")

# Per-scenario (users, spawn rate per second, duration in seconds) defaults.
_FIXED_DEFAULTS = {
    "STEADY": (100, 20, 180),
    "BURST": (100, 100, 120),
    "LOGIN_STORM": (100, 100, 60),
    "WRITE_STORM": (100, 100, 120),
}


def env_str(name, default):
    return os.environ.get(name) or default


def env_int(name, default):
    return int(os.environ.get(name) or default)


def env_float(name, default):
    return float(os.environ.get(name) or default)


def scenario():
    name = env_str("SCENARIO", "steady")
    if name not in SCENARIOS:
        raise SystemExit(f"Unknown SCENARIO '{name}', expected one of {SCENARIOS}")
    return name


def tz():
    return ZoneInfo(env_str("LOADTEST_TZ", "Asia/Ho_Chi_Minh"))


def now():
    return datetime.now(tz())


def wait_range():
    return env_float("WAIT_MIN", 1), env_float("WAIT_MAX", 3)


def code_range(var, default):
    """Parse 'A-B' (inclusive) from env into a range of ints."""
    lo, hi = env_str(var, default).split("-")
    return range(int(lo), int(hi) + 1)


def user_codes(range_var, default):
    """Login codes built from USER_CODE_FORMAT and a numeric range, e.g. E0014..E0300."""
    fmt = env_str("USER_CODE_FORMAT", "E{:04d}")
    return [fmt.format(n) for n in code_range(range_var, default)]


def password():
    return os.environ.get("LOADTEST_PASSWORD", "")


def _fixed_params(name):
    """(users, spawn_rate, duration) for steady / burst / login_storm / custom."""
    if name == "custom":
        return env_int("USERS", 100), env_float("SPAWN_RATE", 20), env_float("DURATION", 60)
    users, rate, duration = _FIXED_DEFAULTS[name.upper()]
    prefix = name.upper()
    return (env_int(f"{prefix}_USERS", users), env_float(f"{prefix}_SPAWN_RATE", rate),
            env_float(f"{prefix}_DURATION", duration))


class BreakGuard:
    """Decides at the end of a ramp step whether the system has broken.

    Broken = p95 of the recent window above TARGET_P95_MS * BREAK_FACTOR, or the
    step's error rate above BREAK_ERR_PCT. The decision is written to
    $RESULT_DIR/break.json (read by report.py) and to the log.
    """

    def __init__(self):
        self.enabled = env_str("STOP_ON_BREAK", "1") not in ("0", "false", "no")
        self.p95_limit = env_float("TARGET_P95_MS", 500) * env_float("BREAK_FACTOR", 2)
        self.err_limit = env_float("BREAK_ERR_PCT", 5)
        self.hold = env_float("BREAK_HOLD_SECONDS", 30)
        self._req0 = self._fail0 = 0

    def begin_step(self, total):
        self._req0 = total.num_requests if total else 0
        self._fail0 = total.num_failures if total else 0

    def evaluate(self, total, step, users, run_time):
        """Return the break record for the step that just ended, or None if it held."""
        if total is None:
            return None
        reqs = total.num_requests - self._req0
        err = 100.0 * (total.num_failures - self._fail0) / reqs if reqs else 0.0
        p95 = total.get_current_response_time_percentile(0.95) or 0
        reasons = []
        if p95 > self.p95_limit:
            reasons.append(f"p95 {p95:.0f} ms > {self.p95_limit:.0f} ms")
        if err > self.err_limit:
            reasons.append(f"error rate {err:.1f}% > {self.err_limit:g}%")
        if not reasons:
            return None
        info = {"step_index": step, "users": users, "p95_ms": p95, "err_pct": round(err, 2),
                "reason": "; ".join(reasons), "hold_seconds": self.hold, "at_seconds": round(run_time, 1)}
        logging.warning("BREAK at step %d (%d users): %s; holding %gs then stopping",
                        step, users, info["reason"], self.hold)
        out = os.environ.get("RESULT_DIR")
        if out:
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "break.json"), "w", encoding="utf-8") as f:
                json.dump(info, f)
        return info


def step_params(name):
    """(steps, step seconds, spawn rate) for the ramp-type scenarios."""
    prefix = "WRITE_RAMP" if name == "write_ramp" else "RAMP"
    default_steps = "50,100,200,300,400,600,800" if name == "write_ramp" else "50,100,150,200,300"
    steps = [int(s) for s in env_str(f"{prefix}_STEPS", default_steps).split(",") if s.strip()]
    return (steps, env_float(f"{prefix}_STEP_SECONDS", 60),
            env_float(f"{prefix}_SPAWN_RATE", 50 if name == "write_ramp" else 20))


def make_step_shape(steps, step_len, rate):
    """Step through user counts; stop early when BreakGuard says the system broke."""
    guard = BreakGuard()

    class StepShape(LoadTestShape):
        cur = -1  # index of the step whose window is open
        stop_at = None  # run time at which to stop after a break
        hold_users = 0

        def _total(self):
            return self.runner.stats.total if self.runner else None

        def tick(self):
            t = self.get_run_time()
            if self.stop_at is not None:
                return None if t >= self.stop_at else (self.hold_users, rate)
            idx = int(t // step_len)
            if idx != self.cur:
                if 0 <= self.cur < len(steps) and guard.enabled:
                    if guard.evaluate(self._total(), self.cur, steps[self.cur], t):
                        self.stop_at = t + guard.hold
                        self.hold_users = steps[self.cur]
                        return self.hold_users, rate
                self.cur = idx
                guard.begin_step(self._total())
            if idx >= len(steps):
                return None
            return steps[idx], rate

    return StepShape


def make_shape():
    """Return the LoadTestShape subclass for the chosen SCENARIO (only one is defined)."""
    name = scenario()
    if name in ("ramp", "write_ramp"):
        return make_step_shape(*step_params(name))

    users, rate, duration = _fixed_params(name)

    class FixedShape(LoadTestShape):
        def tick(self):
            if self.get_run_time() >= duration:
                return None
            return users, rate

    return FixedShape
