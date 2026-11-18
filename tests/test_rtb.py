import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("NEWSAPI_KEY", "test-key")

from app import db, live, rtb  # noqa: E402

INDEX = {
    "a-jayson-adair": {"name": "A. Jayson Adair", "rank": 2826, "networth": 1384.095, "update": "2026-08-31"},
    "abdulla-al-futtaim": {"name": "Abdulla Al Futtaim & family", "rank": 903, "networth": 4743.176, "update": "2026-08-31"},
    "old-timer": {"name": "Olga Old-Timer", "rank": 2000, "networth": 1100, "update": "2021-06-29"},
    "renee-dupont": {"name": "Renée Dupont", "rank": 50, "networth": 30000, "update": "2026-08-31"},
}
INFO = {"uri": "renee-dupont", "name": "Renée Dupont", "birthDate": "1960-02-01", "family": False,
        "residence": {"country": "fr", "state": None, "city": "Paris"}, "source": ["Luxury goods"],
        "deceased": False, "children": 3, "maritalStatus": "Married",
        "organization": {"name": "Maison Dupont", "title": "Chair"}, "selfMade": {"_is": True}}


def fake_get(path):
    if path == "profile/_index":
        return INDEX
    if path == "profile/renee-dupont/info":
        return INFO
    raise AssertionError(path)


class RtbTests(unittest.TestCase):
    def setUp(self):
        db.init()
        with mock.patch.object(rtb, "_get", side_effect=fake_get):
            rtb.refresh_index(force=True)

    def test_matching(self):
        self.assertEqual(rtb.match("Abdulla Al Futtaim")["uri"], "abdulla-al-futtaim")    # "& family" dropped
        self.assertEqual(rtb.match("Renee Dupont")["uri"], "renee-dupont")                 # accents ignored
        self.assertEqual(rtb.match("A Adair")["uri"], "a-jayson-adair")                    # first + last fallback
        self.assertIsNone(rtb.match("Dupont"))                                             # one word never matches
        self.assertIsNone(rtb.match("Jane Nobody"))

    def test_index_is_fetched_once_a_day(self):
        with mock.patch.object(rtb, "_get", side_effect=AssertionError("no second download")):
            self.assertEqual(rtb.refresh_index(), 4)

    def test_profile_uses_forbes_worth_and_facts(self):
        with mock.patch.object(rtb, "_get", side_effect=fake_get):
            rtb.lookup("Renée Dupont")
        with mock.patch.object(rtb, "_get", side_effect=AssertionError("cached")):
            prof = live.build_tags("Renée Dupont", {"found": True, "net_worth": "$12B (2019)"}, {"credits": []}, {"credits": []}, [], sec=[])
        self.assertEqual(prof["worth"], 30000e6)
        self.assertEqual(prof["worthSource"], "forbes")
        labels = [t[1] for t in prof["tags"] if t[0] in ("wealth", "aff")]
        self.assertIn("Forbes billionaire #50 (Aug 2026)", labels)
        self.assertIn("Self-made (Forbes)", labels)
        self.assertIn("Chair, Maison Dupont", labels)
        facts = dict(prof["bio_facts"])
        self.assertEqual(facts["Lives in"], "Paris, FR")
        self.assertNotIn("Net worth", facts)            # the older Wikidata figure is not shown next to Forbes

    def test_dropped_off_entry_is_not_used_for_worth(self):
        prof = live.build_tags("Olga Old-Timer", {"found": False}, {"credits": []}, {"credits": []}, [], sec=[],
                               forbes={**rtb.match("Olga Old-Timer"), "info": {}})
        self.assertIsNone(prof["worth"])
        self.assertIn("Last on the Forbes list 29 Jun 2021", prof["find"]["forbes"][0])


if __name__ == "__main__":
    unittest.main()
