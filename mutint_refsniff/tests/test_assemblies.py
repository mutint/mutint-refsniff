"""Turning a sketch hit's taxid into the assembly **Use as reference** should import."""

from unittest import mock

from django.test import SimpleTestCase

from mutint_refsniff import assemblies
from mutint_refsniff.tests import fake_http
from mutint_refsniff.tests.fake_http import report


def _hit(taxid, accession="NC_1", draft=False, name="n"):
    return {"taxid": taxid, "accession": accession, "draft": draft, "name": name,
            "assembly": "", "import_accession": ""}


class AssemblyForTestCase(SimpleTestCase):
    def _lookup(self, reports, taxid=413997):
        with mock.patch("requests.get", new=fake_http.fake_http(
                assemblies={str(taxid): reports})):
            return assemblies.assembly_for(taxid)

    def test_the_only_assembly(self):
        found = self._lookup([report()])
        self.assertEqual("GCF_000017985.1", found["accession"])
        self.assertEqual("Complete Genome", found["level"])
        self.assertEqual("Escherichia coli B str. REL606", found["organism"])

    def test_a_complete_genome_beats_a_draft_listed_first(self):
        found = self._lookup([report("GCF_2", level="Contig"),
                              report("GCF_1", level="Complete Genome")])
        self.assertEqual("GCF_1", found["accession"])

    def test_between_complete_genomes_ncbis_own_reference_wins(self):
        found = self._lookup([report("GCF_2", level="Complete Genome"),
                              report("GCF_1", level="Complete Genome",
                                     category="reference genome")])
        self.assertEqual("GCF_1", found["accession"])

    def test_with_nothing_to_prefer_the_order_ncbi_listed_them_in_stands(self):
        found = self._lookup([report("GCF_2", level="Contig"), report("GCF_1", level="Contig")])
        self.assertEqual("GCF_2", found["accession"])

    def test_a_taxid_ncbi_lists_nothing_for(self):
        self.assertIsNone(self._lookup([]))

    def test_no_taxid_asks_nothing(self):
        with mock.patch("requests.get", new=fake_http.fake_http()) as get:
            self.assertIsNone(assemblies.assembly_for(0))
        self.assertEqual([], get.taxids)


class AnnotateTestCase(SimpleTestCase):
    def _annotate(self, hits, **kwargs):
        with mock.patch("requests.get", new=fake_http.fake_http(**kwargs)) as get:
            assemblies.annotate(hits, sleep=lambda seconds: None)
        return get

    def test_each_hit_gets_its_assembly_and_what_to_import(self):
        hits = [_hit(413997), _hit(637912, accession="NZ_1", draft=True)]
        get = self._annotate(hits, assemblies={
            "413997": [report()], "637912": [report("GCF_004355015.1", level="Contig")]})
        self.assertEqual(["413997", "637912"], get.taxids)
        self.assertEqual(["GCF_000017985.1", "GCF_004355015.1"],
                         [hit["assembly"] for hit in hits])
        self.assertEqual(["GCF_000017985.1", "GCF_004355015.1"],
                         [hit["import_accession"] for hit in hits])

    def test_a_taxid_with_no_assembly_falls_back_by_kind(self):
        hits = [_hit(1, accession="NC_9"), _hit(2, accession="NZ_9", draft=True)]
        self._annotate(hits)
        self.assertEqual(["", ""], [hit["assembly"] for hit in hits])
        # A complete genome is importable as its own record; a draft's one contig is not.
        self.assertEqual(["NC_9", ""], [hit["import_accession"] for hit in hits])

    def test_an_ncbi_that_answers_an_error_leaves_the_row_and_says_so(self):
        """The sketch is the answer; the assembly is a convenience on it. A lookup that
        fails must not fail the run."""
        said = []
        hits = [_hit(413997, accession="NC_9")]
        with mock.patch("requests.get",
                        new=fake_http.fake_http(datasets_status=500)):
            assemblies.annotate(hits, report=said.append, sleep=lambda s: None)
        self.assertEqual("", hits[0]["assembly"])
        self.assertEqual("NC_9", hits[0]["import_accession"])
        self.assertEqual(1, len(said))
        self.assertIn("Could not look up an assembly", said[0])

    def test_the_polite_delay_is_between_lookups_and_not_before_the_first(self):
        waited = []
        with mock.patch("requests.get", new=fake_http.fake_http()):
            assemblies.annotate([_hit(1), _hit(2), _hit(3)], sleep=waited.append)
        self.assertEqual([assemblies.POLITE_DELAY_SECONDS] * 2, waited)

    def test_one_hit_costs_no_wait(self):
        waited = []
        with mock.patch("requests.get", new=fake_http.fake_http()):
            assemblies.annotate([_hit(1)], sleep=waited.append)
        self.assertEqual([], waited)
