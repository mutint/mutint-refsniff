"""The BLAST URL API client: what it sends, how it waits, and what it makes of the answer."""

from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase, override_settings

from mutint_jobs.jobs import JobCancelled

from mutint_refsniff import blast
from mutint_refsniff.tests import fake_blast


class PutTestCase(SimpleTestCase):
    @override_settings(MUTINT_NCBI_EMAIL="ops@example.org", MUTINT_NCBI_API_KEY="SECRET")
    def test_the_submission_carries_the_search_and_the_identity_but_no_key(self):
        http = fake_blast.FakeHttp()
        rid, rtoe = blast.put(">read_1\nACGT\n", http)
        self.assertEqual((fake_blast.RID, 23), (rid, rtoe))
        data = http.put_data[0]
        self.assertEqual("Put", data["CMD"])
        self.assertEqual("blastn", data["PROGRAM"])
        self.assertEqual("on", data["MEGABLAST"])
        self.assertEqual("refseq_genomes", data["DATABASE"])
        self.assertEqual("txid2[ORGN] OR txid2157[ORGN]", data["ENTREZ_QUERY"])
        self.assertEqual("5", data["HITLIST_SIZE"])
        self.assertEqual(">read_1\nACGT\n", data["QUERY"])
        self.assertEqual("mutint", data["tool"])
        self.assertEqual("ops@example.org", data["email"])
        self.assertNotIn("api_key", data)
        self.assertNotIn("SECRET", str(http.requests))

    @override_settings(MUTINT_NCBI_EMAIL="ops@example.org")
    def test_the_callers_address_wins_over_the_deployments(self):
        http = fake_blast.FakeHttp(waits=0)
        blast.put(">x\nACGT\n", http, email="me@lab.edu")
        self.assertEqual("me@lab.edu", http.put_data[0]["email"])
        blast.status("R", http, "me@lab.edu")
        self.assertIn("email=me%40lab.edu", http.gets[0])
        # With none given, the deployment's.
        blast.put(">x\nACGT\n", http)
        self.assertEqual("ops@example.org", http.put_data[1]["email"])

    def test_a_refusal_carries_ncbis_words(self):
        http = fake_blast.FakeHttp(put_page=fake_blast.REFUSED_PAGE)
        with self.assertRaises(blast.BlastError) as raised:
            blast.put(">x\nACGT\n", http)
        self.assertIn("Failed to read the Blast query", str(raised.exception))

    @override_settings(MUTINT_NCBI_EMAIL="ops@example.org")
    def test_a_get_carries_the_identity_too(self):
        http = fake_blast.FakeHttp(waits=0)
        blast.status("R", http)
        query = parse_qs(urlparse(http.gets[0]).query)
        self.assertEqual(["ops@example.org"], query["email"])
        self.assertEqual(["mutint"], query["tool"])
        self.assertEqual(["SearchInfo"], query["FORMAT_OBJECT"])


class StatusTestCase(SimpleTestCase):
    def test_waiting_then_ready(self):
        http = fake_blast.FakeHttp(waits=1)
        self.assertEqual(("WAITING", False), blast.status("R", http))
        self.assertEqual(("READY", True), blast.status("R", http))

    def test_no_hits_and_unknown(self):
        self.assertEqual(("READY", False),
                         blast.status("R", fake_blast.FakeHttp(waits=0, hits="no")))
        self.assertEqual(("UNKNOWN", False), blast.status("R", lambda url, data=None: "<html>"))


class ParseTestCase(SimpleTestCase):
    def test_the_best_hsp_of_the_first_hit_per_query(self):
        rows = blast.parse(fake_blast.XML)
        self.assertEqual(["read_1", "read_2", "read_3"], [r["query"] for r in rows])
        self.assertEqual("NC_000913.3", rows[0]["accession"])
        self.assertEqual((150, 150, 277.0),
                         (rows[0]["identity"], rows[0]["align_len"], rows[0]["bits"]))
        self.assertEqual("NC_012967.1", rows[2]["accession"])

    def test_not_xml_is_an_error(self):
        with self.assertRaises(blast.BlastError):
            blast.parse("<html>Error</html><p>")

    def test_organism_from_title(self):
        cases = {
            "Escherichia coli str. K-12 substr. MG1655, complete genome":
                "Escherichia coli str. K-12 substr. MG1655",
            "Escherichia coli B str. REL606, complete genome": "Escherichia coli B str. REL606",
            "Pseudomonas aeruginosa PAO1 chromosome, complete genome":
                "Pseudomonas aeruginosa PAO1",
            "Escherichia coli O157:H7 str. Sakai plasmid pO157, complete sequence":
                "Escherichia coli O157:H7 str. Sakai",
            "Bacillus subtilis subsp. subtilis str. 168 DNA, complete genome":
                "Bacillus subtilis subsp. subtilis str. 168",
            "Some organism": "Some organism",
        }
        for title, organism in cases.items():
            self.assertEqual(organism, blast.organism_from_title(title), title)

    def test_tally_ranks_by_reads_then_identity(self):
        hits = blast.tally(blast.parse(fake_blast.XML), 4)
        self.assertEqual(["NC_000913.3", "NC_012967.1"], [h["accession"] for h in hits])
        self.assertEqual(2, hits[0]["reads"])
        self.assertEqual(0.5, hits[0]["fraction"])
        self.assertEqual(99.33, hits[0]["mean_identity"])
        self.assertEqual("Escherichia coli str. K-12 substr. MG1655", hits[0]["organism"])
        self.assertEqual(100.0, hits[1]["mean_identity"])


