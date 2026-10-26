"""Build charts and a Vietnamese markdown report from a load-test result dir.

Usage:
    report.py <result_dir>   # report.md + 4 PNG charts for one run
    report.py --summary      # results/SUMMARY.md, latest run of each scenario
"""
import json
import os
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

# Defaults; the effective values come from env, then meta.json["config"] (see apply_config).
TARGET_P95_MS = 500
TARGET_ERR_PCT = 1.0
BOTTLENECK_RATIO = 0.9
STEP_SECONDS = 60  # ramp step length (RAMP_STEP_SECONDS)
STEP_SETTLE_SECONDS = 15  # ignore the start of each step while users spawn
UNITS = {"b": 1, "kb": 1000, "kib": 1024, "mb": 1000**2, "mib": 1024**2,
         "gb": 1000**3, "gib": 1024**3}
SCENARIO_VI = {"steady": "Ổn định", "burst": "Dồn dập", "ramp": "Tăng dần", "login_storm": "Bão đăng nhập",
               "custom": "Tùy chỉnh", "write_storm": "Ghi dồn dập", "write_ramp": "Ghi tăng dần"}
RAMP_SCENARIOS = ("ramp", "write_ramp")
LOCUST_LABEL = "locust (máy bắn tải)"
LOCUST_WARNING = "Máy bắn tải đã kịch, số đo có thể thấp hơn khả năng thật của api"
DB_LOCK_PCT = 10  # share of active DB sessions waiting on one kind of event that counts as a finding
DB_CLIENT_PCT = 30
# Assigned to services in order of first appearance (sorted by name), cycling if needed.
PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf"]

plt.rcParams.update({"font.family": "DejaVu Sans", "axes.grid": True, "grid.alpha": 0.3,
                     "axes.spines.top": False, "axes.spines.right": False})


# ---------- loading ----------

def parse_size(text):
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text or "")
    return float(m.group(1)) * UNITS.get(m.group(2).lower(), 1) if m else 0.0


def load_stats(d):
    stats = pd.read_csv(d / "locust_stats.csv")
    for col in ("Request Count", "Failure Count"):
        stats[col] = stats[col].astype(int)
    return stats


def load_history(d):
    h = pd.read_csv(d / "locust_stats_history.csv")
    h = h[h["Name"] == "Aggregated"].copy()
    h["t"] = h["Timestamp"] - h["Timestamp"].iloc[0]
    for col in ("50%", "95%"):
        h[col] = pd.to_numeric(h[col], errors="coerce")  # "N/A" before first response
    return h.reset_index(drop=True), float(h["Timestamp"].iloc[0])


def apply_config(meta):
    """Set thresholds from env, else from the config used for the run (meta.json), else defaults."""
    global TARGET_P95_MS, TARGET_ERR_PCT, BOTTLENECK_RATIO, STEP_SECONDS, STEP_SETTLE_SECONDS
    cfg = meta.get("config", {})

    def get(name, default):
        return float(os.environ.get(name) or cfg.get(name) or default)

    TARGET_P95_MS = get("TARGET_P95_MS", 500)
    TARGET_ERR_PCT = get("TARGET_ERR_PCT", 1)
    BOTTLENECK_RATIO = get("BOTTLENECK_PCT", 90) / 100
    STEP_SECONDS = get("WRITE_RAMP_STEP_SECONDS" if meta.get("scenario") == "write_ramp" else "RAMP_STEP_SECONDS", 60)
    STEP_SETTLE_SECONDS = min(15, STEP_SECONDS / 3)


def service_name(container, project, common_prefix):
    """Container name -> service: drop the '<project>-' prefix and the '-<n>' suffix.
    The one-off Locust runner (<project>-<service>-run-<hash>) is shown as LOCUST_LABEL."""
    if re.search(r"-run-[0-9a-f]+$", container):
        return LOCUST_LABEL
    name = re.sub(r"[-_]\d+$", "", container)
    for prefix in (f"{project}-" if project else "", common_prefix):
        if prefix and name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix):]
    return name


def common_prefix(names):
    """Longest shared prefix ending at a '-' (compose project guess when meta has none)."""
    if len(names) < 2:
        return ""
    pre = os.path.commonprefix(list(names))
    return pre[:pre.rfind("-") + 1]


