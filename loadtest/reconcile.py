"""Generic part of the load test: reconcile what clients were told with what the DB holds.

Usage:
    reconcile.py <result_dir>           # writes reconcile.md and adds a section to report.md
    reconcile.py --retry <result_dir>   # re-send once every write whose final status was an error

Inputs in <result_dir> (stdlib only, no pandas):
    writes.jsonl             one line per write: key, kind, final_status, attempts, id, ...
                             (failed writes also carry path/body/key_in_header for --retry)
    db_keys.csv              kind,key,id,created_at from the DB (app_reconcile.sql)
    writes_retry.jsonl       result of --retry (optional)
    db_keys_after_retry.csv  DB keys after --retry (optional)
A CSV row of kind "meta:<kind>_has_key" with key "false" means the DB cannot tell
that kind of write apart yet, so it is reported as "not verifiable" instead of lost.
Exit code 1 when a confirmed write is missing from the DB or a key is duplicated.
"""
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SECTION = "## Đối soát ghi (không mất, không trùng)"


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # a torn last line after a hard stop
    return rows


def read_db(path):
    """-> (Counter of (kind, key) in the DB, set of kinds the DB cannot verify)."""
    keys, unverifiable = Counter(), set()
    path = Path(path)
    if not path.exists():
        return keys, unverifiable
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            kind = row["kind"]
            if kind.startswith("meta:") and kind.endswith("_has_key"):
                if row["key"].strip().lower() in ("f", "false"):
                    unverifiable.add(kind[len("meta:"):-len("_has_key")])
            elif row["key"]:
                keys[(kind, row["key"])] += 1
    return keys, unverifiable


