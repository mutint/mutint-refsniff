"""The task, end to end under the immediate backend."""

import os

from mutint_common import store
from mutint_jobs import logs

from mutint_refsniff.models import STATUS_FAILED, STATUS_FINISHED, RefsniffRun
from mutint_refsniff.tests import fake_blast
from mutint_refsniff.tests.fixture import RefsniffFixture


class RunTestCase(RefsniffFixture):
    def _run(self, http=None, **kwargs):
        with fake_blast.patched(http) as (fake, _clock):
            response = self.launch(self.stage().id, **kwargs)
        self.assertEqual(200, response.status_code, response.content)
        return RefsniffRun.objects.get(), fake

    def test_a_search_that_answers(self):
        run, http = self._run()
        self.assertEqual(STATUS_FINISHED, run.status)
        self.assertEqual(fake_blast.RID, run.blast_rid)
        self.assertEqual("NC_000913.3", run.best_accession)
        self.assertEqual("Escherichia coli str. K-12 substr. MG1655", run.best_organism)
        self.assertEqual(2, run.blast_hits[0]["reads"])
        self.assertEqual(("", ""), (run.error, run.note))
        self.assertIsNotNone(run.finished_at)
        # The reads went to NCBI as the renumbered FASTA, naming the person who launched it
        # -- not the deployment's fallback address -- on every request.
        self.assertIn(">read_1\n", http.put_data[0]["QUERY"])
        self.assertEqual("o@e.com", http.put_data[0]["email"])
        for url in http.gets:
            self.assertIn("email=o%40e.com", url)
        # The log has the search's account, and the row its tail; the files are gone.
        text, _ = logs.read_tail(run.task_result_id)
        self.assertIn("BLAST submitted as", text)
        self.assertIn("3 of 10 reads", text)
        self.assertIn("BLAST submitted as", run.log)
        self.assertFalse(os.path.exists(run.directory()))

    def test_no_hits_is_a_finished_run_with_a_note(self):
        run, _http = self._run(fake_blast.FakeHttp(waits=0, hits="no"))
        self.assertEqual(STATUS_FINISHED, run.status)
        self.assertEqual([], run.blast_hits)
        self.assertEqual("", run.best_accession)
        self.assertIn("no matching genome", run.note)

    def test_a_refused_search_fails_the_run_and_names_ncbis_reason(self):
        """The task re-raises after recording the failure; the immediate backend the view
        runs it under records that on the queue's side and the launch still answers 200,
        which is why this asserts the row rather than an exception."""
        run, _http = self._run(fake_blast.FakeHttp(put_page=fake_blast.REFUSED_PAGE))
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("Failed to read the Blast query", run.error)
        self.assertEqual("", run.best_accession)
        self.assertFalse(os.path.exists(run.directory()))

    def test_a_failed_search_fails_the_run(self):
        run, _http = self._run(fake_blast.FakeHttp(waits=0, final="FAILED"))
        self.assertEqual(STATUS_FAILED, run.status)
        self.assertIn("failed", run.error)

    def test_the_email_never_reaches_the_error(self):
        run, _http = self._run(fake_blast.FakeHttp(put_page=fake_blast.REFUSED_PAGE))
        self.assertNotIn("o@e.com", run.error)
        self.assertNotIn("o@e.com", run.log)

    def test_deleting_the_row_removes_its_directory(self):
        from mutint_refsniff.models import COMPONENT
        run, _http = self._run()
        directory = store.component_dir(COMPONENT, run.pk)
        os.makedirs(directory, exist_ok=True)
        run.delete()
        self.assertFalse(os.path.exists(directory))
