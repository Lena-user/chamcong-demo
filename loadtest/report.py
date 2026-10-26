"""Build charts and a Vietnamese markdown report from a load-test result dir.

Usage:
    report.py <result_dir>   # report.md + 3 PNG charts for one run
    report.py --summary      # results/SUMMARY.md, latest run of each scenario
"""
import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

TARGET_P95_MS = 500
TARGET_ERR_PCT = 1.0
BOTTLENECK_RATIO = 0.9
STEP_SECONDS = 60  # ramp step length, must match locustfile.py
STEP_SETTLE_SECONDS = 15  # ignore the start of each step while users spawn
SERVICES = ["api", "db", "worker", "redis"]
DEFAULT_LIMITS = {  # PROJECT.md section 8, used if meta.json has no limits
    "api": (2.0, 2 * 1024**3), "db": (1.0, 2 * 1024**3),
    "worker": (1.0, 512 * 1024**2), "redis": (0.5, 256 * 1024**2),
}
UNITS = {"b": 1, "kb": 1000, "kib": 1024, "mb": 1000**2, "mib": 1024**2,
         "gb": 1000**3, "gib": 1024**3}
SCENARIO_VI = {"steady": "Ổn định", "burst": "Dồn dập", "ramp": "Tăng dần", "login_storm": "Bão đăng nhập"}
COLORS = {"api": "#1f77b4", "db": "#d62728", "worker": "#2ca02c", "redis": "#ff7f0e"}

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


def load_docker(d, t0):
    path = d / "docker_stats.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                j = json.loads(line)
            except ValueError:
                continue
            m = re.search(r"(?:^|[-_])(api|db|worker|redis)(?:[-_]\d+)?$", j.get("Name", ""))
            if not m:
                continue
            used, _, limit = j.get("MemUsage", "").partition("/")
            rows.append({"t": float(j["ts"]) - t0, "service": m.group(1),
                         "cpu": float(j.get("CPUPerc", "0").strip("%") or 0),
                         "mem": parse_size(used), "mem_limit": parse_size(limit)})
    return pd.DataFrame(rows, columns=["t", "service", "cpu", "mem", "mem_limit"])


def load_meta(d):
    p = d / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ---------- analysis ----------

def verdict(p95, err):
    return "ĐẠT" if p95 < TARGET_P95_MS and err < TARGET_ERR_PCT else "KHÔNG ĐẠT"


def totals(stats):
    agg = stats[stats["Name"] == "Aggregated"].iloc[0]
    n, f = int(agg["Request Count"]), int(agg["Failure Count"])
    return {"requests": n, "failures": f, "err": 100.0 * f / n if n else 0.0,
            "rps": float(agg["Requests/s"]), "p50": float(agg["50%"]),
            "p95": float(agg["95%"]), "p99": float(agg["99%"])}


def container_peaks(docker, meta):
    limits = meta.get("limits", {})
    out = {}
    for svc in SERVICES:
        sub = docker[docker["service"] == svc]
        if sub.empty:
            continue
        cpu_lim, mem_lim = DEFAULT_LIMITS[svc]
        lim = limits.get(svc, {})
        if lim.get("cpu_limit"):
            cpu_lim = lim["cpu_limit"]
        if lim.get("mem_limit_bytes"):
            mem_lim = lim["mem_limit_bytes"]
        out[svc] = {"cpu": sub["cpu"].max(), "cpu_limit": cpu_lim * 100,
                    "mem": sub["mem"].max(), "mem_limit": mem_lim}
    return out


def bottlenecks(peaks):
    found = []
    for svc, p in peaks.items():
        if p["cpu"] >= BOTTLENECK_RATIO * p["cpu_limit"]:
            found.append(f"**{svc}** chạm giới hạn CPU ({p['cpu']:.0f}% / {p['cpu_limit']:.0f}%)")
        if p["mem"] >= BOTTLENECK_RATIO * p["mem_limit"]:
            found.append(f"**{svc}** gần hết RAM ({p['mem'] / 2**20:.0f} / {p['mem_limit'] / 2**20:.0f} MiB)")
    return found