class WaitTestCase(SimpleTestCase):
    def test_polls_no_faster_than_once_a_minute_and_first_waits_for_the_estimate(self):
        http = fake_blast.FakeHttp(waits=2)
        clock = fake_blast.FakeClock()
        self.assertTrue(blast.wait("R", 90, http, sleep=clock.sleep, clock=clock.clock))
        self.assertEqual(3, http.status_calls)
        # First poll at 90 s (NCBI's estimate), then every 60 s; slices of at most 5 s.
        self.assertEqual(90 + 60 + 60, clock.now)
        self.assertTrue(all(s <= blast.SLICE_SECONDS for s in clock.slept))

    def test_a_short_estimate_still_waits_the_floor(self):
        http = fake_blast.FakeHttp(waits=0)
        clock = fake_blast.FakeClock()
        blast.wait("R", 5, http, sleep=clock.sleep, clock=clock.clock)
        self.assertEqual(60, clock.now)

    def test_a_cancel_lands_within_one_slice_and_asks_ncbi_nothing_more(self):
        http = fake_blast.FakeHttp(forever=True)
        clock = fake_blast.FakeClock()

        def check_cancelled():
            if clock.now >= 7:
                raise JobCancelled("stop")

        with self.assertRaises(JobCancelled):
            blast.wait("R", 0, http, check_cancelled=check_cancelled, sleep=clock.sleep,
                       clock=clock.clock)
        self.assertLessEqual(clock.now, 7 + blast.SLICE_SECONDS)
        self.assertEqual(0, http.status_calls)

    def test_failed_and_unknown_and_timeout_raise(self):
        clock = fake_blast.FakeClock()
        with self.assertRaises(blast.BlastError) as raised:
            blast.wait("R", 0, fake_blast.FakeHttp(waits=0, final="FAILED"),
                       sleep=clock.sleep, clock=clock.clock)
        self.assertIn("failed", str(raised.exception))
        with self.assertRaises(blast.BlastError) as raised:
            blast.wait("R", 0, fake_blast.FakeHttp(waits=0, final="UNKNOWN"),
                       sleep=clock.sleep, clock=clock.clock)
        self.assertIn("no longer knows", str(raised.exception))
        clock = fake_blast.FakeClock()
        with self.assertRaises(blast.BlastError) as raised:
            blast.wait("R", 0, fake_blast.FakeHttp(forever=True), sleep=clock.sleep,
                       clock=clock.clock, timeout=200)
        self.assertIn("still running", str(raised.exception))
        self.assertLessEqual(clock.now, 260)


class RunTestCase(SimpleTestCase):
    def test_end_to_end(self):
        lines = []
        with fake_blast.patched() as (http, clock):
            result = blast.run(">read_1\nACGT\n", 4, log_write=lines.append)
        self.assertEqual(fake_blast.RID, result["rid"])
        self.assertEqual(3, result["matched"])
        self.assertEqual("NC_000913.3", result["hits"][0]["accession"])
        self.assertTrue(any("submitted as" in line for line in lines))
        self.assertTrue(any("3 of 4 reads" in line for line in lines))
        # Put, one WAITING, one READY, one fetch.
        self.assertEqual(4, len(http.requests))

    def test_no_hits(self):
        with fake_blast.patched(fake_blast.FakeHttp(waits=0, hits="no")) as (http, _clock):
            result = blast.run(">read_1\nACGT\n", 1)
        self.assertEqual([], result["hits"])
        self.assertEqual(2, len(http.requests))
