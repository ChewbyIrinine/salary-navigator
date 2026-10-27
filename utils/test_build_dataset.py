"""Contract checks for merging independent team exports without accessing Avito."""
import csv
import io
import tempfile
import unittest
from pathlib import Path

from utils.build_dataset import build, digest


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = {"schema_version": "1", "exports": []}

    def row(self, identity="1", **values):
        return dict(id=identity, source_url="https://example.org/" + identity,
                    observed_at="2026-10-21T10:00:00Z", city="Москва",
                    profession="Курьер", title="Курьер", description="Опыт не нужен",
                    salary_raw="100 000 ₽", analysis_status="validated",
                    export_eligibility="included", **values)

    def add(self, run, rows, source="avito"):
        path = self.root / (run + ".csv")
        names = list(dict.fromkeys(k for row in rows for k in row))
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=names)
            writer.writeheader()
            writer.writerows(rows)
        self.registry["exports"].append(dict(run_id=run, source=source,
            path=path.name, sha256=digest(path.read_bytes()),
            placement_regions={"Москва": "Москва"}))
        return path

    def result(self):
        data, _ = build(self.root, self.registry)
        return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))

    def test_one_export_keeps_flags_and_missing_values(self):
        row = self.row(**{"Медкнижка": ""})
        row["export_eligibility"] = "fit_review_required"
        self.add("one", [row])
        result = self.result()[0]
        self.assertEqual(result["Медкнижка"], "")
        self.assertEqual(result["export_eligibility"], "fit_review_required")
        self.assertEqual(result["region"], "Москва")

    def test_multiple_exports_latest_row_new_columns_and_history(self):
        self.add("old", [self.row(**{"Опыт": "3 года"})])
        newer = self.row(**{"Автомобиль": "Да"})
        newer.update(observed_at="2026-10-22T10:00:00Z", salary_raw="120 000 ₽")
        self.add("new", [newer, self.row("2", **{"Автомобиль": ""})])
        rows = self.result()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["salary_raw"], "120 000 ₽")
        self.assertEqual(rows[0]["Опыт"], "")  # No unobserved mix of old and new.
        self.assertEqual(rows[0]["first_seen_at"], "2026-10-21T10:00:00Z")
        self.assertEqual(rows[0]["last_seen_at"], "2026-10-22T10:00:00Z")
        self.assertEqual(rows[0]["run_ids"], "new; old")
        original = build(self.root, self.registry)
        self.registry["exports"].reverse()
        self.assertEqual(build(self.root, self.registry), original)

    def test_identity_includes_source(self):
        self.add("one", [self.row()])
        self.add("two", [self.row()], source="other")
        self.assertEqual(len(self.result()), 2)

    def test_duplicate_within_run_rejected(self):
        self.add("one", [self.row(), self.row()])
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            self.result()

    def test_changed_immutable_input_rejected(self):
        path = self.add("one", [self.row()])
        path.write_text(path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.result()

    def test_same_time_conflict_rejected(self):
        self.add("one", [self.row()])
        row = self.row()
        row["salary_raw"] = "200 000 ₽"
        self.add("two", [row])
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.result()

    def test_identical_repeat_does_not_duplicate(self):
        self.add("one", [self.row()])
        self.add("two", [self.row()])
        self.assertEqual(len(self.result()), 1)

    def test_unreviewed_record_rejected(self):
        row = self.row()
        row["analysis_status"] = "pending"
        self.add("one", [row])
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            self.result()

    def test_enrichment_requires_exact_description_evidence(self):
        row = self.row(enrichment_status="completed", enriched_fields="Опыт",
                       enrichment_version="extract-v1", **{"Опыт": "Без опыта",
                       "Опыт__source": "description", "Опыт__evidence": "Опыт не нужен"})
        self.add("one", [row])
        self.assertEqual(self.result()[0]["enriched_fields"], "Опыт")
        row["Опыт__evidence"] = "придуманная цитата"
        self.registry["exports"].clear()
        self.add("one", [row])
        with self.assertRaisesRegex(ValueError, "quote"):
            self.result()

    def test_geography_never_comes_from_description(self):
        row = self.row()
        row["description"] = "Работа в Туле"
        self.add("one", [row])
        self.assertEqual(self.result()[0]["city"], "Москва")
        self.assertEqual(self.result()[0]["region"], "Москва")
        self.registry["exports"][0]["placement_regions"] = {}
        with self.assertRaisesRegex(ValueError, "region mapping"):
            self.result()

    def test_collection_without_profession_filter_preserves_empty_value(self):
        row = self.row()
        row["profession"] = ""
        row["segments"] = "moscow_all_professions"
        self.add("unfiltered", [row])
        self.assertEqual(self.result()[0]["profession"], "")
        self.assertEqual(self.result()[0]["segments"], "moscow_all_professions")


if __name__ == "__main__":
    unittest.main()
