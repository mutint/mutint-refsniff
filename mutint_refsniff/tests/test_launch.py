"""What the launch endpoint refuses, what the page offers, and the run list."""

import os

from django.contrib.auth.models import User
from django.test import override_settings

from mutint_common import store
from mutint_import.models import STATE_FAILED, STATE_FINALIZED, UploadSession
from mutint_import.tests import breseq_fixture
from mutint_jobs.models import Job

from mutint_refsniff.models import STATUS_QUEUED, RefsniffRun
from mutint_refsniff.tests import fake_blast
from mutint_refsniff.tests.fixture import DATABASE_BACKEND, RefsniffFixture


def establish_reference(experiment):
    from mutint_import import reference_store
    sequences = [("ref", breseq_fixture.SEQUENCE_A)]
    reference_store.establish_or_check(experiment, breseq_fixture.gff3_text(sequences),
                                       sequences)


class PageTestCase(RefsniffFixture):
    def test_the_page_offers_the_form_and_the_strip(self):
        response = self.client.get("/refsniff/?experiment_id=%s" % self.experiment.id)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="refsniff-dropzone"')
        self.assertContains(response, "sent to NCBI")
        self.assertContains(response, "sweetalert")
        # The strip, with this tab active and the type tabs beside it.
        self.assertContains(response, "Identify Reference from Reads")
        self.assertContains(response, "Reference Sequence")

    def test_with_a_reference_the_form_is_replaced_by_a_banner(self):
        establish_reference(self.experiment)
        response = self.client.get("/refsniff/?experiment_id=%s" % self.experiment.id)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has a reference genome")
        self.assertNotContains(response, 'id="refsniff-dropzone"')

    def test_a_reader_gets_the_run_list_and_no_form(self):
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        from mutint_experiment.permissions import grant_project_access
        grant_project_access(self.project, reader, "read")
        self.client.force_login(reader)
        response = self.client.get("/refsniff/?experiment_id=%s" % self.experiment.id)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="refsniff-dropzone"')
        self.assertContains(response, "read access")

    def test_signed_out_is_403(self):
        self.client.logout()
        response = self.client.get("/refsniff/?experiment_id=%s" % self.experiment.id)
        self.assertEqual(response.status_code, 403)

    def test_the_email_box_is_prefilled_from_the_account_then_the_deployment(self):
        response = self.client.get("/refsniff/?experiment_id=%s" % self.experiment.id)
        self.assertContains(response, 'id="refsniff-email"')
        self.assertContains(response, 'value="o@e.com"')
        self.assertContains(response, "/accounts/email/")

        self.owner.email = ""
        self.owner.save(update_fields=["email"])
        response = self.client.get("/refsniff/?experiment_id=%s" % self.experiment.id)
        self.assertContains(response, 'value="ops@example.org"')

        with override_settings(MUTINT_NCBI_EMAIL=""):
            response = self.client.get("/refsniff/?experiment_id=%s" % self.experiment.id)
        self.assertContains(response, 'value=""')


