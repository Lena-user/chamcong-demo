"""Locust load test for the attendance API (contract: PROJECT.md section 6).

Scenario is chosen with env SCENARIO: steady | burst | ramp | login_storm.
Only the user classes and the LoadTestShape of that scenario are defined.
"""
import itertools
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

from locust import FastHttpUser, LoadTestShape, between, constant, task
from locust.exception import StopUser

SCENARIO = os.environ.get("SCENARIO", "steady")
PASSWORD = os.environ.get("LOADTEST_PASSWORD", "")
SCENARIOS = ("steady", "burst", "ramp", "login_storm")
if SCENARIO not in SCENARIOS:
    raise SystemExit(f"Unknown SCENARIO '{SCENARIO}', expected one of {SCENARIOS}")

VN_TZ = timezone(timedelta(hours=7))
EMPLOYEE_CODES = [f"E{n:04d}" for n in range(14, 301)]  # E0014..E0300
MANAGER_CODES = [f"E{n:04d}" for n in range(4, 14)]  # E0004..E0013
_employee_seq = itertools.count()
_manager_seq = itertools.count()


def _count_event(environment, name):
    """Count a notable-but-valid outcome (409) as its own row.

    Logged on the entry only, not through the request event, so it stays out of
    the Aggregated totals (no fake 0 ms samples in the percentiles).
    """
    environment.stats.get(name, "EVENT").log(0, 0)


class BaseUser(FastHttpUser):
    abstract = True
    codes: list = []
    seq = None

    def login(self):
        code = self.codes[next(self.seq) % len(self.codes)]
        with self.client.post(
            "/api/auth/login", json={"code": code, "password": PASSWORD},
            name="/api/auth/login", catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"login {resp.status_code}")
                return False
            try:
                token = resp.json()["access_token"]
            except Exception:
                resp.failure("login: no access_token")
                return False
        self.client.headers["Authorization"] = f"Bearer {token}"
        return True

    def on_start(self):
        if not self.login():
            raise StopUser()


class EmployeeUser(BaseUser):
    weight = 95
    wait_time = between(1, 3)
    codes = EMPLOYEE_CODES
    seq = _employee_seq
    last_key = None

    @task(3)
    def check(self):
        key = str(uuid.uuid4())
        with self.client.post(
            "/api/attendance/check", json={"idempotency_key": key, "source": "web"},
            name="/api/attendance/check", catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                self.last_key = key
                resp.success()
            else:
                resp.failure(f"check {resp.status_code}")

    @task(1)
    def check_duplicate(self):
        if not self.last_key:
            return
        with self.client.post(
            "/api/attendance/check", json={"idempotency_key": self.last_key, "source": "web"},
            name="/api/attendance/check (duplicate)", catch_response=True,
        ) as resp:
            if resp.status_code == 200 and resp.json().get("duplicate") is True:
                resp.success()
            else:
                resp.failure(f"duplicate expected 200/duplicate=true, got {resp.status_code}")

    @task(5)
    def attendance_me(self):
        self.client.get("/api/attendance/me?size=20", name="/api/attendance/me")

    @task(2)
    def me(self):
        self.client.get("/api/me", name="/api/me")

    @task(2)
    def requests_me(self):
        self.client.get("/api/requests/me", name="/api/requests/me")

    @task(1)
    def create_ot(self):
        today = datetime.now(VN_TZ).date()
        body = {
            "type": "ot",
            "work_date": today.isoformat(),
            "start_at": datetime(today.year, today.month, today.day, 18, 0, tzinfo=VN_TZ).isoformat(),
            "end_at": datetime(today.year, today.month, today.day, 20, 0, tzinfo=VN_TZ).isoformat(),
            "reason": "load test overtime",
        }
        with self.client.post(
            "/api/requests", json=body, name="/api/requests (create)", catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                resp.success()
            else:
                resp.failure(f"create request {resp.status_code}")


class ManagerUser(BaseUser):
    weight = 5
    wait_time = between(1, 3)
    codes = MANAGER_CODES
    seq = _manager_seq

    @task
    def review(self):
        with self.client.get(
            "/api/requests?status=pending&size=20", name="/api/requests?status=pending",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"list pending {resp.status_code}")
                return
            items = resp.json().get("items", [])
        if not items:
            return
        req_id = random.choice(items)["id"]
        with self.client.post(
            f"/api/requests/{req_id}/approve", name="/api/requests/[id]/approve",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 409:
                resp.success()  # contention is a valid outcome
                _count_event(self.environment, "approve 409 (contention)")
            else:
                resp.failure(f"approve {resp.status_code}")


class LoginUser(BaseUser):
    """login_storm: nothing but login, as fast as possible."""
    wait_time = constant(0)
    codes = EMPLOYEE_CODES
    seq = _employee_seq

    def on_start(self):
        pass

    @task
    def storm(self):
        self.login()


if SCENARIO == "burst":
    EmployeeUser.wait_time = constant(0)
    ManagerUser.wait_time = constant(0)

# Locust spawns every non-abstract User class in the module, so disable the unused ones.
if SCENARIO == "login_storm":
    EmployeeUser.abstract = ManagerUser.abstract = True
else:
    LoginUser.abstract = True


def _make_shape():
    """Return a LoadTestShape subclass for the scenario (only one is ever defined)."""
    if SCENARIO == "ramp":
        steps = [50, 100, 150, 200, 300]
        step_len = 60

        class RampShape(LoadTestShape):
            def tick(self):
                run = self.get_run_time()
                idx = int(run // step_len)
                if idx >= len(steps):
                    return None
                return steps[idx], 20

        return RampShape

    users, rate, duration = {
        "steady": (100, 20, 180),
        "burst": (100, 100, 120),
        "login_storm": (100, 100, 60),
    }[SCENARIO]

    class FixedShape(LoadTestShape):
        def tick(self):
            if self.get_run_time() >= duration:
                return None
            return users, rate

    return FixedShape


Shape = _make_shape()
