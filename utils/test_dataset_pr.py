import unittest

from utils.dataset_pr import browser_evidence, render


class PublicationTests(unittest.TestCase):
    def test_new_and_repeated_observations_and_new_columns(self):
        before = (["source", "id"], [{"source": "avito", "id": "1"}])
        after = (["source", "id", "Цех"], [
            {"source": "avito", "id": "1", "Цех": ""},
            {"source": "avito", "id": "2", "Цех": "Горячий"}])
        result = render("Пять вакансий вахты.", before, after, [
            ("avito", [{"id": "1"}, {"id": "2"}])])
        self.assertIn("Новые ключи: 1; повторные наблюдения: 1", result)
        self.assertIn("1 → 2 строк; 2 → 3 колонок", result)
        self.assertIn("Новые характеристики: Цех", result)

    def test_missing_batch_rows_and_removed_keys_rejected(self):
        before = (["source", "id"], [{"source": "avito", "id": "1"}])
        with self.assertRaisesRegex(ValueError, "removes"):
            render("Сбор", before, (before[0], []), [("avito", [{"id": "2"}])])
        with self.assertRaisesRegex(ValueError, "absent"):
            render("Сбор", before, before, [("avito", [{"id": "2"}])])

    def test_browser_dumps_rejected_but_structured_data_allowed(self):
        for path in ["Data/avito_runs/run/evidence/a.jpg",
                     "Data/avito_runs/run/evidence/filter.txt",
                     "Data/avito_runs/run/1.snapshot.txt",
                     "Data/avito_runs/run/1.png",
                     "Data/avito_runs/run/snapshots/a.html"]:
            self.assertTrue(browser_evidence(path))
        for path in ["Data/avito_runs/run/raw/1.json",
                     "Data/avito_runs/run/evidence/queue.json",
                     "Data/avito_runs/run/exports/vacancies_flat.csv"]:
            self.assertFalse(browser_evidence(path))


if __name__ == "__main__":
    unittest.main()