class LaunchRefusalTestCase(RefsniffFixture):
    def test_signed_out(self):
        session = self.stage()
        self.client.logout()
        self.assertEqual(403, self.launch(session.id).status_code)

    def test_a_reader_cannot_launch(self):
        session = self.stage()
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        from mutint_experiment.permissions import grant_project_access
        grant_project_access(self.project, reader, "read")
        self.client.force_login(reader)
        self.assertEqual(403, self.launch(session.id).status_code)
        self.assertEqual(0, RefsniffRun.objects.count())

    def test_a_locked_experiment_refuses(self):
        session = self.stage()
        self.experiment.lock(self.owner)
        self.assertEqual(403, self.launch(session.id).status_code)

    def test_an_experiment_with_a_reference_refuses(self):
        establish_reference(self.experiment)
        session = self.stage()
        response = self.launch(session.id)
        self.assertEqual(409, response.status_code)
        self.assertIn("already has a reference", response.json()["error"])

    def test_the_reads_box_is_checked_before_the_file_and_names_its_field(self):
        session = self.stage()
        for bad in ("99", "1001", "x"):
            response = self.launch(session.id, reads=bad)
            self.assertEqual(400, response.status_code, bad)
            self.assertEqual("reads", response.json()["field"])
        # The session is untouched: a typo in a box costs nothing.
        session.refresh_from_db()
        self.assertEqual("open", session.state)

    def test_a_missing_or_bad_email_is_refused_and_names_its_field(self):
        session = self.stage()
        for bad in ("", "  ", "nobody", "a@"):
            response = self.launch(session.id, email=bad)
            self.assertEqual(400, response.status_code, bad)
            self.assertEqual("email", response.json()["field"])
        session.refresh_from_db()
        self.assertEqual("open", session.state)
        self.assertEqual(0, RefsniffRun.objects.count())

    def test_no_upload(self):
        self.assertEqual(400, self.launch("").status_code)

    def test_another_experiments_upload_is_refused(self):
        from mutint_experiment.views import _create_experiment
        other = _create_experiment(self.project, "other", self.owner)
        session = self.stage(experiment=other)
        self.assertEqual(409, self.launch(session.id).status_code)

    def test_two_files_are_refused_and_the_session_abandoned(self):
        session = self.stage(extra=[("more.fastq", b"@r\nA\n+\nI\n")])
        response = self.launch(session.id)
        self.assertEqual(400, response.status_code)
        self.assertIn("exactly one", response.json()["error"])
        session.refresh_from_db()
        self.assertEqual(STATE_FAILED, session.state)
        self.assertFalse(os.path.exists(store.staging_dir(session.id)))

    def test_a_file_not_named_fastq_is_refused(self):
        session = self.stage(name="reads.fasta")
        response = self.launch(session.id)
        self.assertEqual(400, response.status_code)
        self.assertIn("not named like a FASTQ", response.json()["error"])

    def test_fasta_content_is_refused(self):
        session = self.stage(data=b">seq\nACGT\n", name="reads.fastq")
        response = self.launch(session.id)
        self.assertEqual(400, response.status_code)
        self.assertIn("FASTA", response.json()["error"])
        self.assertEqual(0, RefsniffRun.objects.count())

    @override_settings(TASKS=DATABASE_BACKEND)
    def test_one_run_at_a_time(self):
        first = self.launch(self.stage().id)
        self.assertEqual(200, first.status_code)
        run = RefsniffRun.objects.get()
        self.assertEqual(STATUS_QUEUED, run.status)

        session = self.stage()
        second = self.launch(session.id)
        self.assertEqual(409, second.status_code)
        self.assertIn("already in progress", second.json()["error"])
        self.assertEqual(1, RefsniffRun.objects.count())
        # Refused before the session was touched.
        session.refresh_from_db()
        self.assertEqual("open", session.state)


class LaunchTestCase(RefsniffFixture):
    def test_the_happy_path(self):
        session = self.stage(name="reads.fastq.gz")
        with fake_blast.patched():
            response = self.launch(session.id, reads="100")
        self.assertEqual(200, response.status_code, response.content)
        body = response.json()
        run = RefsniffRun.objects.get(pk=body["run_id"])
        self.assertEqual("reads.fastq.gz", run.read_file)
        # Ten records were staged, so fewer than asked were sampled, and the row says so.
        self.assertEqual((100, 10, 100),
                         (run.reads_requested, run.reads_sampled, run.bases_sampled))
        self.assertEqual(self.owner, run.created_by)
        self.assertEqual("o@e.com", run.contact_email)
        # The drop is gone, the session spent, and the job is on /jobs/ with a Cancel.
        session.refresh_from_db()
        self.assertEqual(STATE_FINALIZED, session.state)
        self.assertFalse(os.path.exists(store.staging_dir(session.id)))
        job = Job.objects.get(task_result_id=run.task_result_id)
        self.assertTrue(job.cancellable)
        self.assertEqual(self.owner, job.user)
        self.assertIn("reads.fastq.gz", job.label)
        self.assertEqual(1, len(body["runs"]))

    def test_the_runs_endpoint(self):
        with fake_blast.patched():
            self.launch(self.stage().id)
        response = self.client.get("/refsniff/runs?experiment_id=%s" % self.experiment.id)
        self.assertEqual(200, response.status_code)
        rows = response.json()["runs"]
        self.assertEqual(1, len(rows))
        for key in ("status", "queue_status", "blast_hits", "best_accession", "log_url",
                    "read_file", "note"):
            self.assertIn(key, rows[0])
        self.client.logout()
        self.assertEqual(403, self.client.get(
            "/refsniff/runs?experiment_id=%s" % self.experiment.id).status_code)

    @override_settings(TASKS=DATABASE_BACKEND)
    def test_deleting_an_unfinished_run_is_refused(self):
        self.launch(self.stage().id)
        run = RefsniffRun.objects.get()
        response = self.client.post("/refsniff/run/%d/delete" % run.pk)
        self.assertEqual(409, response.status_code)
        self.assertTrue(RefsniffRun.objects.filter(pk=run.pk).exists())

    def test_deleting_a_finished_run_removes_its_row(self):
        with fake_blast.patched():
            self.launch(self.stage().id)
        run = RefsniffRun.objects.get()
        response = self.client.post("/refsniff/run/%d/delete" % run.pk)
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["runs"])
        self.assertFalse(RefsniffRun.objects.exists())
