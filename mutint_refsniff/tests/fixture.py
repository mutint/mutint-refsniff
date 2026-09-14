"""The setup every view and task test shares: a signed-in owner, a store of their own, an
experiment with **no** reference, and a scripted NCBI (`fake_blast.patched`)."""

import gzip
import json
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from mutint_common import store
from mutint_experiment.models import Project
from mutint_import import staging

from mutint_refsniff.tests.test_fastq import records

DATABASE_BACKEND = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}


class RefsniffFixture(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="owner", email="o@e.com", is_active=True)
        self.client.force_login(self.owner)

        self.store = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store, True)
        patcher = override_settings(MUTINT_STORE_DIR=self.store,
                                    MUTINT_NCBI_EMAIL="ops@example.org")
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.project = Project.objects.create(name="p", user=self.owner)
        from mutint_experiment.views import _create_experiment
        self.experiment = _create_experiment(self.project, "e", self.owner)

    def stage(self, data=None, name="reads.fastq", user=None, experiment=None, extra=None):
        """A staging session holding one file (and `extra` more), as the page would leave it."""
        data = records(10) if data is None else data
        if name.endswith(".gz") and not data.startswith(b"\x1f\x8b"):
            data = gzip.compress(data)
        files = [{"path": name, "size": len(data)}] + [
            {"path": n, "size": len(d)} for n, d in (extra or [])]
        session = staging.open_session(
            user or self.owner, experiment or self.experiment, "mutint_refsniff", files)
        root = store.ensure_dir(store.staging_dir(session.id))
        with open(os.path.join(root, name), "wb") as handle:
            handle.write(data)
        for extra_name, extra_data in (extra or []):
            with open(os.path.join(root, extra_name), "wb") as handle:
                handle.write(extra_data)
        return session

    def launch(self, upload_id, reads="200", email="o@e.com", experiment_id=None):
        body = {"upload_id": str(upload_id), "reads": reads, "email": email}
        return self.client.post(
            "/refsniff/launch?experiment_id=%s" % (
                self.experiment.id if experiment_id is None else experiment_id),
            data=json.dumps(body), content_type="application/json")
