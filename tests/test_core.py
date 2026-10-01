import os
import tempfile
import unittest
from unittest import mock

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["NEWSAPI_KEY"] = "test-key"
os.environ["ANTHROPIC_API_KEY"] = ""

from app import classifier, config, db, enrich, newsapi_client, pipeline  # noqa: E402


def art(title, desc=""):
    return {"url": f"https://x/{hash(title)}", "title": title, "description": desc, "source": "S"}


class ClassifierTests(unittest.TestCase):
    def check(self, title, desc, event, conf=None):
        r = classifier.classify_rules(art(title, desc))
        self.assertEqual(r["event_type"], event, r)
        if conf:
            self.assertEqual(r["event_confidence"], conf)
        return r

    def test_wealthy_death_is_high(self):
        self.check("Billionaire investor dies at 88", "", "Death", "high")

    def test_ordinary_death_not_flagged_by_death_toll(self):
        self.check("Storm death toll rises to 12", "", None)

    def test_national_debt_not_flagged(self):
        self.check("Congress nears debt ceiling deal", "", None)

    def test_divorce(self):
        self.check("Tech founder files for divorce", "", "Divorce", "high")

    def test_bankruptcy(self):
        self.check("Retail chain files for Chapter 11", "", "Debt & Distress", "medium")

    def test_people_extraction_skips_titles_and_orgs(self):
        r = classifier.classify_rules(art("x", "Co-founder Jane Roe sold shares in Acme Holdings to Park Avenue buyers."))
        self.assertEqual([p["name"] for p in r["people"]], ["Jane Roe"])

    def test_title_case_headline_not_mined_for_names(self):
        r = classifier.classify_rules(art("Billionaire Investor Dies At 88 In New York", None))
        self.assertEqual(r["people"], [])

    def test_tags_always_present(self):
        self.assertEqual(classifier.classify_rules(art("zzz", ""))["tags"], ["Other"])


class ClaudeClassifierTests(unittest.TestCase):
    def test_parses_structured_output_and_falls_back_on_refusal(self):
        import json
        body = {"articles": [{"id": 0, "tags": ["Family & Society"], "event_type": "Death", "confidence": "high",
                              "rationale": "Collector died.", "people": [{"name": "Jane Roe", "role": "deceased"}]},
                             {"id": 1, "tags": ["World"], "event_type": "None", "confidence": "low",
                              "rationale": "n/a", "people": []}]}
        msg = mock.Mock(stop_reason="end_turn", content=[mock.Mock(type="text", text=json.dumps(body))])
        with mock.patch("anthropic.Anthropic") as A:
            A.return_value.beta.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = msg
            out = classifier.ClaudeClassifier().classify([art("Collector dies"), art("Storm")])
            kwargs = A.return_value.beta.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(out[0]["event_type"], "Death")
        self.assertEqual(out[0]["people"][0]["role"], "deceased")
        self.assertIsNone(out[1]["event_type"])
        self.assertEqual(out[1]["classifier"], "claude")

        msg.stop_reason = "refusal"
        with mock.patch("anthropic.Anthropic") as A:
            A.return_value.beta.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = msg
            out = classifier.ClaudeClassifier().classify([art("Collector dies")])
        self.assertEqual(out[0]["classifier"], "rules")


