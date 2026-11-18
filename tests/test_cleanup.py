import os
import tempfile
import unittest

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("NEWSAPI_KEY", "test-key")

from app import db, live  # noqa: E402
from app.classifier import classify_rules  # noqa: E402
from app.enrich import name_key  # noqa: E402


class NameMergeTests(unittest.TestCase):
    def test_variants_merge_to_the_common_form(self):
        c = live.canonical_names(["Jensen Huang", "Inside Jensen Huang", "Jensen Huang", "Jensen H. Huang", "Lady Fiona Ashcombe",
                                  "Fiona Ashcombe", "Henri Devereaux", "Margaux Devereaux", "Adaeze Okafor-Reyes", "Daniel Reyes"])
        self.assertEqual(c["Inside Jensen Huang"], "Jensen Huang")
        self.assertEqual(c["Jensen H. Huang"], "Jensen Huang")
        self.assertEqual(c["Lady Fiona Ashcombe"], "Fiona Ashcombe")
        self.assertEqual(c["Henri Devereaux"], "Henri Devereaux")        # same surname, different people
        self.assertEqual(c["Daniel Reyes"], "Daniel Reyes")

    def test_wikipedia_non_person_is_dropped(self):
        db.init()
        db.put_person(name_key("Cash App"), {"name": "Cash App", "found": False, "not_person": True, "what": "mobile payment service"})
        self.assertFalse(live._is_person({"name": "Cash App"}))
        self.assertTrue(live._is_person({"name": "Jack Dorsey"}))


class BuySignalTests(unittest.TestCase):
    def check(self, title, desc, label):
        r = classify_rules({"title": title, "description": desc})
        self.assertEqual(r["event_type"], "Liquidity Event", title)
        self.assertIn(r["event_confidence"], ("high", "medium"), title)
        self.assertEqual(live.buy_event(f"{title} {desc}"), label, title)

    def test_new_buy_patterns(self):
        self.check("Nvidia CEO Jensen Huang sells $300 million of shares", "Huang has sold shares under a trading plan.", "Share sale")
        self.check("Figma co-founder Dylan Field becomes a billionaire in market debut", "The company went public on the NYSE.", "IPO")
        self.check("Hedge fund billionaire Ken Griffin buys $45 million Palm Beach mansion", "Griffin, the founder of Citadel, bought the estate.", "New property")
        self.check("Scale AI founder Alexandr Wang raises $1 billion at $14 billion valuation", "The funding round was led by Accel.", "Funding round")
        self.check("Founder Priya Natarajan sells startup to Google for $2 billion", "She will join Google.", "Company sale")

    def test_opinion_story_is_not_a_buy_signal(self):
        r = classify_rules({"title": "Billionaire Mark Cuban says tariffs will hurt consumers", "description": "Cuban criticized the policy."})
        self.assertIsNone(r["event_type"])


if __name__ == "__main__":
    unittest.main()