def read_owners(path):
    """(kind, key) -> owner, when the DB export has an optional `owner` column (e.g. employee code)."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["kind"], r["key"]): r["owner"] for r in csv.DictReader(f)
                if r.get("owner") and r["key"] and not r["kind"].startswith("meta:")}


def is_ok(status):
    return 200 <= int(status) < 300


def analyse(writes, db, unverifiable, owners=None):
    """Compare confirmed / failed writes with the DB keys. `writes` has one row per key.

    With `owners`, a confirmed write whose DB row belongs to someone other than the user who sent
    it (the `code` in writes.jsonl) counts as written to the wrong person."""
    out = {"total": len(writes), "by_kind": dict(Counter(w["kind"] for w in writes)),
           "unverifiable_kinds": sorted(unverifiable)}
    confirmed = [w for w in writes if is_ok(w["final_status"])]
    failed = [w for w in writes if not is_ok(w["final_status"])]
    verif = [w for w in confirmed if w["kind"] not in unverifiable]
    out["confirmed"] = len(confirmed)
    out["lost"] = [w for w in verif if (w["kind"], w["key"]) not in db]
    out["unverifiable_confirmed"] = len(confirmed) - len(verif)
    out["duplicates"] = sorted(k for k, n in db.items() if n > 1 and k[0] not in unverifiable)
    out["failed"] = len(failed)
    out["failed_in_db"] = [w for w in failed if w["kind"] not in unverifiable and (w["kind"], w["key"]) in db]
    out["failed_unverifiable"] = len([w for w in failed if w["kind"] in unverifiable])
    known = {(w["kind"], w["key"]) for w in writes}
    out["db_unknown"] = len([k for k in db if k not in known and k[0] not in unverifiable])
    out["retried"] = len([w for w in writes if w.get("attempts", 1) > 1])
    owners = owners or {}
    checked = [w for w in verif if (w["kind"], w["key"]) in owners and w.get("code")]
    out["owner_checked"] = len(checked)
    out["wrong_owner"] = [w for w in checked if owners[(w["kind"], w["key"])] != w["code"]]
    return out


def merge_retry(writes, retry_rows):
    """Writes with the final status of the reconcile re-send applied."""
    final = {r["key"]: r for r in retry_rows}
    merged = []
    for w in writes:
        r = final.get(w["key"])
        merged.append({**w, "final_status": r["final_status"], "id": r.get("id")} if r else w)
    return merged


def bold_if(ok, text_ok="ĐẠT", text_bad="KHÔNG ĐẠT"):
    return text_ok if ok else f"**{text_bad}**"


def render(first, second=None, retry_rows=None):
    kinds = ", ".join(f"{k}: {n}" for k, n in sorted(first["by_kind"].items())) or "-"
    lines = [SECTION, "",
             "| Chỉ số | Số lượng | Kết quả |", "|---|---|---|",
             f"| Tổng lệnh ghi đã gửi ({kinds}) | {first['total']} | |",
             f"| Được xác nhận (2xx) | {first['confirmed']} | |",
             f"| **Đã xác nhận nhưng không có trong DB** | {len(first['lost'])} | "
             f"{bold_if(not first['lost'])} |",
             f"| Key bị trùng trong DB | {len(first['duplicates'])} | {bold_if(not first['duplicates'])} |",
             *([f"| Ghi nhầm người (trong {first['owner_checked']} lệnh có kiểm người gửi) | "
                f"{len(first['wrong_owner'])} | {bold_if(not first['wrong_owner'])} |"] if first["owner_checked"] else []),
             f"| Lỗi sau khi hết retry | {first['failed']} | |",
             f"| Trong số lỗi, thực ra đã vào DB (commit nhưng client không nhận được trả lời) | "
             f"{len(first['failed_in_db'])} | |",
             f"| Lệnh phải retry ít nhất một lần | {first['retried']} | |",
             f"| Key trong DB không có trong writes.jsonl (thường là lệnh đang bay lúc test dừng, hoặc traffic khác) | {first['db_unknown']} | |"]
    if first["unverifiable_kinds"]:
        lines += ["", f"Không đối soát được loại `{', '.join(first['unverifiable_kinds'])}`: DB chưa có cột "
                  f"idempotency_key, nên {first['unverifiable_confirmed']} lệnh đã xác nhận và "
                  f"{first['failed_unverifiable']} lệnh lỗi của loại này chỉ được đếm, không kiểm chứng được."]
    if first["lost"]:
        sample = ", ".join(w["key"] for w in first["lost"][:5])
        lines += ["", f"Key đã xác nhận nhưng mất (tối đa 5): {sample}"]
    if second is not None:
        ok_n = len([r for r in retry_rows if is_ok(r["final_status"])])
        lines += ["", "### Sau vòng retry đối soát", "",
                  "| Chỉ số | Số lượng | Kết quả |", "|---|---|---|",
                  f"| Key lỗi được gửi lại một lần | {len(retry_rows)} | |",
                  f"| Gửi lại thành công (2xx) | {ok_n} | |",
                  f"| Vẫn lỗi | {second['failed']} | |",
                  f"| Đã xác nhận nhưng không có trong DB | {len(second['lost'])} | "
                  f"{bold_if(not second['lost'])} |",
                  f"| Key bị trùng trong DB | {len(second['duplicates'])} | "
                  f"{bold_if(not second['duplicates'])} |"]
    elif first["failed"] and retry_rows is None:
        lines += ["", "Chưa chạy vòng retry đối soát (RECONCILE_RETRY=0 hoặc chưa có kết quả)."]
    return "\n".join(lines) + "\n"


def write_report(d, text):
    (d / "reconcile.md").write_text(text, encoding="utf-8")
    report = d / "report.md"
    if report.exists():
        body = report.read_text(encoding="utf-8")
        if SECTION in body:
            body = body[:body.index(SECTION)].rstrip("\n") + "\n"
        report.write_text(body + "\n" + text, encoding="utf-8")


def reconcile(d):
    d = Path(d)
    writes = read_jsonl(d / "writes.jsonl")
    db, unverifiable = read_db(d / "db_keys.csv")
    first = analyse(writes, db, unverifiable, read_owners(d / "db_keys.csv"))
    second = retry_rows = None
    retry_rows = read_jsonl(d / "writes_retry.jsonl") or None
    if retry_rows is not None and (d / "db_keys_after_retry.csv").exists():
        db2, unverifiable2 = read_db(d / "db_keys_after_retry.csv")
        second = analyse(merge_retry(writes, retry_rows), db2, unverifiable2)
    write_report(d, render(first, second, retry_rows))
    bad = len(first["lost"]) + len(first["duplicates"]) + len(first["wrong_owner"])
    if second is not None:
        bad += len(second["lost"]) + len(second["duplicates"])
    print(f"Reconcile: {first['total']} writes, {first['confirmed']} confirmed, "
          f"{len(first['lost'])} confirmed-but-missing, {len(first['duplicates'])} duplicate keys, "
          f"{first['failed']} failed ({len(first['failed_in_db'])} of them in DB)")
    return 1 if bad else 0


# ---------- --retry ----------

def http(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def retry_failed(d):
    d = Path(d)
    host = os.environ.get("RECONCILE_HOST") or os.environ.get("TARGET_HOST") or "http://api:8000"
    password = os.environ.get("LOADTEST_PASSWORD", "")
    failed = [w for w in read_jsonl(d / "writes.jsonl")
              if not is_ok(w["final_status"]) and w.get("path")]
    tokens = {}
    for code in sorted({w["code"] for w in failed}):
        status, data = http("POST", f"{host}/api/auth/login", {"code": code, "password": password})
        if status == 200 and data:
            tokens[code] = data["access_token"]

    def resend(w):
        token = tokens.get(w["code"])
        if not token:
            return {"key": w["key"], "kind": w["kind"], "final_status": 0, "id": None}
        headers = {"Authorization": f"Bearer {token}"}
        if w.get("key_in_header"):
            headers["Idempotency-Key"] = w["key"]
        status, data = http("POST", host + w["path"], w["body"], headers)
        rid = (data or {}).get("event_id", (data or {}).get("id")) if is_ok(status) else None
        return {"key": w["key"], "kind": w["kind"], "final_status": status, "id": rid}

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(resend, failed))
    with open(d / "writes_retry.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Reconcile retry: re-sent {len(results)} failed writes, "
          f"{len([r for r in results if is_ok(r['final_status'])])} succeeded")
    return 0


def main(argv):
    if len(argv) == 3 and argv[1] == "--retry":
        return retry_failed(argv[2])
    if len(argv) == 2 and not argv[1].startswith("-"):
        return reconcile(argv[1])
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
