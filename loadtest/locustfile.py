"""Locust load test for the attendance API (contract: PROJECT.md section 6).

To reuse this kit for another app: rewrite THIS file (user classes and tasks)
and config.env; keep shapes.py, run.sh and report.py unchanged.

Scenario is chosen with env SCENARIO:
steady | burst | ramp | login_storm | custom | write_storm | write_ramp.
Load shapes and all tunables come from shapes.py (env / config.env).
"""
import itertools
import json
import os
import random
import time
import uuid
from datetime import datetime

import gevent
import requests
from locust import FastHttpUser, between, constant, task
from locust.exception import StopUser

import shapes

SCENARIO = shapes.scenario()
PASSWORD = shapes.password()
WAIT_MIN, WAIT_MAX = shapes.wait_range()
MANAGER_WEIGHT = shapes.env_int("MANAGER_WEIGHT", 5)

RETRY_MAX = shapes.env_int("RETRY_MAX", 3)
RETRY_BACKOFF_S = shapes.env_float("RETRY_BACKOFF_MS", 200) / 1000
RETRY_AFTER_CAP_S = shapes.env_float("RETRY_AFTER_CAP_S", 10)
REQUEST_TIMEOUT_S = shapes.env_float("REQUEST_TIMEOUT_S", 30)
RESULT_DIR = os.environ.get("RESULT_DIR", "")
RETRYABLE = (0, 502, 503, 504)  # 0 = connection error or timeout

EMPLOYEE_CODES = shapes.user_codes("EMPLOYEE_RANGE", "14-300")
MANAGER_CODES = shapes.user_codes("MANAGER_RANGE", "4-13")
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
    code = ""

    def login(self):
        code = self.code = self.codes[next(self.seq) % len(self.codes)]
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
        self.client.auth_header = f"Bearer {token}"  # FastHttpSession has no .headers
        return True

    def on_start(self):
        if not self.login():
            raise StopUser()


class EmployeeUser(BaseUser):
    weight = 100 - MANAGER_WEIGHT
    wait_time = between(WAIT_MIN, WAIT_MAX)
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
        today = shapes.now().date()
        today_tz = shapes.tz()
        body = {
            "type": "ot",
            "work_date": today.isoformat(),
            "start_at": datetime(today.year, today.month, today.day, 18, 0, tzinfo=today_tz).isoformat(),
            "end_at": datetime(today.year, today.month, today.day, 20, 0, tzinfo=today_tz).isoformat(),
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
    weight = MANAGER_WEIGHT
    wait_time = between(WAIT_MIN, WAIT_MAX)
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


_writes_file = None


def _log_write(record):
    """Append one JSON line to $RESULT_DIR/writes.jsonl (one short write, no yield in between)."""
    global _writes_file
    if not RESULT_DIR:
        return
    if _writes_file is None:
        os.makedirs(RESULT_DIR, exist_ok=True)
        _writes_file = open(os.path.join(RESULT_DIR, "writes.jsonl"), "a", encoding="utf-8")
    _writes_file.write(json.dumps(record) + "\n")
    _writes_file.flush()


class WriterUser(BaseUser):
    """write_storm / write_ramp: writes only, no think time, with client-like retries.

    Every write gets a fresh UUID. On 502/503/504, connection error or timeout it is
    re-sent with the SAME key (up to RETRY_MAX times), honouring Retry-After.
    Stats: a failed first attempt keeps the normal endpoint name and counts as an
    error; re-sends use "<name> (retry)". Each write ends as one line in writes.jsonl.
    """
    wait_time = constant(0)
    codes = EMPLOYEE_CODES
    seq = _employee_seq
    connection_timeout = REQUEST_TIMEOUT_S
    network_timeout = REQUEST_TIMEOUT_S

    def login(self):
        """Log in outside the request stats: bcrypt logins at spawn time are slow and would
        otherwise dominate p95 and trigger a false "break". They are counted as EVENT rows."""
        code = self.code = self.codes[next(self.seq) % len(self.codes)]
        t0 = time.perf_counter()
        token = None
        try:
            r = requests.post(self.host + "/api/auth/login", json={"code": code, "password": PASSWORD},
                              timeout=REQUEST_TIMEOUT_S)
            if r.status_code == 200:
                token = r.json().get("access_token")
        except (requests.RequestException, ValueError):
            pass
        ms = (time.perf_counter() - t0) * 1000
        self.environment.stats.get("login (not in totals)" if token else "login FAILED (user stopped)",
                                   "EVENT").log(ms, 0)
        if not token:
            return False
        self.client.auth_header = f"Bearer {token}"
        return True

    def _write(self, kind, path, name, body, key, key_in_header, id_field):
        headers = {"Idempotency-Key": key} if key_in_header else None
        started = time.time()
        attempts = 0
        status = 0
        rid = None
        while True:
            attempts += 1
            retry_after = None
            with self.client.post(
                path, json=body, headers=headers, catch_response=True,
                name=name if attempts == 1 else f"{name} (retry)",
            ) as resp:
                status = resp.status_code or 0
                if status in (200, 201):
                    try:
                        rid = resp.json().get(id_field)
                    except Exception:
                        rid = None
                    resp.success()
                else:
                    resp.failure(f"{kind} {status or 'conn/timeout'}")
                    if status and resp.headers:
                        retry_after = resp.headers.get("Retry-After")
            if status in (200, 201) or status not in RETRYABLE or attempts > RETRY_MAX:
                break
            wait = RETRY_BACKOFF_S * 2 ** (attempts - 1) * random.uniform(0.5, 1.5)
            try:
                wait = max(wait, min(float(retry_after), RETRY_AFTER_CAP_S))
            except (TypeError, ValueError):
                pass
            gevent.sleep(wait)
        record = {"key": key, "kind": kind, "final_status": status, "attempts": attempts,
                  "id": rid, "code": self.code, "ts": started, "ts_end": time.time()}
        if status not in (200, 201):  # enough to re-send it later (reconcile.py --retry)
            record.update(path=path, body=body, key_in_header=key_in_header)
        _log_write(record)

    @task(3)
    def check(self):
        key = str(uuid.uuid4())
        self._write("attendance", "/api/attendance/check", "/api/attendance/check",
                    {"idempotency_key": key, "source": "web"}, key, False, "event_id")

    @task(1)
    def create_ot(self):
        today = shapes.now().date()
        today_tz = shapes.tz()
        body = {
            "type": "ot",
            "work_date": today.isoformat(),
            "start_at": datetime(today.year, today.month, today.day, 18, 0, tzinfo=today_tz).isoformat(),
            "end_at": datetime(today.year, today.month, today.day, 20, 0, tzinfo=today_tz).isoformat(),
            "reason": "load test overtime",
        }
        self._write("request", "/api/requests", "/api/requests (create)",
                    body, str(uuid.uuid4()), True, "id")


if SCENARIO == "burst":
    EmployeeUser.wait_time = constant(0)
    ManagerUser.wait_time = constant(0)

# Locust spawns every non-abstract User class in the module, so disable the unused ones.
if SCENARIO == "login_storm":
    EmployeeUser.abstract = ManagerUser.abstract = WriterUser.abstract = True
elif SCENARIO in shapes.WRITE_SCENARIOS:
    EmployeeUser.abstract = ManagerUser.abstract = LoginUser.abstract = True
else:
    LoginUser.abstract = WriterUser.abstract = True


Shape = shapes.make_shape()
