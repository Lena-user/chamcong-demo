"""Generic, app-independent part of the load test: configuration and load shapes.

Everything is read from environment variables (run.sh loads config.env and
passes them into the container). Defaults here match config.env, so Locust also
works without run.sh. Keep this file unchanged when reusing the kit for another app.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from locust import LoadTestShape

SCENARIOS = ("steady", "burst", "ramp", "login_storm", "custom")

# Per-scenario (users, spawn rate per second, duration in seconds) defaults.
_FIXED_DEFAULTS = {
    "STEADY": (100, 20, 180),
    "BURST": (100, 100, 120),
    "LOGIN_STORM": (100, 100, 60),
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


def make_shape():
    """Return the LoadTestShape subclass for the chosen SCENARIO (only one is defined)."""
    name = scenario()
    if name == "ramp":
        steps = [int(s) for s in env_str("RAMP_STEPS", "50,100,150,200,300").split(",") if s.strip()]
        step_len = env_float("RAMP_STEP_SECONDS", 60)
        rate = env_float("RAMP_SPAWN_RATE", 20)

        class RampShape(LoadTestShape):
            def tick(self):
                idx = int(self.get_run_time() // step_len)
                if idx >= len(steps):
                    return None
                return steps[idx], rate

        return RampShape

    users, rate, duration = _fixed_params(name)

    class FixedShape(LoadTestShape):
        def tick(self):
            if self.get_run_time() >= duration:
                return None
            return users, rate

    return FixedShape
