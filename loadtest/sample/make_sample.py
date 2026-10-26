"""Generate FAKE Locust CSVs and docker_stats.jsonl (ramp-like) to test report.py.

Usage: python make_sample.py  (writes next to this file)
"""
import csv
import json
import random
from pathlib import Path

random.seed(7)
out = Path(__file__).resolve().parent
T0 = 1_760_000_000
STEPS = [50, 100, 150, 200, 300]
PCTS = ["50%", "66%", "75%", "80%", "90%", "95%", "98%", "99%", "99.9%", "99.99%", "100%"]
ENDPOINTS = [("POST", "/api/auth/login", 0.01), ("POST", "/api/attendance/check", 0.25),
             ("GET", "/api/attendance/me", 0.33), ("GET", "/api/me", 0.14),
             ("GET", "/api/requests/me", 0.14), ("POST", "/api/requests (create)", 0.06),
             ("GET", "/api/requests?status=pending", 0.03), ("POST", "/api/requests/[id]/approve", 0.04)]


def pct_row(p50, p95):
    vals = [p50, p50 * 1.2, p50 * 1.4, p50 * 1.6, p95 * 0.7, p95, p95 * 1.2, p95 * 1.4, p95 * 2, p95 * 2.5, p95 * 3]
    return [f"{v:.0f}" for v in vals]


hist_rows, docker_rows = [], []
total_req = total_fail = 0
for t in range(300):
    users = STEPS[t // 60]
    load = users / 100
    rps = min(users / 2.0, 190) * random.uniform(0.95, 1.05)
    p50 = 30 + 25 * load ** 2.2 + random.uniform(-3, 3)
    p95 = 90 + 110 * load ** 2.6 + random.uniform(-8, 8)
    fails = 0 if users < 200 else rps * (0.004 if users == 200 else 0.03)
    total_req += int(rps)
    total_fail += int(fails)
    hist_rows.append([T0 + t, users, "", "Aggregated", f"{rps:.2f}", f"{fails:.2f}", *pct_row(p50, p95),
                      total_req, total_fail, f"{p50:.0f}", f"{p50 * 1.1:.0f}", 5, 900, 420])
    if t % 2 == 0:
        cpu = {"api": min(40 + rps * 1.05, 200) + random.uniform(-4, 4), "db": 8 + rps * 0.22,
               "worker": 1 + random.random(), "redis": 2 + rps * 0.04}
        mem = {"api": 180 + users * 0.6, "db": 700 + users * 1.2, "worker": 90, "redis": 12}
        lim = {"api": "2GiB", "db": "2GiB", "worker": "512MiB", "redis": "256MiB"}
        for svc in cpu:
            docker_rows.append({"ts": T0 + t + 0.5, "Name": f"chamcong-demo-{svc}-1",
                                "CPUPerc": f"{cpu[svc]:.2f}%", "MemUsage": f"{mem[svc]:.1f}MiB / {lim[svc]}"})

hist_hdr = ["Timestamp", "User Count", "Type", "Name", "Requests/s", "Failures/s", *PCTS,
            "Total Request Count", "Total Failure Count", "Total Median Response Time",
            "Total Average Response Time", "Total Min Response Time", "Total Max Response Time",
            "Total Average Content Size"]
with open(out / "locust_stats_history.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(hist_hdr)
    w.writerows(hist_rows)

stats_hdr = ["Type", "Name", "Request Count", "Failure Count", "Median Response Time",
             "Average Response Time", "Min Response Time", "Max Response Time", "Average Content Size",
             "Requests/s", "Failures/s", *PCTS]
with open(out / "locust_stats.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(stats_hdr)
    for method, name, share in ENDPOINTS:
        n = int(total_req * share)
        fl = int(total_fail * share)
        w.writerow([method, name, n, fl, 40, 55, 4, 800, 300, f"{n / 300:.2f}", f"{fl / 300:.2f}", *pct_row(40, 190)])
    w.writerow(["EVENT", "approve 409 (contention)", 37, 0, 0, 0, 0, 0, 0, "0.12", "0.00", *["0"] * 11])
    w.writerow(["", "Aggregated", total_req, total_fail, 42, 58, 4, 900, 300, f"{total_req / 300:.2f}",
                f"{total_fail / 300:.2f}", *pct_row(42, 230)])

(out / "docker_stats.jsonl").write_text("\n".join(json.dumps(r) for r in docker_rows) + "\n")
(out / "meta.json").write_text(json.dumps({
    "scenario": "ramp", "host": "http://api:8000", "started_at": "SAMPLE (fake data)", "git_commit": "0000000",
    "limits": {"api": {"cpu_limit": 2.0, "mem_limit_bytes": 2 * 1024**3},
               "db": {"cpu_limit": 1.0, "mem_limit_bytes": 2 * 1024**3},
               "worker": {"cpu_limit": 1.0, "mem_limit_bytes": 512 * 1024**2},
               "redis": {"cpu_limit": 0.5, "mem_limit_bytes": 256 * 1024**2}}}, indent=2) + "\n")
print("sample written")
