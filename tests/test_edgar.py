import os
import tempfile
import unittest
from datetime import date
from unittest import mock

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("NEWSAPI_KEY", "test-key")

from app import config, db, edgar, live  # noqa: E402

FORM4 = """<SEC-DOCUMENT>0001234567-26-000123.txt
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000111111</issuerCik><issuerName>Northwind Robotics Inc</issuerName><issuerTradingSymbol>nwr</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0002222222</rptOwnerCik><rptOwnerName>OKAFOR ADAEZE N</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0003333333</rptOwnerCik><rptOwnerName>Okafor Family Holdings LLC</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isTenPercentOwner>1</isTenPercentOwner></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-11-12</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>100000</value></transactionShares>
        <transactionPricePerShare><value>40.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>900000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-11-13</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>50000</value></transactionShares>
        <transactionPricePerShare><value>42.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>850000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-11-13</value></transactionDate>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>9999999</value></transactionShares>
        <transactionPricePerShare><value>1.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</SEC-DOCUMENT>"""

ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Latest Filings</title>
<entry><title>4 - OKAFOR ADAEZE N (0002222222) (Reporting)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/2222222/000123456726000123/0001234567-26-000123-index.htm"/>
<summary type="html">Filed: 2026-11-16 AccNo: 0001234567-26-000123 Size: 5 KB</summary></entry>
<entry><title>4 - Northwind Robotics Inc (0000111111) (Issuer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/111111/000123456726000123/0001234567-26-000123-index.htm"/>
<summary type="html">Filed: 2026-11-16 AccNo: 0001234567-26-000123 Size: 5 KB</summary></entry>
</feed>"""

IDX = """Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
4           OKAFOR ADAEZE N                                               2222222     20261113    edgar/data/2222222/0001234567-26-000123.txt
4/A         SOMEONE ELSE                                                  4444444     20261113    edgar/data/4444444/0009999999-26-000001.txt
424B2       BIG BANK                                                      5555555     20261113    edgar/data/5555555/0008888888-26-000002.txt
"""


class EdgarParseTests(unittest.TestCase):
    def test_parse_form4_sales_owners_and_holdings(self):
        f = edgar.parse_form4(FORM4)
        self.assertEqual(f["ticker"], "NWR")
        self.assertEqual(f["owners"], [{"name": "Adaeze N Okafor", "role": "Chief Executive Officer"}])  # LLC skipped
        self.assertEqual(f["sale_value"], 100000 * 40 + 50000 * 42)   # option exercise (M) ignored
        self.assertEqual(f["holdings_value"], 850000 * 42)

    def test_person_name(self):
        self.assertEqual(edgar.person_name("MCDONALD JAMES R JR"), "James R McDonald Jr")
        self.assertIsNone(edgar.person_name("MADONNA"))

    def test_latest_feed_dedupes_accessions(self):
        with mock.patch.object(edgar, "_get", return_value=ATOM):
            self.assertEqual(edgar.latest_filings(), [("2222222", "0001234567-26-000123")])

    def test_daily_index_keeps_only_form_4(self):
        with mock.patch.object(edgar, "_get", return_value=IDX):
            self.assertEqual(edgar.daily_filings(date(2026, 11, 13)), [("2222222", "0001234567-26-000123")])

    def test_below_threshold_is_not_a_signal(self):
        f = edgar.parse_form4(FORM4)
        with mock.patch.object(config, "EDGAR_MIN_SALE_USD", 1e9):
            self.assertEqual(edgar.to_signals(f, "1", "0001234567-26-000123", "2026-11-16"), ([], []))

    def test_user_agent_carries_contact(self):
        self.assertIn(config.CONTACT, edgar.headers()["User-Agent"])


class EdgarPipelineTests(unittest.TestCase):
    def setUp(self):
        db.init()

    def test_poll_stores_buy_signal_once_and_feeds_profile(self):
        def fake_get(url):
            return ATOM if "getcurrent" in url else FORM4
        with mock.patch.object(edgar, "_get", side_effect=fake_get):
            first = edgar.poll()
            second = edgar.poll()
        self.assertEqual(first["new"], 1)
        self.assertEqual(second, {"read": 0, "new": 0})     # accession remembered
        self.assertEqual(db.last_pull()["counts_against_quota"], 0)   # never touches the NewsAPI quota

        stories, people = live.stories_and_people()
        s = next(s for s in stories if s["source"] == edgar.SOURCE)
        self.assertEqual(s["signal"], "buy")
        self.assertIn("Adaeze N Okafor sells $6.1M of Northwind Robotics Inc (NWR)", s["headline"])

        prof = live.build_tags("Adaeze N Okafor", {"found": False}, {"credits": []}, {"credits": []}, [s["headline"]])
        self.assertEqual(prof["worthSource"], "sec")
        self.assertEqual(prof["worth"], 850000 * 42)
        labels = [t[1] for t in prof["tags"] if t[0] in ("wealth", "aff")]
        self.assertTrue(any(l.startswith("Insider sale: $6.1M of NWR") for l in labels))
        self.assertIn("Chief Executive Officer at Northwind Robotics Inc", labels)

    def test_wikidata_worth_wins_over_sec(self):
        sec = [{"holdings_value": 5e9, "sale_value": 1e7, "ticker": "X", "issuer": "X Co", "role": "Director", "filed_at": "2026-11-01"}]
        prof = live.build_tags("A B", {"found": True, "net_worth": "$2.0B (2025)"}, {"credits": []}, {"credits": []}, [], sec=sec)
        self.assertEqual(prof["worthSource"], "wikipedia")


if __name__ == "__main__":
    unittest.main()
