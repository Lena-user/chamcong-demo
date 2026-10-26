"""Seed fake demo data. Usage: python -m scripts.seed (idempotent: wipes then recreates)."""
import argparse
import asyncio
import json
import os
import random
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import asyncpg

from app.core.config import get_settings
from app.core.security import _hash

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
N_EMPLOYEES = 300
N_DEPARTMENTS = 5
DAYS_BACK = 91
PENDING_REQUESTS = 40
DECIDED_REQUESTS = 40
DEPARTMENT_NAMES = ["Kinh doanh", "Ke toan", "San xuat", "Kho van", "Nhan su"]
LAST = ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Vu", "Dang", "Bui", "Do", "Ngo"]
MIDDLE = ["Van", "Thi", "Minh", "Quoc", "Thanh", "Hong", "Duc", "Ngoc"]
FIRST = ["An", "Binh", "Cuong", "Dung", "Giang", "Hai", "Hanh", "Hung", "Lan", "Linh",
         "Mai", "Nam", "Phuc", "Quynh", "Son", "Thao", "Trang", "Tu", "Viet", "Yen"]
SHIFTS = [  # name, start, end, crosses_midnight
    ("Ca sang", dtime(8, 0), dtime(17, 0), False),
    ("Ca chieu", dtime(13, 0), dtime(22, 0), False),
    ("Ca dem", dtime(22, 0), dtime(6, 0), True),
]
PAYROLL_PARAMS = {
    "standard_days": 26,
    "hours_per_day": 8,
    "ot_multiplier": 1.5,
    "social_insurance_rate": 0.08,
    "health_insurance_rate": 0.015,
    "unemployment_insurance_rate": 0.01,
    "personal_deduction": 15_500_000,
    "dependent_deduction": 6_200_000,
    "tax_brackets": [  # (upper bound of taxable income, rate); null = no upper bound
        [10_000_000, 0.05], [30_000_000, 0.10], [60_000_000, 0.20],
        [100_000_000, 0.30], [None, 0.35],
    ],
}
TABLES = ["audit_logs", "payslips", "payroll_periods", "payroll_rules", "requests",
          "attendance_daily", "attendance_events", "schedules", "shifts",
          "salary_contracts", "employee_sensitive", "employees"]


def departments(n: int) -> list[str]:
    return [DEPARTMENT_NAMES[i] if i < len(DEPARTMENT_NAMES) else f"Phong ban {i + 1}" for i in range(n)]


def role_for(i: int, n_departments: int = N_DEPARTMENTS) -> str:
    """E0001 admin, E0002-3 hr, then two managers per department, the rest employees."""
    if i == 1:
        return "admin"
    if i in (2, 3):
        return "hr"
    if 4 <= i <= 3 + 2 * n_departments:
        return "manager"
    return "employee"