def load_docker(d, t0, meta=None):
    path = d / "docker_stats.jsonl"
    project = (meta or {}).get("project", "")
    records = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                j = json.loads(line)
                records.append((j["Name"], j))
            except (ValueError, KeyError):
                continue
    names = {re.sub(r"[-_]\d+$", "", n) for n, _ in records if not re.search(r"-run-[0-9a-f]+$", n)}
    prefix = "" if project else common_prefix(sorted(names))
    rows = []
    for name, j in records:
        used, _, limit = j.get("MemUsage", "").partition("/")
        rows.append({"t": float(j["ts"]) - t0, "service": service_name(name, project, prefix),
                     "cpu": float(j.get("CPUPerc", "0").strip("%") or 0),
                     "mem": parse_size(used), "mem_limit": parse_size(limit)})
    return pd.DataFrame(rows, columns=["t", "service", "cpu", "mem", "mem_limit"])


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def load_meta(d):
    return load_json(d / "meta.json")


def load_pg_samples(d):
    """Rows of pg_samples.jsonl as dicts (empty list when the file is missing)."""
    path = d / "pg_samples.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def load_top_queries(d):
    path = d / "pg_top_queries.csv"
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


# ---------- analysis ----------

def verdict(p95, err):
    return "ĐẠT" if p95 < TARGET_P95_MS and err < TARGET_ERR_PCT else "KHÔNG ĐẠT"


def totals(stats):
    agg = stats[stats["Name"] == "Aggregated"].iloc[0]
    n, f = int(agg["Request Count"]), int(agg["Failure Count"])
    return {"requests": n, "failures": f, "err": 100.0 * f / n if n else 0.0,
            "rps": float(agg["Requests/s"]), "p50": float(agg["50%"]),
            "p95": float(agg["95%"]), "p99": float(agg["99%"])}


def services_of(docker):
    return sorted(docker["service"].unique())


def service_colors(docker):
    return {svc: PALETTE[i % len(PALETTE)] for i, svc in enumerate(services_of(docker))}


def container_peaks(docker, meta):
    """Per service: peak CPU/RAM and limits. A missing limit means unlimited:
    CPU is then measured against all host CPUs, RAM against the limit docker reports."""
    limits = meta.get("limits", {})
    host_cpus = meta.get("host_cpus") or os.cpu_count() or 1
    out = {}
    for svc in services_of(docker):
        sub = docker[docker["service"] == svc]
        lim = limits.get(svc, {})
        cpu_lim, mem_lim = lim.get("cpu_limit"), lim.get("mem_limit_bytes")
        out[svc] = {"cpu": sub["cpu"].max(), "mem": sub["mem"].max(),
                    "cpu_limit": (cpu_lim or host_cpus) * 100, "cpu_unlimited": not cpu_lim,
                    "mem_limit": mem_lim or sub["mem_limit"].max(), "mem_unlimited": not mem_lim}
    return out


def bottlenecks(peaks):
    found = []
    for svc, p in peaks.items():
        if svc == LOCUST_LABEL:
            if p["cpu"] >= BOTTLENECK_RATIO * p["cpu_limit"]:
                found.append(f"**{svc}** chạm {p['cpu']:.0f}% / {p['cpu_limit']:.0f}% CPU. {LOCUST_WARNING}")
            continue
        if p["cpu"] >= BOTTLENECK_RATIO * p["cpu_limit"]:
            what = "chạm số CPU của máy" if p["cpu_unlimited"] else "chạm giới hạn CPU"
            found.append(f"**{svc}** {what} ({p['cpu']:.0f}% / {p['cpu_limit']:.0f}%)")
        if not p["mem_unlimited"] and p["mem"] >= BOTTLENECK_RATIO * p["mem_limit"]:
            found.append(f"**{svc}** gần hết RAM ({p['mem'] / 2**20:.0f} / {p['mem_limit'] / 2**20:.0f} MiB)")
    return found


