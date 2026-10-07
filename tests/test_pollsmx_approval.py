import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from approval.pollsmx import export_web, ingest, weekly_payload


def fixture(run=1, retrieved="2026-10-07T12:00:00+00:00"):
    series = []
    for name, metric, value in (("Aprobación", "aprobacion", 70), ("Desaprobación", "desaprobacion", 30)):
        for kind in ("line", "arearange", "scatter"):
            point = {"fecha": "2026-10-06", "estimacion": value, "y": value,
                     "x": 1791266400000, "partido_alianza": metric, "id_corrida": str(run)}
            if kind == "arearange":
                point.update(low=value - 5, high=value + 5)
            if kind == "scatter":
                point["id_fuente"] = "38"
            series.append({"name": name, "type": kind, "data": [point]})
    # Identical scatter points must survive: API does not expose a poll ID.
    series[2]["data"].append(copy.deepcopy(series[2]["data"][0]))
    return {"schemaVersion": 1, "charts": [{"president_key": "00", "retrieved_at": retrieved,
        "api_url": "https://example.org/chart", "payload": {"data": {
            "source": {"code": "approval"}, "scope": {"key": "00", "label": "Claudia Sheinbaum Pardo"},
            "status": "active", "run_id": run, "external_run_id": str(run), "series": series}}}]}


class PollsMXTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.db"
        self.snapshot = Path(self.temp.name) / "snapshot.json"

    def count(self, table):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_idempotent_history_export_and_duplicate_observations(self):
        self.assertEqual(ingest(fixture(), self.db, self.snapshot)["changed"], 1)
        self.assertEqual(ingest(fixture(), self.db, self.snapshot)["unchanged"], 1)
        self.assertEqual(self.count("fact_approval_pollsmx_point"), 7)
        ingest(fixture(2, "2026-10-08T12:00:00+00:00"), self.db, self.snapshot)
        self.assertEqual(self.count("fact_approval_pollsmx_point"), 14)
        self.assertEqual(self.count("view_approval_pollsmx_current"), 7)
        output = Path(self.temp.name) / "web.json"
        export_web(self.db, output)
        result = json.loads(output.read_text())
        self.assertEqual(len(result["points"]), 7)
        self.assertEqual(result["presidents"][0]["president"], "Sheinbaum")
        self.assertEqual({r["run_id"] for r in result["points"]}, {2})
        with self.assertRaisesRegex(ValueError, "older"):
            ingest(fixture(), self.db, self.snapshot)

    def test_bad_response_leaves_existing_data_intact(self):
        ingest(fixture(), self.db, self.snapshot)
        bad = fixture(2)
        bad["charts"][0]["payload"]["data"]["series"][1]["data"][0]["high"] = 101
        with self.assertRaises(ValueError):
            ingest(bad, self.db, self.snapshot)
        self.assertEqual(self.count("dim_approval_pollsmx_run"), 1)

    def test_revised_run_replaces_only_its_points(self):
        ingest(fixture(), self.db, self.snapshot)
        revised = fixture()
        revised["charts"][0]["payload"]["data"]["series"][2]["data"].pop()
        self.assertEqual(ingest(revised, self.db, self.snapshot)["changed"], 1)
        self.assertEqual(self.count("fact_approval_pollsmx_point"), 6)

    def test_transaction_rolls_back_stale_snapshot(self):
        ingest(fixture(1, "2026-10-08T12:00:00+00:00"), self.db, self.snapshot)
        bundle = fixture(2)
        other = copy.deepcopy(bundle["charts"][0])
        other["president_key"] = "006"
        other["payload"]["data"]["scope"] = {"key": "006", "label": "AMLO"}
        bundle["charts"].insert(0, other)
        with self.assertRaises(ValueError):
            ingest(bundle, self.db, self.snapshot)
        self.assertEqual(self.count("dim_approval_pollsmx_run"), 1)
        self.assertEqual(self.count("dim_approval_pollsmx_president"), 1)

    def test_missing_or_inconsistent_uncertainty_fails(self):
        for change in ("missing", "inconsistent"):
            bundle = fixture()
            series = bundle["charts"][0]["payload"]["data"]["series"]
            if change == "missing":
                series.pop(1)
            else:
                series[1]["data"][0]["fecha"] = "2026-10-05"
            with self.assertRaises(ValueError):
                ingest(bundle, self.db, self.snapshot)

    def test_weekly_export_keeps_last_day_and_partial_week(self):
        presidents = [{"president_key": "00", "president": "Sheinbaum", "term_start": "2024-10-01"}]
        points = [{"president_key": "00", "run_id": 1, "metric": "aprobacion",
                   "series_type": "line", "observation_date": day, "estimate": value}
                  for day, value in [("2024-09-30", 80), ("2024-10-01", 70),
                                     ("2024-10-06", 71), ("2024-10-07", 72), ("2024-10-08", 73)]]
        points.append({**points[-1], "series_type": "scatter", "estimate": 99})
        points.append({**points[-2], "metric": "desaprobacion", "estimate": 27})
        result = weekly_payload(presidents, list(reversed(points)))
        self.assertEqual([p["date"] for p in result["points"]], ["2024-10-06", "2024-10-08"])
        self.assertEqual([p["approve"] for p in result["points"]], [71, 73])
        self.assertEqual([p["weekStart"] for p in result["points"]], ["2024-09-30", "2024-10-07"])
        self.assertAlmostEqual(result["points"][0]["month"], 5 / 31, places=6)


if __name__ == "__main__":
    unittest.main()