def dept_for(i: int, depts: list[str]) -> str:
    if 4 <= i <= 3 + 2 * len(depts):
        return depts[(i - 4) // 2]
    return depts[i % len(depts)]


def work_date_of(local_dt: datetime, crossing_by_date: dict[date, bool]) -> date:
    """Night-shift checkouts after midnight belong to the day the shift started."""
    d = local_dt.date()
    if local_dt.hour < 12 and crossing_by_date.get(d - timedelta(days=1)):
        return d - timedelta(days=1)
    return d


def compute_daily(events: list[datetime], crossing_by_date: dict[date, bool]) -> dict[date, dict]:
    """Rebuild attendance_daily rows from raw event times (first/last event per work date)."""
    grouped: dict[date, list[datetime]] = defaultdict(list)
    for t in events:
        local = t.astimezone(TZ)
        grouped[work_date_of(local, crossing_by_date)].append(t)
    out = {}
    for wd, ts in grouped.items():
        first, last = min(ts), max(ts)
        minutes = int((last - first).total_seconds() // 60) if len(ts) > 1 else 0
        if minutes > 360:
            minutes -= 60  # unpaid break
        out[wd] = {"first_in": first, "last_out": last if len(ts) > 1 else None,
                   "worked_minutes": minutes, "ot_minutes": max(0, minutes - 480)}
    return out


async def main(n_employees: int, n_departments: int, days_back: int, n_pending: int) -> None:
    t0 = time.perf_counter()
    depts = departments(n_departments)
    n_managers = 2 * n_departments
    rng = random.Random(42)
    password = os.environ["SEED_PASSWORD"]
    pw_hash = _hash(password)  # hashed once, shared by every account
    dsn = get_settings().database_url.replace("+asyncpg", "")
    today = datetime.now(TZ).date()
    conn = await asyncpg.connect(dsn)
    counts: dict[str, int] = {}
    try:
        async with conn.transaction():
            await conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")

            emp_rows, sens_rows, contract_rows = [], [], []
            for i in range(1, n_employees + 1):
                name = f"{rng.choice(LAST)} {rng.choice(MIDDLE)} {rng.choice(FIRST)}"
                emp_rows.append((f"E{i:04d}", name, dept_for(i, depts), date(2023, 1, 1) + timedelta(days=rng.randint(0, 900)),
                                 None, role_for(i, n_departments), pw_hash, 1))
            await conn.copy_records_to_table(
                "employees", records=emp_rows,
                columns=["code", "full_name", "department", "hired_on", "left_on", "role", "password_hash", "version"])
            ids = {r["code"]: r["id"] for r in await conn.fetch("SELECT id, code FROM employees")}
            emp_ids = [ids[f"E{i:04d}"] for i in range(1, n_employees + 1)]

            for eid in emp_ids:
                sens_rows.append((eid, "".join(rng.choices("0123456789", k=12)),
                                  "".join(rng.choices("0123456789", k=12)),
                                  "".join(rng.choices("0123456789", k=10)), rng.randint(0, 3)))
                contract_rows.append((eid, rng.randint(80, 300) * 100_000, rng.randint(5, 20) * 100_000,
                                      date(2024, 1, 1), None))
            await conn.copy_records_to_table(
                "employee_sensitive", records=sens_rows,
                columns=["employee_id", "national_id", "bank_account", "tax_code", "dependents"])
            await conn.copy_records_to_table(
                "salary_contracts", records=contract_rows,
                columns=["employee_id", "base_salary", "allowance", "effective_from", "effective_to"])

            await conn.copy_records_to_table(
                "shifts", records=SHIFTS, columns=["name", "start_time", "end_time", "crosses_midnight"])
            shift_ids = [r["id"] for r in await conn.fetch("SELECT id FROM shifts ORDER BY id")]

            # Schedule + attendance events (Mon-Sat, ~3% absent days, ~10% of staff on the night shift)
            sched_rows, event_rows, daily_rows = [], [], []
            start = today - timedelta(days=days_back)
            for idx, eid in enumerate(emp_ids):
                if rng.random() < 0.10:
                    shift_idx = 2
                else:
                    shift_idx = idx % 2
                _, s_time, e_time, crosses = SHIFTS[shift_idx]
                crossing_by_date: dict[date, bool] = {}
                times: list[datetime] = []
                for n in range(days_back + 1):
                    d = start + timedelta(days=n)
                    if d.weekday() == 6:
                        continue
                    sched_rows.append((eid, d, shift_ids[shift_idx]))
                    crossing_by_date[d] = crosses
                    if d >= today or rng.random() < 0.03:
                        continue
                    t_in = datetime.combine(d, s_time, TZ) + timedelta(minutes=rng.randint(-10, 10))
                    t_out = datetime.combine(d + timedelta(days=int(crosses)), e_time, TZ) + timedelta(minutes=rng.randint(0, 30))
                    for t in (t_in, t_out):
                        times.append(t)
                        event_rows.append((eid, t, rng.choice(["web", "web", "mobile"]), uuid.uuid4()))
                for wd, v in compute_daily(times, crossing_by_date).items():
                    daily_rows.append((eid, wd, v["first_in"], v["last_out"], v["worked_minutes"], v["ot_minutes"]))

            await conn.copy_records_to_table("schedules", records=sched_rows,
                                             columns=["employee_id", "work_date", "shift_id"])
            await conn.copy_records_to_table("attendance_events", records=event_rows,
                                             columns=["employee_id", "event_time", "source", "idempotency_key"])
            await conn.copy_records_to_table(
                "attendance_daily", records=daily_rows,
                columns=["employee_id", "work_date", "first_in", "last_out", "worked_minutes", "ot_minutes"])

            # Requests: some decided (older), some pending (current month)
            req_rows = []
            managers = emp_ids[3 : 3 + n_managers]
            for n in range(n_pending + DECIDED_REQUESTS):
                eid = rng.choice(emp_ids[3 + n_managers :])
                typ = rng.choice(["leave", "adjustment", "ot"])
                pending = n < n_pending
                wd = today - timedelta(days=rng.randint(1, 20 if pending else 60))
                status = "pending" if pending else rng.choice(["approved", "rejected"])
                created = datetime.combine(wd, dtime(9, 0), TZ)
                req_rows.append((eid, typ, wd, None, None, f"Demo {typ} request", status,
                                 None if pending else rng.choice(managers),
                                 None if pending else created + timedelta(days=1), created))
            await conn.copy_records_to_table(
                "requests", records=req_rows,
                columns=["employee_id", "type", "work_date", "start_at", "end_at", "reason", "status",
                         "approved_by", "decided_at", "created_at"])

            await conn.execute("INSERT INTO payroll_periods (year, month, status) VALUES ($1, $2, 'draft')",
                               today.year, today.month)
            await conn.execute(
                "INSERT INTO payroll_rules (version, effective_from, params, note) VALUES (1, $1, $2::jsonb, $3)",
                date(2024, 1, 1), json.dumps(PAYROLL_PARAMS),
                "dữ liệu demo, cần kế toán xác nhận")

            for t in TABLES:
                counts[t] = await conn.fetchval(f"SELECT count(*) FROM {t}")
    finally:
        await conn.close()
    print("Seed complete:")
    for t, c in counts.items():
        print(f"  {t}: {c}")
    print(f"Elapsed: {time.perf_counter() - t0:.1f}s")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Seed fake demo data (wipes existing data first).")
    ap.add_argument("--employees", type=int, default=N_EMPLOYEES)
    ap.add_argument("--departments", type=int, default=N_DEPARTMENTS)
    ap.add_argument("--days", type=int, default=DAYS_BACK, help="days of attendance history")
    ap.add_argument("--pending-requests", type=int, default=PENDING_REQUESTS)
    args = ap.parse_args()
    if args.departments < 1:
        ap.error("--departments must be at least 1")
    if args.employees < 4 + 2 * args.departments:
        ap.error("--employees must leave room for 1 admin, 2 hr, 2 managers per department and 1 employee")
    if args.days < 1 or args.pending_requests < 0:
        ap.error("--days must be >= 1 and --pending-requests >= 0")
    return args


if __name__ == "__main__":
    a = parse_args()
    asyncio.run(main(a.employees, a.departments, a.days, a.pending_requests))
