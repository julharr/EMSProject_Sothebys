import unittest

from app import names
from tests.name_fixtures import CASES, HELDOUT


def score(cases):
    tp = fp = fn = 0
    for title, desc, expected in cases:
        got = {p["name"] for p in names.extract(title, desc)}
        tp += len(got & set(expected)); fp += len(got - set(expected)); fn += len(set(expected) - got)
    return tp / max(1, tp + fp), tp / max(1, tp + fn)


class NameExtractionTests(unittest.TestCase):
    def test_development_set_exact(self):
        for title, desc, expected in CASES:
            with self.subTest(title=title):
                self.assertEqual([p["name"] for p in names.extract(title, desc)], expected)

    def test_heldout_set_quality(self):
        precision, recall = score(HELDOUT)
        self.assertGreaterEqual(precision, 0.95)
        self.assertGreaterEqual(recall, 0.9)

    def test_roles(self):
        roles = {p["name"]: p["role"] for p in names.extract(
            "Heirs of Constance Merriweather battle over estate",
            "Her nephew, James Hale, and her late husband Henri Merriweather's executor disagree.")}
        self.assertIsNone(roles["Constance Merriweather"])   # "heirs of X" does not make X an heir
        self.assertEqual(roles["James Hale"], "nephew")

    def test_companies_and_places_are_not_people(self):
        got = names.extract("Goldman Sachs and Morgan Stanley slip; Palm Beach Mansion Sells",
                            "Shares of Whitmore Capital and Aldana Resorts fell on the New York Stock Exchange.")
        self.assertEqual(got, [])

    def test_principal_from_headline_comes_first(self):
        got = names.extract("Billionaire Investor Harold Whitmore Dies At 94",
                            "His son, Peter Whitmore, said Whitmore died at home.")
        self.assertEqual(got[0]["name"], "Harold Whitmore")


if __name__ == "__main__":
    unittest.main()