class NewsAPITests(unittest.TestCase):
    def test_normalize_strips_source_suffix_and_removed(self):
        a = newsapi_client.normalize({"title": "Big news - Reuters", "url": "u", "source": {"name": "Reuters"}})
        self.assertEqual(a["title"], "Big news")
        self.assertIsNone(newsapi_client.normalize({"title": "[Removed]", "url": "u"}))

    def test_error_raises(self):
        resp = mock.Mock(status_code=429, headers={"content-type": "application/json"}, text="",
                         json=lambda: {"status": "error", "code": "rateLimited", "message": "too many"})
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaises(newsapi_client.NewsAPIError):
                newsapi_client.fetch("top")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        db.init()
        with db.connect() as c:
            c.execute("DELETE FROM pulls"); c.execute("DELETE FROM articles")

    def test_pull_inserts_and_dedupes(self):
        rows = [art("Heiress inherits estate", "Jane Roe inherits her father's estate."), art("Weather today")]
        with mock.patch.object(newsapi_client, "fetch", return_value=[dict(r) for r in rows]):
            self.assertEqual(pipeline.run_pull()["new"], 2)
            self.assertEqual(pipeline.run_pull()["new"], 0)
        self.assertEqual(db.requests_last_24h(), 2)
        flagged = [a for a in db.list_articles() if a["event_type"]]
        self.assertEqual(flagged[0]["event_type"], "Estate & Succession")

    def test_quota_guard_blocks_request(self):
        with mock.patch.object(config, "DAILY_REQUEST_LIMIT", 1), \
             mock.patch.object(newsapi_client, "fetch", return_value=[]) as f:
            pipeline.run_pull()
            self.assertEqual(pipeline.run_pull()["status"], "quota")
            self.assertEqual(f.call_count, 1)

    def test_alternating_modes(self):
        with mock.patch.object(config, "PULL_MODE", "alternate"), \
             mock.patch.object(newsapi_client, "fetch", return_value=[]) as f:
            pipeline.run_pull(); pipeline.run_pull()
        self.assertEqual([c.args[0] for c in f.call_args_list], ["top", "everything"])


class EnrichTests(unittest.TestCase):
    def setUp(self):
        db.init()

    def test_wikidata_family(self):
        def fake_get(url, **p):
            if p.get("list") == "search":
                return {"query": {"search": [{"title": "Jane Roe"}]}}
            if "summary" in url:
                return {"wikibase_item": "Q1", "extract": "Jane Roe is a collector.", "description": "collector",
                        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Jane_Roe"}}}
            if p.get("ids") == "Q1":
                item = lambda q: {"mainsnak": {"datavalue": {"value": {"id": q}}}}  # noqa: E731
                return {"entities": {"Q1": {"claims": {
                    "P31": [item("Q5")], "P26": [item("Q2")], "P40": [item("Q3")], "P22": [item("Q4")],
                    "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1950-03-04T00:00:00Z"}}}}],
                    "P2218": [{"mainsnak": {"datavalue": {"value": {"amount": "+2500000000", "unit": "http://www.wikidata.org/entity/Q4917"}}}}],
                }}}}
            return {"entities": {q: {"labels": {"en": {"value": f"Person {q}"}}} for q in p["ids"].split("|")}}

        with mock.patch.object(enrich, "_get", side_effect=fake_get):
            p = enrich.profile("Jane Roe", "heir")
        self.assertTrue(p["found"])
        self.assertEqual(p["born"], "1950-03-04")
        self.assertEqual(p["net_worth"], "$2.5B")
        self.assertEqual(p["family"]["spouses"][0]["name"], "Person Q2")
        self.assertEqual(p["family"]["children"][0]["name"], "Person Q3")
        self.assertEqual(p["family"]["parents"][0]["name"], "Person Q4")
        # cached: second call makes no requests
        with mock.patch.object(enrich, "_get", side_effect=AssertionError):
            self.assertEqual(enrich.profile("Jane Roe")["name"], "Jane Roe")

    def test_non_human_rejected(self):
        def fake_get(url, **p):
            if p.get("list") == "search":
                return {"query": {"search": [{"title": "Acme Fund"}]}}
            if "summary" in url:
                return {"wikibase_item": "Q9"}
            return {"entities": {"Q9": {"claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q4830453"}}}}]}}}}
        with mock.patch.object(enrich, "_get", side_effect=fake_get):
            self.assertFalse(enrich.profile("Acme Fund")["found"])


if __name__ == "__main__":
    unittest.main()
