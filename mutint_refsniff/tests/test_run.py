"""The task, end to end under the immediate backend."""

import gzip
import os
import subprocess
from unittest import mock

from mutint_common import store
from mutint_jobs import logs, processes

from mutint_refsniff import fastq, tasks
from mutint_refsniff.models import STATUS_FAILED, STATUS_FINISHED, RefsniffRun
from mutint_refsniff.tests import fake_http, fake_sketch
from mutint_refsniff.tests.fixture import RefsniffFixture
from mutint_refsniff.tests.test_fastq import records


class RunTestCase(RefsniffFixture):
    def _run(self, **kwargs):
        """Launch a drop. The immediate backend runs the task inside the launch."""
        with fake_sketch.patched(**kwargs) as run_tool, \
                fake_http.patched(assemblies=fake_sketch.ASSEMBLIES):
            response = self.launch(self.stage(name="reads.fastq.gz").id)
        self.assertEqual(200, response.status_code, response.content)
        return RefsniffRun.objects.get(), run_tool

    def test_a_sketch_that_answers(self):
        run, run_tool = self._run()
        self.assertEqual(STATUS_FINISHED, run.status)
        self.assertEqual((135798, 20369700), (run.reads_sketched, run.bases_sketched))
        self.assertEqual(3, len(run.hits))
        self.assertEqual("Escherichia coli B str. REL606", run.best_organism)
        # The top hit's *assembly*, not its chromosome: core resolves a GCF_ to every
        # sequence it is made of, which is the reference breseq wants.
        self.assertEqual("GCF_000017985.1", run.best_accession)
        self.assertEqual(("", ""), (run.error, run.note))
        self.assertIsNotNone(run.finished_at)

    def test_the_reads_went_to_sendsketch_under_their_own_name(self):
        """gzip is chosen by suffix all the way down, so the drop keeps the name it
        arrived with."""
        run, run_tool = self._run()
        argv = run_tool.calls[0]
        self.assertEqual(1, len(run_tool.calls))
        self.assertIn("in=%s" % os.path.join(store.component_dir("mutint_refsniff", run.pk),
                                             "reads.fastq.gz"), argv)
        self.assertIn("address=refseq", argv)
        self.assertIn("level=1", argv)

    def test_each_hit_carries_what_use_as_reference_would_import(self):
        run, _run_tool = self._run()
        by_name = {hit["name"]: hit for hit in run.hits}
        # A complete genome with an assembly: the assembly.
        self.assertEqual("GCF_000017985.1",
                         by_name["Escherichia coli B str. REL606"]["import_accession"])
        # A draft with an assembly: still the assembly, never its one contig.
        self.assertEqual("GCF_004355015.1",
                         by_name["Escherichia coli OP50"]["import_accession"])
        self.assertTrue(by_name["Escherichia coli OP50"]["draft"])
        # A complete genome NCBI listed no assembly for: its own record.
        self.assertEqual("NZ_CP014768.1",
                         by_name["Shigella sp. PAMC 28760"]["import_accession"])

    def test_the_log_has_the_account_and_the_row_its_tail_and_the_files_are_gone(self):
        run, _run_tool = self._run()
        text, _ = logs.read_tail(run.task_result_id)
        self.assertIn("Sketched 135798 reads", text)
        self.assertIn("3 genomes matched", text)
        self.assertIn("Sketched 135798 reads", run.log)
        self.assertFalse(os.path.exists(run.directory()))

    def test_a_head_at_the_limit_explains_the_zlib_traceback_before_it_happens(self):
        """sendsketch prints an EOFException for a gzip cut at 16 MB and exits 0. The log has
        to say so, above it, or a perfectly good run reads as a broken one.

        `HEAD_BYTES` is patched rather than staging 16 MB: what is being tested is the rule,
        and a real head would make this test take a second and the file a megabyte."""
        with mock.patch.object(tasks.fastq, "HEAD_BYTES", 50), \
                fake_sketch.patched(), \
                fake_http.patched(assemblies=fake_sketch.ASSEMBLIES):
            response = self.launch(self.stage(name="reads.fastq.gz").id)
        self.assertEqual(200, response.status_code, response.content)
        run = RefsniffRun.objects.get()

        text, _ = logs.read_tail(run.task_result_id)
        self.assertIn("EOFException", text)
        self.assertIn("not a failure", text)
        # Above the tool's own output, which is the whole point of writing it first.
        self.assertLess(text.index("EOFException"), text.index("Sketched"))

    def test_a_head_under_the_limit_says_nothing_about_truncation(self):
        run, _run_tool = self._run()
        text, _ = logs.read_tail(run.task_result_id)
        self.assertNotIn("EOFException", text)
        self.assertNotIn("cut off", text)

    def test_a_sketch_that_matched_nothing_is_a_finished_run_with_a_note(self):
        run, _run_tool = self._run(text='{"Name": "x", "Seqs": 3, "Bases": 30}')
        self.assertEqual(STATUS_FINISHED, run.status)
        self.assertEqual([], run.hits)
        self.assertEqual("", run.best_accession)
        self.assertIn("matched no genome", run.note)

    def test_a_nonzero_exit_fails_the_run(self):
        run, _run_tool = self._run(returncode=3)
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("exited with status 3", run.error)
        self.assertFalse(os.path.exists(run.directory()))

    def test_no_result_file_fails_the_run(self):
        run, _run_tool = self._run(write=False)
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("wrote no result file", run.error)

    def test_unreadable_output_fails_the_run_with_a_sentence(self):
        run, _run_tool = self._run(text="this is not json")
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("readable JSON", run.error)

    def test_a_timeout_fails_the_run(self):
        run, _run_tool = self._run(raises=subprocess.TimeoutExpired("sendsketch", 600))
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("did not finish within", run.error)

    def test_a_tool_that_cannot_be_started_fails_the_run(self):
        run, _run_tool = self._run(raises=OSError("Exec format error"))
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("could not be started", run.error)

    def test_a_missing_tool_fails_the_run_naming_what_installs_it(self):
        run, run_tool = self._run(no_tool=True)
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("tools.txt", run.error)
        # Looked for before anything else, so nothing was run.
        self.assertEqual([], run_tool.calls)

    def test_deleting_the_row_removes_its_directory(self):
        from mutint_refsniff.models import COMPONENT
        run, _run_tool = self._run()
        directory = store.component_dir(COMPONENT, run.pk)
        os.makedirs(directory, exist_ok=True)
        run.delete()
        self.assertFalse(os.path.exists(directory))