def ramp_steps(hist):
    """Per-step p95 and error rate, and the first step that breaks a target."""
    rows = []
    for idx, g in hist.groupby((hist["t"] // STEP_SECONDS).astype(int)):
        users = int(g["User Count"].max())
        settled = g[g["t"] - idx * STEP_SECONDS >= STEP_SETTLE_SECONDS]
        g = settled if not settled.empty else g
        rps = g["Requests/s"].sum()
        err = 100.0 * g["Failures/s"].sum() / rps if rps else 0.0
        rows.append({"users": users, "p95": float(g["95%"].median()), "err": err,
                     "rps": float(g["Requests/s"].mean())})
    broken = next((r for r in rows if r["p95"] > TARGET_P95_MS or r["err"] > TARGET_ERR_PCT), None)
    return rows, broken


# ---------- charts ----------

def save(fig, path):
    fig.savefig(path, dpi=100)  # figsize 12x6 in => 1200x600 px
    plt.close(fig)


def chart_latency(hist, path):
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(hist["t"], hist["50%"], label="p50", color="#2ca02c", lw=2)
    ax.plot(hist["t"], hist["95%"], label="p95", color="#d62728", lw=2)
    ax.axhline(TARGET_P95_MS, color="#7f7f7f", ls="--", label=f"Mục tiêu p95 ({TARGET_P95_MS} ms)")
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
    for svc in SERVICES:
        sub = docker[docker["service"] == svc]
        if not sub.empty:
            ax.plot(sub["t"], sub["cpu"], label=svc, color=COLORS[svc], lw=2)
    if "api" in peaks:
        ax.axhline(peaks["api"]["cpu_limit"], color=COLORS["api"], ls=":", alpha=0.7,
                   label=f"Giới hạn api ({peaks['api']['cpu_limit']:.0f}%)")
    ax.set_xlabel("Thời gian (giây)")
    ax.set_ylabel("CPU (%, 100% = 1 core)")
    ax.set_title("CPU của từng container (200% = kịch 2 CPU)")
    ax.legend(loc="upper left")
    save(fig, path)


# ---------- single-run report ----------

def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def analyse(d):
    stats, meta = load_stats(d), load_meta(d)
    hist, t0 = load_history(d)
    docker = load_docker(d, t0)
    peaks = container_peaks(docker, meta)
    return stats, meta, hist, docker, peaks, totals(stats)


def report_one(d):
    d = Path(d)
    stats, meta, hist, docker, peaks, tot = analyse(d)
    scenario = meta.get("scenario", d.name)
    chart_latency(hist, d / "chart_latency.png")
    chart_rps(hist, d / "chart_rps.png")
    chart_cpu(docker, peaks, d / "chart_cpu.png")

    lines = [f"# Báo cáo load test: {scenario} ({SCENARIO_VI.get(scenario, scenario)})", ""]
    lines.append(f"- Địa chỉ bắn: `{meta.get('host', '?')}`")
    lines.append(f"- Thời điểm chạy: {meta.get('started_at', '?')} (commit `{meta.get('git_commit', '?')}`)")
    n_vi = f"{tot['requests']:,}".replace(",", ".")
    lines.append(f"- Tổng số request: {n_vi}, trung bình {tot['rps']:.1f} request/giây")
    lines += ["", "## Mục tiêu so với thực tế", ""]
    lines.append(md_table(["Chỉ số", "Mục tiêu", "Thực tế", "Kết quả"], [
        ["p95 thời gian phản hồi", f"< {TARGET_P95_MS} ms", f"{tot['p95']:.0f} ms",
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
            [svc, f"{p['cpu']:.0f}%", f"{p['cpu_limit']:.0f}%", f"{p['mem'] / 2**20:.0f} MiB",
             f"{p['mem_limit'] / 2**20:.0f} MiB"] for svc, p in peaks.items()]))
    else:
        lines.append("Không có dữ liệu docker stats.")
    found = bottlenecks(peaks)
    lines += ["", "**Điểm nghẽn:** " + ("; ".join(found) if found else
              f"không container nào chạm {BOTTLENECK_RATIO * 100:.0f}% giới hạn CPU hoặc RAM.")]

    if scenario == "ramp":
        steps, broken = ramp_steps(hist)
        lines += ["", "## Điểm gãy (ramp)", ""]
        lines.append(md_table(["Số user", "p95 (ms)", "% lỗi", "RPS"], [
            [s["users"], f"{s['p95']:.0f}", f"{s['err']:.2f}", f"{s['rps']:.1f}"] for s in steps]))
        lines += ["", "**Điểm gãy:** " + (
            f"{broken['users']} user (p95 {broken['p95']:.0f} ms, lỗi {broken['err']:.2f}%)" if broken
            else "chưa gãy ở mức user cao nhất đã thử.")]

    lines += ["", "## Biểu đồ", "", "![Thời gian phản hồi](chart_latency.png)", "",
              "![RPS và lỗi](chart_rps.png)", "", "![CPU container](chart_cpu.png)", ""]
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
        if sc == "ramp":
            _, broken = ramp_steps(hist)
            extra = f"gãy ở {broken['users']} user" if broken else "chưa gãy"
        note = "; ".join(x for x in (", ".join(re.findall(r"\*\*(\w+)\*\*", "; ".join(found))), extra) if x)
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
