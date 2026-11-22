"""
One test per bug that was fixed. Run from the project folder with:

    python -m unittest

Everything uses made-up data and temporary files; your own are never touched.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pandas as pd

from finance import (auto_parser, categories, categorizer, compare, exclusions, forecast,
                     processor, storage, subscriptions, valuation)


def tx(day, amount, category, description="x", source="Bank"):
    return (datetime.strptime(day, "%Y-%m-%d"), amount, description, category, source)


def monthly(first, last, amount, category, day=5, description="x"):
    """One transaction a month from "YYYY-MM" to "YYYY-MM"."""
    return [tx(f"{m}-{day:02d}", amount, category, description)
            for m in subscriptions._months_between(first, last)]


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


class FixedToday(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 22)


class StaleData(unittest.TestCase):
    """The data ends on 19 September; the app is opened on 22 November."""

    def setUp(self):
        self.rows = (monthly("2025-01", "2026-08", -650.0, "Rent", description="Flat rent")
                     + monthly("2025-01", "2026-08", -30.0, "Gym", 20, "Some gym")
                     + [tx("2026-09-05", -650.0, "Rent", "Flat rent"),
                        tx("2026-09-19", -5.0, "Dining & Food")])
        from finance import dates
        patcher = mock.patch.object(dates, "date", FixedToday)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_forecast_counts_from_the_last_day_of_data(self):
        f = forecast.summarise(self.rows, 6800.0)
        self.assertEqual(f["as_of"], date(2026, 9, 19))
        self.assertEqual(f["projection"][0]["month"], "2026-10")
        self.assertEqual(f["runway"]["empty_on"], "2027-07")      # 10 months after September

    def test_runway_date_counts_days_not_just_months(self):
        outlook = {"living_mean": 100.0, "investing_mean": 0.0, "living_sd": 0.0}
        self.assertEqual(forecast.runway(60.0, outlook, start=date(2026, 9, 19))["empty_on"],
                         "2026-10")

    def test_partly_imported_month_is_not_complete(self):
        f = forecast.summarise(self.rows, 6800.0)
        self.assertEqual(f["outlook"]["months"][-1], "2026-08")
        self.assertAlmostEqual(f["outlook"]["living_mean"], 680.0)
        self.assertEqual(compare.complete_months(self.rows)[-1], "2026-08")
        self.assertEqual(quiet(processor.process, self.rows)["in_progress_month"], "2026-09")

    def test_month_ending_a_few_days_early_is_complete(self):
        rows = self.rows + [tx("2026-09-27", -5.0, "Dining & Food")]
        self.assertEqual(compare.complete_months(rows)[-1], "2026-09")

    def test_current_month_is_never_complete(self):
        rows = self.rows + [tx("2026-11-21", -5.0, "Dining & Food")]
        self.assertNotIn("2026-11", compare.complete_months(rows))

    def test_subscriptions_are_not_lapsed_by_an_old_import(self):
        found = {s["merchant"]: s["lapsed"] for s in subscriptions.detect_all(self.rows)}
        self.assertFalse(found["Some Gym"])

    def test_subscription_that_really_stopped_is_lapsed(self):
        rows = [r for r in self.rows if r[3] != "Gym" or r[0] < datetime(2026, 5, 1)]
        found = {s["merchant"]: s["lapsed"] for s in subscriptions.detect_all(rows)}
        self.assertTrue(found["Some Gym"])

    def test_partial_last_month_is_not_called_suspect(self):
        rows = self.rows + [tx(f"{m}-{d:02d}", -1.0, "Groceries")
                            for m in subscriptions._months_between("2025-01", "2026-08")
                            for d in range(1, 13)]
        self.assertNotIn("2026-09", [s["month"] for s in exclusions.suspect_months(rows)])


class Totals(unittest.TestCase):
    ROWS = [tx("2026-01-10", 1000.0, "Income"), tx("2026-01-12", -400.0, "Rent"),
            tx("2026-02-10", 1000.0, "Income"), tx("2026-02-12", -400.0, "Rent"),
            tx("2026-02-13", -100.0, "Investments"), tx("2026-03-12", -300.0, "Rent")]

    def test_excluding_a_month_drops_its_income_too(self):
        d = quiet(processor.process, self.ROWS, exclude_months={"2026-02"})
        self.assertEqual((d["total_income"], d["total_spending"], d["net_worth"]),
                         (1000.0, 700.0, 300.0))

    def test_nothing_excluded_keeps_everything(self):
        d = quiet(processor.process, self.ROWS)
        self.assertEqual((d["total_income"], d["total_spending"], d["net_worth"]),
                         (2000.0, 1100.0, 900.0))

    def test_refunds_lower_spending_and_both_net_worths_agree(self):
        rows = self.ROWS + [tx("2026-03-20", 50.0, "Shopping"),
                            tx("2026-03-21", -80.0, "Shopping"),
                            tx("2026-03-22", 10.0, "Investments")]
        d, last = quiet(processor.process, rows), processor.compute_net_worth_timeline(rows)[-1]
        self.assertEqual(d["cat_totals"]["Shopping"], 30.0)
        self.assertAlmostEqual(d["net_worth"], last["net_worth"])
        self.assertAlmostEqual(d["cash_worth"], last["cash"])

    def test_forecast_and_comparison_net_refunds(self):
        rows = monthly("2025-01", "2025-12", -100.0, "Shopping") + \
            monthly("2025-01", "2025-12", 40.0, "Shopping", 9) + [tx("2025-12-31", -1.0, "Other")]
        self.assertEqual(
            next(c for c in forecast.spending_outlook(rows)["by_category"]
                 if c["category"] == "Shopping")["mean"], 60.0)
        shopping = next(c for c in compare.compare(rows)["categories"]
                        if c["category"] == "Shopping")
        self.assertEqual((shopping["before"], shopping["after"]), (60.0, 60.0))


class Valuations(unittest.TestCase):
    ROWS = [tx("2025-01-10", -1000.0, "Investments", "A"),
            tx("2025-01-10", -9000.0, "Investments", "B")]

    def returns(self, snapshot, rows=ROWS):
        with mock.patch.object(valuation, "load_snapshots", lambda: {"2026-01": snapshot}):
            detect = lambda desc, source: (desc, source)
            return (valuation.compute_returns(rows, detect),
                    valuation.value_timeline(rows, detect)[-1])

    def test_unvalued_fund_is_left_out_instead_of_worth_nothing(self):
        res, month = self.returns({"A": 1100.0})
        self.assertEqual(res["totals"]["contributed"], 1000.0)
        self.assertEqual(res["totals"]["gain"], 100.0)
        b = next(f for f in res["funds"] if f["fund"] == "B")
        self.assertFalse(b["valued"])
        self.assertIsNone(b["gain"])
        self.assertIsNone(month["value"])

    def test_complete_valuation_counts_everything(self):
        res, month = self.returns({"A": 1100.0, "B": 9500.0})
        self.assertEqual(res["totals"]["gain"], 600.0)
        self.assertEqual(month["gain"], 600.0)

    def test_sold_fund_needs_no_valuation(self):
        rows = self.ROWS + [tx("2025-06-10", 9100.0, "Investments", "B")]
        res, month = self.returns({"A": 1100.0}, rows)
        self.assertEqual(res["totals"]["gain"], 200.0)
        self.assertEqual(month["gain"], 200.0)


class Saving(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = str(Path(folder.name) / "t.xlsx")
        self.fare = pd.DataFrame([["2026-10-01", -1.5, "Bus", "Other"]],
                                 columns=["Date", "Amount", "Description", "Category"])

    def save(self, *frames, **kwargs):
        return quiet(storage.save_to_global, pd.concat(frames), "Bank", self.file, **kwargs)

    def test_second_identical_purchase_is_kept(self):
        self.assertEqual(self.save(self.fare), 1)
        self.assertEqual(self.save(self.fare, self.fare), 1)
        self.assertEqual(len(quiet(storage.load_from_global, self.file)), 2)

    def test_importing_the_same_file_twice_adds_nothing(self):
        self.assertEqual(self.save(self.fare, self.fare), 2)
        self.assertEqual(self.save(self.fare, self.fare), 0)

    def test_repeat_can_be_forced(self):
        self.save(self.fare)
        self.assertEqual(self.save(self.fare), 0)
        self.assertEqual(self.save(self.fare, allow_repeats=True), 1)

    def test_data_file_does_not_depend_on_the_current_folder(self):
        self.assertTrue(os.path.isabs(storage.GLOBAL_FILE))


class Importing(unittest.TestCase):
    def test_amounts(self):
        for text, value in {
            "1.234,56": 1234.56, "-12,50": -12.5, "2.000,00": 2000.0, "1,234.56": 1234.56,
            "12.345.678,90": 12345678.9, "1.234": 1234.0, "-1.500": -1500.0, "1,234": 1234.0,
            "−12,50": -12.5, "299.99€": 299.99, "-0.5": -0.5, "0.125": 0.125,
            "1234.5": 1234.5, "10": 10.0, "abc": None, "": None,
        }.items():
            self.assertEqual(auto_parser.parse_amount(text), value, text)

    def test_excel_script_keeps_spanish_amounts_and_reports_bad_rows(self):
        from scripts.import_excel import load_existing_excel
        rows = pd.DataFrame([["01/10/2026", "1.234,56", "A", "Other"],
                             ["02/10/2026", "-12,50", "B", "Other"],
                             ["03/10/2026", "2.000,00", "C", "Other"],
                             ["someday", "5,00", "D", "Other"]])
        with tempfile.TemporaryDirectory() as folder:
            for name, write in (("a.xlsx", rows.to_excel), ("a.csv", rows.to_csv)):
                path = str(Path(folder) / name)
                write(path, index=False, header=False)
                df = load_existing_excel(path)
                self.assertEqual(df["Amount"].tolist(), [1234.56, -12.5, 2000.0], name)
                self.assertEqual([r[2] for r in df.attrs["skipped"]], ["D"], name)


class Keywords(unittest.TestCase):
    def test_keywords_match_whole_words(self):
        for text in ("HOTEL ZARAGOZA", "SALSA CLUB", "PARAGUAS MADRID", "NICARAGUA", "AGUACATES"):
            self.assertIsNone(categorizer.find_category(text, {}), text)

    def test_real_merchants_still_match(self):
        for text, category in {
            "ZARA MADRID": "Shopping", "LIDL0123 MADRID": "Groceries", "Amazon.es*AB1": "Shopping",
            "McDonald's": "Dining & Food", "PIZZAS LUIGI": "Dining & Food",
            "Debit to account: N 1 Vivagym": "Gym", "CANAL AGUA": "Water",
            "Cumpleanos de Ana": "Social & Gifts",
        }.items():
            self.assertEqual(categorizer.find_category(text, {}), category, text)

    def test_learned_keywords_match_anywhere(self):
        self.assertEqual(categorizer.find_category("SUPERFOO1", {"foo": "Pet"}), "Pet")


class DamagedSettings(unittest.TestCase):
    def test_defaults_are_used_and_the_damaged_file_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "categories.json"
            path.write_text("{bad")
            with mock.patch.object(categories, "CATEGORIES_FILE", path):
                self.assertIn("Groceries", categories.load_categories())
            self.assertEqual((Path(folder) / "categories.json.corrupt").read_text(), "{bad")
            path.write_text(json.dumps(["Mine"]))
            with mock.patch.object(categories, "CATEGORIES_FILE", path):
                self.assertEqual(categories.load_categories(), ["Mine"])


if __name__ == "__main__":
    unittest.main()