class AccessionRunTestCase(RefsniffFixture):
    """A run named by accession: the fetch happens on the worker, before the sketch."""

    def _run(self, files, sketch_kwargs=None, **http_kwargs):
        rows = {"SRR2584863": [fake_http.ROW()]}
        http_kwargs.setdefault("assemblies", fake_sketch.ASSEMBLIES)
        with fake_sketch.patched(**(sketch_kwargs or {})) as run_tool, \
                fake_http.patched(rows, files, **http_kwargs) as get:
            response = self.launch("", accession="SRR2584863")
        self.assertEqual(200, response.status_code, response.content)
        return RefsniffRun.objects.get(), run_tool, get

    def test_the_head_is_fetched_and_sketched(self):
        run, run_tool, _get = self._run({"SRR2584863_1.fastq.gz": gzip.compress(records(10))})
        self.assertEqual(STATUS_FINISHED, run.status)
        self.assertEqual("GCF_000017985.1", run.best_accession)
        self.assertIn("in=%s" % os.path.join(run.directory(), "SRR2584863_1.fastq.gz"),
                      run_tool.calls[0])
        text, _ = logs.read_tail(run.task_result_id)
        self.assertIn("Fetching the first 16 MB of SRR2584863_1.fastq.gz", text)
        self.assertIn("from SRR2584863_1.fastq.gz (SRR2584863)", text)
        self.assertFalse(os.path.exists(run.directory()))

    def test_a_server_that_ignores_the_range_still_works(self):
        # A 200 rather than a 206: the range was asked for and not honoured. The fetch
        # reads to its own limit and closes the response, so the answer is the same.
        whole = gzip.compress(records(400))
        run, _run_tool, get = self._run({"SRR2584863_1.fastq.gz": whole}, status=200)
        self.assertEqual(STATUS_FINISHED, run.status)
        self.assertEqual(["bytes=0-%d" % (fastq.HEAD_BYTES - 1)], get.ranges)

    def test_the_fetch_stops_at_the_limit(self):
        # Pure: a server streaming past the range asked for is cut off at the limit.
        from mutint_refsniff import ena
        path = os.path.join(self.store, "head.bin")
        with fake_http.patched({}, {"f": b"x" * 100}, chunk=30, status=200):
            written = ena.fetch_head("https://h/f", path, 45, "f")
        self.assertEqual(45, written)
        self.assertEqual(45, os.path.getsize(path))

    def test_ena_unreachable_fails_the_run_with_a_redacted_sentence(self):
        run, run_tool, _get = self._run({}, unreachable=True)
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("Could not reach ENA", run.error)
        self.assertNotIn("SECRET", run.error)
        self.assertNotIn("o@e.com", run.error)
        self.assertEqual([], run_tool.calls)
        self.assertFalse(os.path.exists(run.directory()))

    def test_a_file_ena_answers_404_for_fails_the_run(self):
        run, run_tool, _get = self._run({})
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("HTTP 404", run.error)
        self.assertEqual([], run_tool.calls)

    def test_a_fetch_that_stops_partway_fails_the_run(self):
        run, _run_tool, _get = self._run(
            {"SRR2584863_1.fastq.gz": gzip.compress(records(10))}, raise_after=1)
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("stopped partway", run.error)

    def test_a_head_with_no_whole_record_fails_the_run(self):
        run, run_tool, _get = self._run({"SRR2584863_1.fastq.gz": b"@r1\nACG"})
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("no complete FASTQ record", run.error)
        self.assertEqual([], run_tool.calls)

    def test_a_missing_tool_is_found_before_anything_is_downloaded(self):
        run, run_tool, get = self._run({"SRR2584863_1.fastq.gz": gzip.compress(records(10))},
                                       sketch_kwargs={"no_tool": True})
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("tools.txt", run.error)
        self.assertEqual([], get.files)
        self.assertEqual([], run_tool.calls)


class RunToolSeamTestCase(RefsniffFixture):
    """The fake stands in for `mutint_jobs.processes.run_tool`; this asserts it is the
    thing the task actually reaches, so the fake cannot drift away from the seam."""

    def test_the_task_shells_out_through_run_tool(self):
        from mutint_refsniff import tasks
        self.assertIs(processes, tasks.processes)
