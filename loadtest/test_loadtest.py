"""Offline tests with fake data (no stack needed): python3 -m unittest loadtest/test_loadtest.py
The BreakGuard test needs locust, so it runs inside the locust image and is skipped elsewhere."""
import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reconcile  # noqa: E402


def write(key, kind="attendance", status=201, attempts=1):
    return {"key": key, "kind": kind, "final_status": status, "attempts": attempts, "id": 1}


def make_dir(writes, db_rows, has_key=True):
    d = Path(tempfile.mkdtemp())
    (d / "writes.jsonl").write_text("\n".join(json.dumps(w) for w in writes) + "\n")
    with open(d / "db_keys.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "key", "id", "created_at"])
        w.writerows(db_rows)
        w.writerow(["meta:request_has_key", str(has_key).lower(), "", ""])
    (d / "report.md").write_text("# Báo cáo\n")
    return d


class ReconcileTest(unittest.TestCase):
    def test_detects_confirmed_but_missing_key(self):
        writes = [write("k1"), write("k2"), write("k3"), write("k4", status=503, attempts=4),
                  write("k5", status=0, attempts=4), write("r1", "request", 201)]
        db = [["attendance", "k1", 1, ""], ["attendance", "k2", 2, ""],
              ["attendance", "k5", 3, ""],  # failed for the client but committed
              ["request", "r1", 4, ""]]     # k3 is confirmed but missing on purpose
        d = make_dir(writes, db)
        self.assertEqual(reconcile.reconcile(d), 1)
        first = reconcile.analyse(writes, *reconcile.read_db(d / "db_keys.csv"))
        self.assertEqual([w["key"] for w in first["lost"]], ["k3"])
        self.assertEqual(first["confirmed"], 4)
        self.assertEqual(first["failed"], 2)
        self.assertEqual([w["key"] for w in first["failed_in_db"]], ["k5"])
        self.assertEqual(first["retried"], 2)
        text = (d / "report.md").read_text()
        self.assertIn("**KHÔNG ĐẠT**", text)
        self.assertEqual(text.count(reconcile.SECTION), 1)
        reconcile.reconcile(d)  # idempotent: the section is replaced, not duplicated
        self.assertEqual((d / "report.md").read_text().count(reconcile.SECTION), 1)

    def test_clean_run_and_duplicates(self):
        writes = [write("a"), write("b")]
        d = make_dir(writes, [["attendance", "a", 1, ""], ["attendance", "b", 2, ""]])
        self.assertEqual(reconcile.reconcile(d), 0)
        d = make_dir(writes, [["attendance", "a", 1, ""], ["attendance", "a", 2, ""], ["attendance", "b", 3, ""]])
        self.assertEqual(reconcile.reconcile(d), 1)
        first = reconcile.analyse(writes, *reconcile.read_db(d / "db_keys.csv"))
        self.assertEqual(first["duplicates"], [("attendance", "a")])

    def test_requests_without_key_column_are_not_counted_lost(self):
        writes = [write("r1", "request", 201)]
        d = make_dir(writes, [["request", "", 1, ""]], has_key=False)
        self.assertEqual(reconcile.reconcile(d), 0)
        self.assertIn("Không đối soát được", (d / "reconcile.md").read_text())

    def test_retry_section(self):
        writes = [write("a"), write("b", status=503, attempts=4)]
        d = make_dir(writes, [["attendance", "a", 1, ""]])
        (d / "writes_retry.jsonl").write_text(json.dumps(
            {"key": "b", "kind": "attendance", "final_status": 201, "id": 2}) + "\n")
        with open(d / "db_keys_after_retry.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["kind", "key", "id", "created_at"])
            w.writerows([["attendance", "a", 1, ""], ["attendance", "b", 2, ""]])
        self.assertEqual(reconcile.reconcile(d), 0)
        self.assertIn("Sau vòng retry đối soát", (d / "reconcile.md").read_text())


class BreakGuardTest(unittest.TestCase):
    def test_break_conditions(self):
        try:
            import shapes
        except ImportError:
            self.skipTest("locust not installed")
        os.environ.update(RESULT_DIR=tempfile.mkdtemp(), TARGET_P95_MS="500", BREAK_FACTOR="2",
                          BREAK_ERR_PCT="5", BREAK_HOLD_SECONDS="30")
        guard = shapes.BreakGuard()

        def total(reqs, fails, p95):
            return SimpleNamespace(num_requests=reqs, num_failures=fails,
                                   get_current_response_time_percentile=lambda _: p95)

        guard.begin_step(total(100, 0, 0))
        self.assertIsNone(guard.evaluate(total(1100, 10, 900), 0, 50, 60))   # p95 900 <= 1000, err 1%
        self.assertIsNotNone(guard.evaluate(total(1100, 10, 1200), 0, 50, 60))  # p95 > 2 x target
        self.assertIsNotNone(guard.evaluate(total(1100, 100, 100), 0, 50, 60))  # err ~10%
        info = json.loads((Path(os.environ["RESULT_DIR"]) / "break.json").read_text())
        self.assertEqual(info["users"], 50)


if __name__ == "__main__":
    unittest.main()
