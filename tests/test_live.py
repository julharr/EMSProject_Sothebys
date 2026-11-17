import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("NEWSAPI_KEY", "test-key")

from app import config, db, live, museums, newsapi_client, pipeline  # noqa: E402


class FreeOnlyTests(unittest.TestCase):
    def test_claude_off_by_default_even_with_key(self):
        self.assertTrue(config.FREE_APIS_ONLY)
        self.assertFalse(config.USE_CLAUDE)


class MuseumTests(unittest.TestCase):
    def setUp(self):
        db.init()

    def test_met_keeps_only_credit_lines_naming_the_person(self):
        def fake_get(url, **p):
            if "search" in url:
                return {"objectIDs": [1, 2]}
            if url.endswith("/1"):
                return {"title": "Vase", "department": "Asian Art", "creditLine": "Gift of Jane and John Roe, 2019", "objectURL": "https://x/1"}
            return {"title": "Chair", "department": "The American Wing", "creditLine": "Gift of Someone Else", "objectURL": "https://x/2"}
        with mock.patch.object(museums, "_get", side_effect=fake_get):
            got = museums.met("Jane Roe")["credits"]
        self.assertEqual([c["title"] for c in got], ["Vase"])
        self.assertEqual(got[0]["kind"], "donor")

    def test_cleveland_lender_and_department_mapping(self):
        with mock.patch.object(museums, "_get", return_value={"data": [
                {"title": "Portrait", "department": "European Painting and Sculpture", "creditline": "Lent by Ann Lee", "url": "u"}]}):
            got = museums.cleveland("Ann Lee")["credits"]
        self.assertEqual(got[0]["kind"], "lender")
        self.assertEqual(museums.category_for(got[0]["department"]), "oldmasters")
        self.assertEqual(museums.category_for("Modern and Contemporary Art"), "contemporary")


class LiveTests(unittest.TestCase):
    def setUp(self):
        db.init()
        with db.connect() as c:
            c.execute("DELETE FROM pulls"); c.execute("DELETE FROM articles"); c.execute("DELETE FROM people")

    def test_tags_from_free_sources(self):
        wiki = {"found": True, "bio": "Jane Roe is a philanthropist and art collector known for her Impressionist paintings.",
                "net_worth": "$2.5B (2024)", "occupations": ["investor"], "wikipedia_url": "https://en.wikipedia.org/wiki/Jane_Roe"}
        met = {"credits": [{"museum": "The Met", "department": "Asian Art", "credit": "Gift of Jane Roe", "kind": "donor"}]}
        prof = live.build_tags("Jane Roe", wiki, met, {"credits": []}, ["Jane Roe sells her stake in Halcyon"])
        cats = {t[0]: t[1] for t in prof["tags"] if t[0] not in ("wealth", "aff")}
        markers = [t[1] for t in prof["tags"] if t[0] in ("wealth", "aff")]
        self.assertEqual(prof["worth"], 2.5e9)
        self.assertIn("met", cats["asian"])
        self.assertIn("wikipedia", cats["impmod"])
        self.assertIn("Museum donor (The Met)", markers)
        self.assertIn("Philanthropist", markers)
        self.assertIn("Recent liquidity: Share sale", markers)

    def test_unknown_worth_and_billionaire_fallback(self):
        none = live.build_tags("A B", {"found": False}, {"credits": []}, {"credits": []}, ["A B buys a home"])
        self.assertIsNone(none["worth"])
        bil = live.build_tags("A B", {"found": False}, {"credits": []}, {"credits": []}, ["A B becomes a billionaire after IPO"])
        self.assertEqual(bil["worth"], 1e9)

    def test_stories_split_and_pending_profiles(self):
        rows = [
            {"url": "https://x/1", "title": "Billionaire collector dies at 90", "description": "Jane Roe, a billionaire art collector, died. Her jewelry will be sold.", "source": "S"},
            {"url": "https://x/2", "title": "Startup IPO makes founder rich", "description": "Co-founder Ann Lee holds a stake after the IPO.", "source": "S"},
        ]
        with mock.patch.object(newsapi_client, "fetch", return_value=[dict(r) for r in rows]):
            pipeline.run_pull(mode="everything")
        stories, people = live.stories_and_people()
        by_person = {s["person"]: s for s in stories}
        self.assertEqual(by_person["p:jane roe"]["signal"], "consign")
        self.assertEqual(by_person["p:jane roe"]["event"], "")          # no Death/Divorce/Debt label
        self.assertIn("Jewelry", by_person["p:jane roe"]["item"])
        self.assertEqual(by_person["p:ann lee"]["signal"], "buy")
        self.assertEqual(by_person["p:ann lee"]["event"], "IPO")
        self.assertTrue(people["p:ann lee"]["pending"])                    # not looked up yet

    def test_item_and_event_helpers(self):
        self.assertEqual(live.items_in("Heirs battle over $4 billion estate"), "Not named in coverage")
        self.assertEqual(live.buy_event("Corvane agrees to buy Lumen for $4B"), "Company sale")
        self.assertEqual(live.parse_worth("$450M"), 4.5e8)


if __name__ == "__main__":
    unittest.main()
