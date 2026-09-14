"""sendsketch's command line, and reading what it wrote -- on a real run's real output."""

import json
import os
import shutil
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from mutint_refsniff import sketch
from mutint_refsniff.tests import fake_sketch


class ArgvTestCase(SimpleTestCase):
    def test_the_command_line(self):
        argv = sketch.build_argv("/t/sendsketch.sh", "/r/head.fastq.gz", "/r/sketch.json")
        self.assertEqual(
            ["/t/sendsketch.sh", "-Xmx1g", "in=/r/head.fastq.gz", "address=refseq",
             "level=1", "records=10", "format=json", "printname0=t", "printtaxa=t",
             "out=/r/sketch.json"], argv)

    def test_level_one_is_per_strain_and_is_not_the_default(self):
        """`level=2`, sendsketch's default, answers one record per *species*, which is the
        one answer this page cannot use. Asserted because it is a single character."""
        self.assertIn("level=1", sketch.build_argv("s", "i", "o"))

    def test_the_heap_is_capped(self):
        """Left to itself sendsketch takes about 5.6 GB, beside a pool of workers and a
        database on the same machine."""
        self.assertIn("-Xmx1g", sketch.build_argv("s", "i", "o"))
        self.assertIn("-Xmx2g", sketch.build_argv("s", "i", "o", heap="2g"))

    def test_records_reaches_the_command_line(self):
        self.assertIn("records=3", sketch.build_argv("s", "i", "o", records=3))


class ParseTestCase(SimpleTestCase):
    """Against `tests/data/sketch_rel606.json`, which is what sendsketch actually wrote for
    the first 16 MiB of SRR2584863 read 1 on 2026-09-14."""

    def setUp(self):
        self.result = sketch.parse(fake_sketch.real())

    def test_the_query_summary(self):
        self.assertEqual(135798, self.result["reads"])
        self.assertEqual(20369700, self.result["bases"])

    def test_every_dict_value_is_a_hit_and_the_scalars_are_not(self):
        self.assertEqual(10, len(self.result["hits"]))
        self.assertNotIn("RefSeq", [hit["name"] for hit in self.result["hits"]])

    def test_the_top_hit_is_rel606(self):
        top = self.result["hits"][0]
        self.assertEqual("Escherichia coli B str. REL606", top["name"])
        self.assertEqual(413997, top["taxid"])
        self.assertEqual("NC_012967.1", top["accession"])
        self.assertEqual("Escherichia coli B str. REL606, complete genome", top["title"])
        self.assertEqual(99.785, top["ani"])
        self.assertEqual(100.0, top["completeness"])
        self.assertEqual(1.14, top["contamination"])
        self.assertEqual((19849, 13), (top["matches"], top["unique"]))
        self.assertFalse(top["draft"])
        self.assertIn("Enterobacteriaceae", top["taxonomy"])

    def test_k12_is_nowhere_in_the_answer(self):
        """The whole point of sketching the full head rather than 200 reads: K-12 is 1.5%
        divergent from REL606, above mutint-breseq's `--max-percent-divergence 1.0`."""
        self.assertNotIn("K-12", " ".join(hit["name"] for hit in self.result["hits"]))

    def test_a_draft_assemblys_seq_name_is_one_contig_and_is_marked(self):
        op50 = [hit for hit in self.result["hits"] if hit["name"] == "Escherichia coli OP50"][0]
        self.assertTrue(op50["draft"])
        self.assertEqual("NZ_WIDO01000031.1", op50["accession"])

    def test_the_order_is_sendsketchs_own_and_is_not_a_column_here(self):
        """The last two rows of this real answer are *not* in `matches` order, so sorting on
        any one column here would reorder the tail."""
        matches = [hit["matches"] for hit in self.result["hits"]]
        self.assertNotEqual(sorted(matches, reverse=True), matches)

    def test_hits_start_with_no_assembly(self):
        """`assemblies.annotate` fills these; `parse` reaches no network."""
        self.assertEqual({""}, {hit["assembly"] for hit in self.result["hits"]})

    def test_nothing_readable_is_a_sentence_not_a_traceback(self):
        for bad in ("", "not json", "[1, 2]"):
            with self.assertRaises(sketch.SketchError, msg=bad):
                sketch.parse(bad)

    def test_a_sketch_that_matched_nothing_parses_to_no_hits(self):
        result = sketch.parse(json.dumps({"Name": "x", "Seqs": 5, "Bases": 50}))
        self.assertEqual([], result["hits"])
        self.assertEqual((5, 50), (result["reads"], result["bases"]))


class ImportAccessionTestCase(SimpleTestCase):
    def test_the_assembly_wins(self):
        self.assertEqual("GCF_1", sketch.import_accession(
            {"assembly": "GCF_1", "accession": "NC_1", "draft": False}))

    def test_a_complete_genome_falls_back_to_its_nucleotide_accession(self):
        self.assertEqual("NC_1", sketch.import_accession(
            {"assembly": "", "accession": "NC_1", "draft": False}))

    def test_a_draft_with_no_assembly_offers_nothing(self):
        """Its `seqName` names one contig of hundreds; importing it would be a reference
        missing almost all of the genome."""
        self.assertEqual("", sketch.import_accession(
            {"assembly": "", "accession": "NZ_1", "draft": True}))


class ToolEnvironmentTestCase(SimpleTestCase):
    """What `sendsketch.sh` is handed to run `java` with.

    The point of these is one failure that a machine with a system JDK cannot show: bioconda's
    `openjdk` installs **nothing** into the prefix's `bin/`, so putting `env/tools/bin` on
    PATH -- which is all every other component in the suite needs -- leaves the tool running
    whatever `java` the host happens to have, or none.
    """

    def setUp(self):
        self.tools = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tools, True)
        os.makedirs(os.path.join(self.tools, "bin"))

    def _with_jvm(self):
        os.makedirs(os.path.join(self.tools, sketch.JVM_DIR, "bin"))

    def _environment(self, env):
        with mock.patch.object(sketch.tools, "tools_dir", return_value=self.tools):
            return sketch.tool_environment(env)

    def test_the_provisioned_jvm_is_on_path_ahead_of_the_hosts(self):
        self._with_jvm()
        env = self._environment({"PATH": "/usr/bin"})
        entries = env["PATH"].split(os.pathsep)
        self.assertEqual([os.path.join(self.tools, "bin"),
                          os.path.join(self.tools, sketch.JVM_DIR, "bin"),
                          "/usr/bin"], entries)

    def test_java_home_is_what_condas_activation_script_would_have_set(self):
        self._with_jvm()
        env = self._environment({})
        self.assertEqual(os.path.join(self.tools, sketch.JVM_DIR), env["JAVA_HOME"])
        self.assertEqual(os.path.join(self.tools, sketch.JVM_DIR, "lib", "server"),
                         env["JAVA_LD_LIBRARY_PATH"])

    def test_without_a_provisioned_jvm_the_hosts_java_home_is_left_alone(self):
        env = self._environment({"JAVA_HOME": "/host/jdk", "PATH": "/usr/bin"})
        self.assertEqual("/host/jdk", env["JAVA_HOME"])
        self.assertEqual([os.path.join(self.tools, "bin"), "/usr/bin"],
                         env["PATH"].split(os.pathsep))

    def test_with_no_tools_directory_the_environment_is_unchanged(self):
        with mock.patch.object(sketch.tools, "tools_dir", return_value=""):
            self.assertEqual({"PATH": "/usr/bin"},
                             sketch.tool_environment({"PATH": "/usr/bin"}))
