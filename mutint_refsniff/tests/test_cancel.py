"""Stopping a run before it starts, and while NCBI is waited on."""

import os
from unittest import mock

from django.test import override_settings

from mutint_jobs import jobs as jobs_api

from mutint_refsniff import tasks
from mutint_refsniff.models import STATUS_CANCELLED, STATUS_QUEUED, RefsniffRun
from mutint_refsniff.tests import fake_blast
from mutint_refsniff.tests.fixture import DATABASE_BACKEND, RefsniffFixture


@override_settings(TASKS=DATABASE_BACKEND)
class CancelledRunTestCase(RefsniffFixture):
    """Against the database backend, so a queued job exists to be cancelled."""

    def _queued(self):
        self.assertEqual(200, self.launch(self.stage().id).status_code)
        run = RefsniffRun.objects.get()
        self.assertEqual(STATUS_QUEUED, run.status)
        return run

    def test_a_run_cancelled_before_it_starts_asks_ncbi_nothing(self):
        run = self._queued()
        job = jobs_api.for_user(self.owner).get(task_result_id=run.task_result_id)
        self.assertTrue(job.cancellable)
        jobs_api.request_cancel(job, by=self.owner)

        with fake_blast.patched() as (http, _clock):
            self.assertIsNone(tasks.run_refsniff.call(None, run.pk))

        run.refresh_from_db()
        self.assertEqual(STATUS_CANCELLED, run.status)
        self.assertEqual([], http.requests)
        self.assertFalse(os.path.exists(run.directory()))

    def test_a_cancel_during_the_wait_lands_within_one_slice(self):
        run = self._queued()
        http = fake_blast.FakeHttp(forever=True)
        clock = fake_blast.FakeClock()

        # Not cancelled at entry (the fake clock has not moved), and cancelled the moment
        # the wait has slept once.
        def is_cancelled(_task_result_id):
            return clock.now > 0

        with fake_blast.patched(http, clock), \
                mock.patch.object(tasks.jobs, "is_cancelled", side_effect=is_cancelled):
            self.assertIsNone(tasks.run_refsniff.call(None, run.pk))

        run.refresh_from_db()
        self.assertEqual(STATUS_CANCELLED, run.status)
        self.assertEqual(1, len(http.put_data))
        self.assertEqual(0, http.status_calls)
        self.assertLessEqual(clock.now, 2 * tasks.blast.SLICE_SECONDS)
        self.assertFalse(os.path.exists(run.directory()))
