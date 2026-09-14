"""What the launch endpoint refuses, what the page offers, and the run list."""

import gzip
import os

from django.contrib.auth.models import User
from django.test import override_settings

from mutint_common import store
from mutint_import.models import STATE_FAILED, STATE_FINALIZED
from mutint_import.tests import breseq_fixture
from mutint_jobs.models import Job

from mutint_refsniff.models import STATUS_FINISHED, STATUS_QUEUED, RefsniffRun
from mutint_refsniff.tests import fake_http, fake_sketch
from mutint_refsniff.tests.fixture import DATABASE_BACKEND, RefsniffFixture
from mutint_refsniff.tests.test_fastq import records


def establish_reference(experiment):
    from mutint_import import reference_store
    sequences = [("ref", breseq_fixture.SEQUENCE_A)]
    reference_store.establish_or_check(experiment, breseq_fixture.gff3_text(sequences),
                                       sequences)


class PageTestCase(RefsniffFixture):
    def _get(self):
        with fake_sketch.patched():
            return self.client.get("/refsniff/?experiment_id=%s" % self.experiment.id)

    def test_the_page_offers_the_form_and_the_strip(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="refsniff-dropzone"')
        self.assertContains(response, 'id="refsniff-accession"')
        self.assertContains(response, "sweetalert")
        # The strip, with this tab active and the type tabs beside it.
        self.assertContains(response, "Identify Reference from Reads")
        self.assertContains(response, "Reference Sequence")

    def test_the_page_says_what_leaves_the_deployment(self):
        """Core's rule is that sequence does not leave; a sketch is a bend in it, and the
        page has to say which of the two this is in as many words."""
        response = self._get()
        self.assertContains(response, "A sketch of the reads leaves this deployment")
        self.assertContains(response, "k-mer hashes")
        self.assertNotContains(response, "BLAST")

    def test_the_form_has_no_read_count_and_no_contact_email(self):
        """sendsketch reads the whole head, so there is nothing to ask for; and JGI's sketch
        server wants no contact address, so there is nobody to name."""
        response = self._get()
        self.assertNotContains(response, 'id="refsniff-reads"')
        self.assertNotContains(response, 'id="refsniff-email"')

    def test_without_the_tool_the_page_says_so(self):
        with fake_sketch.patched(no_tool=True):
            response = self.client.get("/refsniff/?experiment_id=%s" % self.experiment.id)
        self.assertContains(response, "This cannot run here")
        self.assertContains(response, "tools.txt")

    def test_with_a_reference_the_form_is_replaced_by_a_banner(self):
        establish_reference(self.experiment)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has a reference genome")
        self.assertNotContains(response, 'id="refsniff-dropzone"')

    def test_a_reader_gets_the_run_list_and_no_form(self):
        reader = User.objects.create(username="reader", email="r@e.com", is_active=True)
        from mutint_experiment.permissions import grant_project_access
        grant_project_access(self.project, reader, "read")
        self.client.force_login(reader)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="refsniff-dropzone"')
        self.assertContains(response, "read access")

    def test_signed_out_is_403(self):
        self.client.logout()
        response = self._get()
        self.assertEqual(response.status_code, 403)


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

    def test_no_upload_and_no_accession(self):
        response = self.launch("")
        self.assertEqual(400, response.status_code)
        self.assertIn("accession", response.json()["error"])

    def test_a_file_and_an_accession_together_are_refused(self):
        session = self.stage()
        with fake_http.patched({"SRR2584863": [fake_http.ROW()]}):
            response = self.launch(session.id, accession="SRR2584863")
        self.assertEqual(400, response.status_code)
        self.assertEqual("accession", response.json()["field"])
        self.assertIn("not both", response.json()["error"])
        # Refused before either was looked at: the session stays open to try again.
        session.refresh_from_db()
        self.assertEqual("open", session.state)
        self.assertEqual(0, RefsniffRun.objects.count())

    def test_an_unknown_accession_is_refused_by_name_and_names_its_field(self):
        with fake_http.patched({}):
            response = self.launch("", accession="SRR99999999")
        self.assertEqual(400, response.status_code)
        self.assertEqual("accession", response.json()["field"])
        self.assertIn("SRR99999999", response.json()["error"])
        self.assertEqual(0, RefsniffRun.objects.count())

    def test_a_token_of_no_sra_shape_is_refused_before_ena_is_asked(self):
        with fake_http.patched({}) as get:
            response = self.launch("", accession="NC_000913.3")
        self.assertEqual(400, response.status_code)
        self.assertEqual("accession", response.json()["field"])
        self.assertEqual([], get.files)

    def test_a_run_ena_holds_no_fastq_for_is_refused(self):
        with fake_http.patched({"SRR1": [fake_http.ROW(run="SRR1", files=[])]}):
            response = self.launch("", accession="SRR1")
        self.assertEqual(400, response.status_code)
        self.assertIn("no FASTQ", response.json()["error"])

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


class AccessionLaunchTestCase(RefsniffFixture):
    """The second way in. The immediate backend runs the task inside the launch, so the
    scripted web and the scripted sendsketch are both installed around it."""

    def _launch(self, accession, rows, files, **kwargs):
        kwargs.setdefault("assemblies", fake_sketch.ASSEMBLIES)
        with fake_sketch.patched(), fake_http.patched(rows, files, **kwargs) as get:
            response = self.launch("", accession=accession)
        return response, get

    def test_the_happy_path(self):
        reads = gzip.compress(records(10))
        response, get = self._launch(
            "srr2584863", {"SRR2584863": [fake_http.ROW()]},
            {"SRR2584863_1.fastq.gz": reads})
        self.assertEqual(200, response.status_code, response.content)
        run = RefsniffRun.objects.get(pk=response.json()["run_id"])
        # Upper-cased, resolved to read 1 of the run, and recorded with where it was found.
        self.assertEqual("SRR2584863", run.accession)
        self.assertEqual("SRR2584863_1.fastq.gz", run.read_file)
        self.assertTrue(run.read_url.startswith("https://ftp.sra.ebi.ac.uk/"))
        self.assertEqual("https://www.ebi.ac.uk/ena/browser/view/SRR2584863",
                         run.accession_url())
        # Only read 1 was fetched, and only its head was asked for.
        self.assertEqual(["SRR2584863_1.fastq.gz"], get.files)
        self.assertEqual(["bytes=0-16777215"], get.ranges)
        self.assertEqual(STATUS_FINISHED, run.status)
        self.assertEqual("GCF_000017985.1", run.best_accession)
        job = Job.objects.get(task_result_id=run.task_result_id)
        self.assertTrue(job.cancellable)
        self.assertIn("SRR2584863", job.label)
        self.assertFalse(os.path.exists(run.directory()))

    def test_a_paired_run_takes_read_one_even_when_an_orphan_file_is_listed_first(self):
        reads = gzip.compress(records(3))
        row = fake_http.ROW(run="SRR1", files=[("SRR1.fastq.gz", b"x"),
                                               ("SRR1_1.fastq.gz", reads),
                                               ("SRR1_2.fastq.gz", reads)])
        response, get = self._launch("SRR1", {"SRR1": [row]}, {"SRR1_1.fastq.gz": reads})
        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual("SRR1_1.fastq.gz", RefsniffRun.objects.get().read_file)
        self.assertEqual(["SRR1_1.fastq.gz"], get.files)

    def test_a_single_end_run_takes_its_only_file(self):
        reads = gzip.compress(records(3))
        row = fake_http.ROW(run="SRR1", files=[("SRR1.fastq.gz", reads)])
        response, _get = self._launch("SRR1", {"SRR1": [row]}, {"SRR1.fastq.gz": reads})
        self.assertEqual(200, response.status_code, response.content)
        self.assertEqual("SRR1.fastq.gz", RefsniffRun.objects.get().read_file)

    def test_an_experiment_accession_takes_its_first_run(self):
        reads = gzip.compress(records(3))
        rows = {"SRX1": [fake_http.ROW(run="SRR1"), fake_http.ROW(run="SRR2")]}
        response, get = self._launch("SRX1", rows, {"SRR1_1.fastq.gz": reads})
        self.assertEqual(200, response.status_code, response.content)
        run = RefsniffRun.objects.get()
        self.assertEqual(("SRX1", "SRR1_1.fastq.gz"), (run.accession, run.read_file))
        self.assertEqual(["SRR1_1.fastq.gz"], get.files)

    def test_the_runs_list_carries_the_accession(self):
        reads = gzip.compress(records(3))
        self._launch("SRR2584863", {"SRR2584863": [fake_http.ROW()]},
                     {"SRR2584863_1.fastq.gz": reads})
        rows = self.client.get(
            "/refsniff/runs?experiment_id=%s" % self.experiment.id).json()["runs"]
        self.assertEqual("SRR2584863", rows[0]["accession"])
        self.assertIn("ena/browser/view/SRR2584863", rows[0]["accession_url"])

    @override_settings(TASKS=DATABASE_BACKEND)
    def test_one_run_at_a_time_holds_across_the_two_ways_in(self):
        with fake_http.patched({"SRR2584863": [fake_http.ROW()]}):
            self.assertEqual(200, self.launch("", accession="SRR2584863").status_code)
        session = self.stage()
        self.assertEqual(409, self.launch(session.id).status_code)
        self.assertEqual(1, RefsniffRun.objects.count())


class LaunchTestCase(RefsniffFixture):
    def _launch(self, session):
        with fake_sketch.patched() as run_tool, \
                fake_http.patched(assemblies=fake_sketch.ASSEMBLIES):
            return self.launch(session.id), run_tool

    def test_the_happy_path(self):
        session = self.stage(name="reads.fastq.gz")
        response, run_tool = self._launch(session)
        self.assertEqual(200, response.status_code, response.content)
        body = response.json()
        run = RefsniffRun.objects.get(pk=body["run_id"])
        self.assertEqual("reads.fastq.gz", run.read_file)
        self.assertEqual(self.owner, run.created_by)
        # The drop is gone, the session spent, and the job is on /jobs/ with a Cancel.
        session.refresh_from_db()
        self.assertEqual(STATE_FINALIZED, session.state)
        self.assertFalse(os.path.exists(store.staging_dir(session.id)))
        job = Job.objects.get(task_result_id=run.task_result_id)
        self.assertTrue(job.cancellable)
        self.assertEqual(self.owner, job.user)
        self.assertIn("reads.fastq.gz", job.label)
        self.assertEqual(1, len(body["runs"]))

    @override_settings(TASKS=DATABASE_BACKEND)
    def test_the_drop_is_moved_into_the_runs_directory_before_the_job_is_queued(self):
        """The database backend, so nothing runs it: the file has to be waiting where the
        task will look, under the name it arrived with."""
        session = self.stage(name="reads.fastq.gz")
        self.assertEqual(200, self.launch(session.id).status_code)
        run = RefsniffRun.objects.get()
        self.assertTrue(os.path.isfile(run.reads_path()))
        self.assertTrue(run.reads_path().endswith("reads.fastq.gz"))
        self.assertFalse(os.path.exists(store.staging_dir(session.id)))

    def test_the_runs_endpoint(self):
        self._launch(self.stage())
        response = self.client.get("/refsniff/runs?experiment_id=%s" % self.experiment.id)
        self.assertEqual(200, response.status_code)
        rows = response.json()["runs"]
        self.assertEqual(1, len(rows))
        for key in ("status", "queue_status", "hits", "best_accession", "log_url",
                    "read_file", "note", "accession", "accession_url", "reads_sketched",
                    "bases_sketched"):
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
        self._launch(self.stage())
        run = RefsniffRun.objects.get()
        response = self.client.post("/refsniff/run/%d/delete" % run.pk)
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["runs"])
        self.assertFalse(RefsniffRun.objects.exists())
