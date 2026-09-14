"""Stopping a run before it starts, while its reads are fetched, and while it is sketching."""

import gzip
import os
from unittest import mock

from django.test import override_settings

from mutint_jobs import jobs as jobs_api

from mutint_refsniff import tasks
from mutint_refsniff.models import STATUS_CANCELLED, STATUS_QUEUED, RefsniffRun
from mutint_refsniff.tests import fake_http, fake_sketch
from mutint_refsniff.tests.fixture import DATABASE_BACKEND, RefsniffFixture
from mutint_refsniff.tests.test_fastq import records


@override_settings(TASKS=DATABASE_BACKEND)
class CancelledRunTestCase(RefsniffFixture):
    """Against the database backend, so a queued job exists to be cancelled."""

    def _queued(self):
        self.assertEqual(200, self.launch(self.stage().id).status_code)
        run = RefsniffRun.objects.get()
        self.assertEqual(STATUS_QUEUED, run.status)
        return run

    def test_a_run_cancelled_before_it_starts_runs_nothing(self):
        run = self._queued()
        job = jobs_api.for_user(self.owner).get(task_result_id=run.task_result_id)
        self.assertTrue(job.cancellable)
        jobs_api.request_cancel(job, by=self.owner)

        with fake_sketch.patched() as run_tool, fake_http.patched() as get:
            self.assertIsNone(tasks.run_refsniff.call(None, run.pk))

        run.refresh_from_db()
        self.assertEqual(STATUS_CANCELLED, run.status)
        self.assertEqual([], run_tool.calls)
        self.assertEqual([], get.taxids)
        self.assertFalse(os.path.exists(run.directory()))

    def test_a_cancel_while_sendsketch_runs_is_the_tools_own_answer(self):
        """`run_tool` polls the flag while the process runs and raises `Cancelled`; the
        task's job is to record that as a cancellation rather than a failure."""
        run = self._queued()
        job = jobs_api.for_user(self.owner).get(task_result_id=run.task_result_id)

        asked = []

        def is_cancelled(_task_result_id):
            asked.append(True)
            # Not at entry, so the run starts; then yes, which is what `run_tool` sees.
            return len(asked) > 1

        with fake_sketch.patched() as run_tool, fake_http.patched(), \
                mock.patch.object(tasks.jobs, "is_cancelled", side_effect=is_cancelled):
            self.assertIsNone(tasks.run_refsniff.call(None, run.pk))

        run.refresh_from_db()
        self.assertEqual(STATUS_CANCELLED, run.status)
        self.assertEqual("", run.error)
        # It got as far as the command line and no further.
        self.assertEqual(1, len(run_tool.calls))
        self.assertFalse(os.path.exists(run.directory()))
        self.assertTrue(job.cancellable)

    def test_a_cancel_during_the_fetch_lands_between_chunks_and_sketches_nothing(self):
        rows = {"SRR2584863": [fake_http.ROW()]}
        with fake_http.patched(rows):
            self.assertEqual(200, self.launch("", accession="SRR2584863").status_code)
        run = RefsniffRun.objects.get()
        self.assertEqual(STATUS_QUEUED, run.status)

        # Not cancelled at entry, and cancelled once the first chunk has been asked about.
        asked = []

        def is_cancelled(_task_result_id):
            asked.append(True)
            return len(asked) > 1

        files = {"SRR2584863_1.fastq.gz": gzip.compress(records(50))}
        with fake_sketch.patched() as run_tool, fake_http.patched(rows, files, chunk=16), \
                mock.patch.object(tasks.jobs, "is_cancelled", side_effect=is_cancelled):
            self.assertIsNone(tasks.run_refsniff.call(None, run.pk))

        run.refresh_from_db()
        self.assertEqual(STATUS_CANCELLED, run.status)
        self.assertEqual([], run_tool.calls)
        self.assertFalse(os.path.exists(run.directory()))