def ramp_steps(hist, brk=None):
    """Per-step p95, error rate and RPS; the first step that breaks a target; the best
    RPS of a step that still met both targets. Steps after the break (the hold that
    BreakGuard adds to collect data) are marked hold=True and left out of both."""
    rows = []
    for idx, g in hist.groupby((hist["t"] // STEP_SECONDS).astype(int)):
        users = int(g["User Count"].max())
        settled = g[g["t"] - idx * STEP_SECONDS >= STEP_SETTLE_SECONDS]
        g = settled if not settled.empty else g
        rps = g["Requests/s"].sum()
        err = 100.0 * g["Failures/s"].sum() / rps if rps else 0.0
        rows.append({"users": users, "p95": float(g["95%"].median()), "err": err,
                     "rps": float(g["Requests/s"].mean()),
                     "hold": bool(brk) and idx > brk.get("step_index", idx)})
    steps = [r for r in rows if not r["hold"]]
    broken = next((r for r in steps if r["p95"] > TARGET_P95_MS or r["err"] > TARGET_ERR_PCT), None)
    passing = [r for r in steps if r["p95"] <= TARGET_P95_MS and r["err"] <= TARGET_ERR_PCT]
    best = max(passing, key=lambda r: r["rps"], default=None)
    return rows, broken, best


# ---------- database ----------

WAIT_MEANING = {
    "CPU": "đang chạy CPU, không chờ gì",
    "Lock": "tranh chấp khóa giữa các giao dịch",
    "LWLock": "tranh chấp khóa nhẹ trong bộ nhớ của Postgres",
    "IO": "chờ đọc/ghi đĩa",
    "Client": "DB chờ app gửi/nhận (app chậm)",
    "IPC": "chờ tiến trình khác của Postgres",
    "Timeout": "chờ hết giờ (nghỉ có chủ đích)",
}
WAL_EVENTS = ("WALWrite", "WALSync", "WALInsert", "WALBufMapping")


def wait_summary(samples):
    """Counts of active sessions per wait event over all samples -> (rows sorted desc, total)."""
    counts = {}
    for smp in samples:
        for w, n in (smp.get("waits") or {}).items():
            counts[w] = counts.get(w, 0) + int(n)
    total = sum(counts.values())
    return sorted(counts.items(), key=lambda kv: -kv[1]), total


def wait_meaning(w):
    kind, _, event = w.partition("/")
    if event in WAL_EVENTS:
        return "nghẽn ghi WAL (log giao dịch)"
    return WAIT_MEANING.get(kind, "")


def db_findings(samples):
    """Bottleneck sentences from the share of DB wait events (empty when nothing stands out)."""
    rows, total = wait_summary(samples)
    if not total:
        return []
    share = {"Lock": 0, "WAL": 0, "Client": 0}
    for w, n in rows:
        kind, _, event = w.partition("/")
        if event in WAL_EVENTS:
            share["WAL"] += n
        elif kind in share:
            share[kind] += n
    pct = {k: 100.0 * v / total for k, v in share.items()}
    out = []
    if pct["Lock"] >= DB_LOCK_PCT:
        out.append(f"**db** tranh chấp khóa ({pct['Lock']:.0f}% session đang chạy phải chờ Lock)")
    if pct["WAL"] >= DB_LOCK_PCT:
        out.append(f"**db** nghẽn ghi WAL ({pct['WAL']:.0f}% session chờ WALWrite/WALSync)")
    if pct["Client"] >= DB_CLIENT_PCT:
        out.append(f"**app** chậm hơn DB ({pct['Client']:.0f}% session đang chạy chỉ chờ Client)")
    return out


def tps_series(samples, t0):
    """(t, TPS, active connections) per sample from the pg_stat_database counters."""
    rows = []
    prev = None
    for smp in samples:
        try:
            ts = float(smp["ts"])
            xacts = int(smp["xact_commit"]) + int(smp["xact_rollback"])
        except (KeyError, TypeError, ValueError):
            continue
        if prev is not None and ts > prev[0]:
            rows.append({"t": ts - t0, "tps": (xacts - prev[1]) / (ts - prev[0]),
                         "active": int((smp.get("states") or {}).get("active", 0))})
        prev = (ts, xacts)
    return pd.DataFrame(rows, columns=["t", "tps", "active"])


# ---------- charts ----------

def save(fig, path):
    fig.savefig(path, dpi=100)  # figsize 12x6 in => 1200x600 px
    plt.close(fig)


def chart_latency(hist, path):
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(hist["t"], hist["50%"], label="p50", color="#2ca02c", lw=2)
    ax.plot(hist["t"], hist["95%"], label="p95", color="#d62728", lw=2)
    ax.axhline(TARGET_P95_MS, color="#7f7f7f", ls="--", label=f"Mục tiêu p95 ({TARGET_P95_MS:g} ms)")
    ax.set_xlabel("Thời gian (giây)")
    ax.set_ylabel("Thời gian phản hồi (ms)")
    ax2 = ax.twinx()
    ax2.fill_between(hist["t"], hist["User Count"], step="post", alpha=0.15, color="#1f77b4")
    ax2.set_ylim(bottom=0)
    ax2.set_ylabel("Số user")
    ax2.grid(False)
    ax2.spines["right"].set_visible(True)
    ax.set_title("Thời gian phản hồi p50 / p95 và số user")
    ax.legend(loc="upper left")
    save(fig, path)


def chart_rps(hist, path):
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(hist["t"], hist["Requests/s"], label="Request/giây", color="#1f77b4", lw=2)
    ax.plot(hist["t"], hist["Failures/s"], label="Lỗi/giây", color="#d62728", lw=2)
    ax.set_xlabel("Thời gian (giây)")
    ax.set_ylabel("Số lượng / giây")
    ax.set_title("Thông lượng (RPS) và lỗi theo thời gian")
    ax.legend(loc="upper left")
    save(fig, path)


def chart_cpu(docker, peaks, path):
    fig, ax = plt.subplots(figsize=(12, 6))
    if docker.empty:
        ax.text(0.5, 0.5, "Không có dữ liệu docker stats", ha="center", va="center", transform=ax.transAxes)
    colors = service_colors(docker)
    for svc in services_of(docker):
        sub = docker[docker["service"] == svc]
        ax.plot(sub["t"], sub["cpu"], label=svc, color=colors[svc], lw=2)
    if peaks:
        top = max(peaks, key=lambda k: peaks[k]["cpu"])  # service with the highest CPU
        lim = peaks[top]
        kind = "số CPU máy" if lim["cpu_unlimited"] else "giới hạn"
        ax.axhline(lim["cpu_limit"], color=colors[top], ls=":", alpha=0.7,
                   label=f"{kind.capitalize()} {top} ({lim['cpu_limit']:.0f}%)")
    ax.set_xlabel("Thời gian (giây)")
    ax.set_ylabel("CPU (%, 100% = 1 core)")
    ax.set_title("CPU của từng container (đường chấm = giới hạn CPU của container cao nhất)")
    ax.legend(loc="upper left")
    save(fig, path)


def chart_db(series, path):
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(series["t"], series["tps"], label="TPS (commit + rollback / giây)", color="#1f77b4", lw=2)
    ax.set_xlabel("Thời gian (giây)")
    ax.set_ylabel("TPS")
    ax2 = ax.twinx()
    ax2.plot(series["t"], series["active"], label="Connection active", color="#ff7f0e", lw=2)
    ax2.set_ylim(bottom=0)
    ax2.set_ylabel("Số connection active")
    ax2.grid(False)
    ax2.spines["right"].set_visible(True)
    ax.set_title("TPS của DB và số connection active")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left")
    save(fig, path)


# ---------- single-run report ----------

def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def analyse(d):
    stats, meta = load_stats(d), load_meta(d)
    hist, t0 = load_history(d)
    apply_config(meta)
    runner = load_json(d / "runner_limits.json")  # limits of the Locust runner, written by run.sh
    if runner:
        meta.setdefault("limits", {})[LOCUST_LABEL] = runner
    docker = load_docker(d, t0, meta)
    peaks = container_peaks(docker, meta)
    return stats, meta, hist, docker, peaks, totals(stats)


def db_sections(d, samples):
    """Report sections from the Postgres samples and the top-queries CSV."""
    lines = []
    if samples:
        lines += ["", "## TPS của DB", ""]
        series = tps_series(samples, 0)
        if series.empty:
            lines.append("Không đủ mẫu để tính TPS.")
        else:
            lines.append(f"TPS trung bình {series['tps'].mean():.0f}, cao nhất {series['tps'].max():.0f}; "
                         f"connection active cao nhất {series['active'].max()}. Biểu đồ ở cuối báo cáo.")
        rows, total = wait_summary(samples)
        lines += ["", "## DB đang chờ gì", ""]
        if total:
            lines.append("Mỗi 2 giây đếm các session đang chạy theo wait event. Nhiều `Lock` nghĩa là tranh chấp "
                         "khóa, `LWLock/WALWrite` nghĩa là nghẽn ghi WAL, `Client` nghĩa là app chậm.")
            lines.append("")
            lines.append(md_table(["Wait event", "Số lần thấy", "Tỷ lệ", "Ý nghĩa"], [
                [f"`{w}`", n, f"{100.0 * n / total:.1f}%", wait_meaning(w)] for w, n in rows[:8]]))
        else:
            lines.append("Không có session nào đang chạy trong các mẫu.")
    top = load_top_queries(d)
    lines += ["", "## Truy vấn tốn nhất", ""]
    if top.empty:
        lines.append("Không có số liệu pg_stat_statements.")
    else:
        lines.append(md_table(["Lần gọi", "Trung bình (ms)", "Tổng (ms)", "Số dòng", "Câu SQL"], [
            [int(r["calls"]), f"{r['mean_ms']:.2f}", f"{r['total_ms']:.1f}", int(r["rows"]),
             "`" + str(r["query"]).replace("|", "\\|").replace("`", "'") + "`"]
            for _, r in top.iterrows()]))
    return lines


def report_one(d):
    d = Path(d)
    stats, meta, hist, docker, peaks, tot = analyse(d)
    scenario = meta.get("scenario", d.name)
    chart_latency(hist, d / "chart_latency.png")
    chart_rps(hist, d / "chart_rps.png")
    chart_cpu(docker, peaks, d / "chart_cpu.png")
    samples = load_pg_samples(d)
    series = tps_series(samples, load_history(d)[1])
    has_db_chart = not series.empty
    if has_db_chart:
        chart_db(series, d / "chart_db.png")

    lines = [f"# Báo cáo load test: {scenario} ({SCENARIO_VI.get(scenario, scenario)})", ""]
    lines.append(f"- Địa chỉ bắn: `{meta.get('host', '?')}`")
    lines.append(f"- Thời điểm chạy: {meta.get('started_at', '?')} (commit `{meta.get('git_commit', '?')}`)")
    n_vi = f"{tot['requests']:,}".replace(",", ".")
    lines.append(f"- Tổng số request: {n_vi}, trung bình {tot['rps']:.1f} request/giây")
    rl = meta.get("rate_limit_enabled")
    lines.append(f"- RATE_LIMIT_ENABLED của api: {'chưa đặt (app dùng mặc định)' if rl is None else f'`{rl}`'}")
    lines += ["", "## Mục tiêu so với thực tế", ""]
    lines.append(md_table(["Chỉ số", "Mục tiêu", "Thực tế", "Kết quả"], [
        ["p95 thời gian phản hồi", f"< {TARGET_P95_MS:g} ms", f"{tot['p95']:.0f} ms",
         "ĐẠT" if tot["p95"] < TARGET_P95_MS else "KHÔNG ĐẠT"],
        ["Tỷ lệ lỗi", f"< {TARGET_ERR_PCT:g}%", f"{tot['err']:.2f}%",
         "ĐẠT" if tot["err"] < TARGET_ERR_PCT else "KHÔNG ĐẠT"],
    ]))
    lines += ["", f"**Kết luận: {verdict(tot['p95'], tot['err'])}**", ""]

    eps = stats[(stats["Name"] != "Aggregated") & (stats["Type"] != "EVENT")]
    lines += ["## Theo từng endpoint", ""]
    lines.append(md_table(["Endpoint", "Số request", "p50 (ms)", "p95 (ms)", "p99 (ms)", "% lỗi"], [
        [f"`{r['Type']} {r['Name']}`", r["Request Count"], f"{r['50%']:.0f}", f"{r['95%']:.0f}",
         f"{r['99%']:.0f}", f"{100.0 * r['Failure Count'] / r['Request Count']:.2f}" if r["Request Count"] else "0.00"]
        for _, r in eps.iterrows()
    ] + [[f"**Tổng**", tot["requests"], f"{tot['p50']:.0f}", f"{tot['p95']:.0f}", f"{tot['p99']:.0f}", f"{tot['err']:.2f}"]]))
    events = stats[stats["Type"] == "EVENT"]
    if not events.empty:
        lines += ["", "Kết quả hợp lệ được đếm riêng (không tính là lỗi, không nằm trong tổng):", ""]
        lines += [f"- {r['Name']}: {int(r['Request Count'])} lần" for _, r in events.iterrows()]

    lines += ["", "## CPU và RAM cao nhất", ""]
    if peaks:
        lines.append(md_table(["Container", "CPU cao nhất", "Giới hạn CPU", "RAM cao nhất", "Giới hạn RAM"], [
            [svc, f"{p['cpu']:.0f}%",
             f"không giới hạn (máy có {p['cpu_limit'] / 100:g} CPU)" if p["cpu_unlimited"] else f"{p['cpu_limit']:.0f}%",
             f"{p['mem'] / 2**20:.0f} MiB",
             "không giới hạn" if p["mem_unlimited"] else f"{p['mem_limit'] / 2**20:.0f} MiB"]
            for svc, p in peaks.items()]))
    else:
        lines.append("Không có dữ liệu docker stats.")
    found = bottlenecks(peaks) + db_findings(samples)
    lines += ["", "**Điểm nghẽn:** " + ("; ".join(found) if found else
              f"không container nào chạm {BOTTLENECK_RATIO * 100:.0f}% giới hạn CPU hoặc RAM, "
              "và DB không có wait event nào nổi bật.")]

    lines += db_sections(d, samples)

    if scenario in RAMP_SCENARIOS:
        brk = load_json(d / "break.json")
        steps, broken, best = ramp_steps(hist, brk)
        lines += ["", "## Điểm gãy (ramp)", ""]
        lines.append(md_table(["Số user", "p95 (ms)", "% lỗi", "RPS"], [
            [f"{s['users']}" + (" (giữ thêm sau khi gãy)" if s["hold"] else ""), f"{s['p95']:.0f}",
             f"{s['err']:.2f}", f"{s['rps']:.1f}"] for s in steps]))
        what = "RPS ghi" if scenario == "write_ramp" else "RPS"
        lines += ["", "**Điểm gãy:** " + (
            f"{broken['users']} user (p95 {broken['p95']:.0f} ms, lỗi {broken['err']:.2f}%)" if broken
            else "chưa gãy ở mức user cao nhất đã thử."),
            "", f"**{what} cao nhất vẫn đạt mục tiêu:** " + (
                f"{best['rps']:.1f}/giây (ở {best['users']} user)" if best else "không bậc nào đạt mục tiêu."),
            "", "**Tự dừng:** " + (
                f"dừng ở bậc {brk['step_index'] + 1} ({brk['users']} user): {brk['reason']}; "
                f"giữ tải thêm {brk['hold_seconds']:g} giây để thu số liệu."
                if brk else "không bậc nào vượt ngưỡng dừng, đã chạy hết các bậc.")]

    lines += ["", "## Biểu đồ", "", "![Thời gian phản hồi](chart_latency.png)", "",
              "![RPS và lỗi](chart_rps.png)", "", "![CPU container](chart_cpu.png)", ""]
    if has_db_chart:
        lines += ["![TPS của DB](chart_db.png)", ""]
    (d / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {d / 'report.md'}")


# ---------- summary ----------

def report_summary(results):
    results = Path(results)
    latest = {}
    for d in sorted(p for p in results.iterdir() if p.is_dir() and (p / "locust_stats.csv").exists()):
        latest[load_meta(d).get("scenario") or d.name.split("-", 2)[-1]] = d  # sorted => newest wins
    rows = []
    for sc in [s for s in SCENARIO_VI if s in latest] + [s for s in latest if s not in SCENARIO_VI]:
        d = latest[sc]
        _, meta, hist, _, peaks, tot = analyse(d)
        found = bottlenecks(peaks)
        extra = ""
        if sc in RAMP_SCENARIOS:
            _, broken, _ = ramp_steps(hist, load_json(d / "break.json"))
            extra = f"gãy ở {broken['users']} user" if broken else "chưa gãy"
        note = "; ".join(x for x in (", ".join(re.findall(r"\*\*([\w.-]+)\*\*", "; ".join(found))), extra) if x)
        rows.append([sc, d.name, meta.get("host", "?"), f"{tot['rps']:.1f}", f"{tot['p95']:.0f} ms",
                     f"{tot['err']:.2f}%", verdict(tot["p95"], tot["err"]), note or "-"])
    lines = ["# Tổng hợp load test", "", "Mỗi kịch bản lấy lần chạy mới nhất.", ""]
    lines.append(md_table(["Kịch bản", "Lần chạy", "Host", "RPS", "p95", "% lỗi", "Kết quả", "Điểm nghẽn / ghi chú"], rows)
                 if rows else "Chưa có kết quả nào.")
    (results / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {results / 'SUMMARY.md'}")


def main(argv):
    if len(argv) == 2 and argv[1] == "--summary":
        report_summary(Path(__file__).resolve().parent / "results")
    elif len(argv) == 2:
        report_one(argv[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
