"""One identification run: which reads were sketched, what came back, and the answer.

A row exists because the work outlives the request that asked for it -- an ENA fetch is tens
of seconds and a sketch a few more -- and because the table that comes back is the product: a
ranked list of candidate genomes a person reads and picks from. `django_tasks_db` records a
status and a traceback, which is a queue's record of a job rather than the experiment's
record of an analysis.

`status` here is what *this* believes; `task_result_id` is how to ask the queue. They disagree
in exactly one useful way -- a row that says `queued` while the queue says the task has never
been picked up means **no worker is running** -- which is the design's easiest failure and is
otherwise indistinguishable from a slow start. mutint-breseq's model states the same.
"""

import logging
import os
import shutil

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from mutint_common import store

from mutint_refsniff.sketch import SKETCH_FILENAME

logger = logging.getLogger("mutint_refsniff.models")

COMPONENT = "mutint_refsniff"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

STATUS_CHOICES = [
    (STATUS_QUEUED, "Queued"),
    (STATUS_RUNNING, "Running"),
    (STATUS_FINISHED, "Finished"),
    (STATUS_FAILED, "Failed"),
    (STATUS_CANCELLED, "Cancelled"),
]

FINISHED_STATUSES = (STATUS_FINISHED, STATUS_FAILED, STATUS_CANCELLED)

# The job log is the whole account; this column holds its tail, which outlives the `Job`
# row `./mutint reap_jobs` may remove.
MAX_LOG_CHARS = 20000

ENA_BROWSER_URL = "https://www.ebi.ac.uk/ena/browser/view/%s"


class RefsniffRun(models.Model):
    """One launch: a head of reads, and what the sketch server made of it."""

    experiment = models.ForeignKey("mutint_experiment.Experiment",
                                   on_delete=models.CASCADE, related_name="refsniff_runs")
    # SET_NULL: deleting a person must not delete the record of an analysis.
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # The basename dropped, for a person to read -- or, for a run named by accession, the
    # ENA filename the task fetches (`SRR..._1.fastq.gz`). Not a path: nothing resolves it,
    # but it is the name the file keeps inside the run's directory, because sendsketch
    # chooses gzip by suffix.
    read_file = models.CharField(max_length=255)
    # The SRA accession typed, when the reads come from ENA rather than a drop; blank for a
    # drop. `read_url` is where `sra_fetch.resolve` found the file, recorded at launch so
    # the task fetches what was found and does not ask ENA for a second opinion.
    accession = models.CharField(max_length=32, blank=True, default="")
    read_url = models.CharField(max_length=512, blank=True, default="")

    # What sendsketch reported it loaded. Not what was asked for -- nothing is asked for; the
    # head is whatever fitted in `fastq.HEAD_BYTES` and these are what was in it.
    reads_sketched = models.PositiveIntegerField(default=0)
    bases_sketched = models.BigIntegerField(default=0)

    # `[{name, taxid, accession, title, taxonomy, ani, completeness, contamination, matches,
    # unique, draft, assembly, import_accession}]`, in the order the sketch server ranked
    # them. See `sketch.parse`.
    hits = models.JSONField(default=list)
    # Something worth reading about a run that finished: that nothing matched.
    note = models.TextField(blank=True)

    # The top hit, as the page's headline. `best_accession` is what **Use as reference**
    # would import for it -- the assembly where there is one.
    best_accession = models.CharField(max_length=64, blank=True)
    best_organism = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    # django.tasks' own result id. Blank when the enqueue itself failed.
    task_result_id = models.CharField(max_length=64, blank=True)
    log = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return "RefsniffRun %s (%s, %s)" % (self.pk, self.read_file, self.status)

    @property
    def is_finished(self):
        return self.status in FINISHED_STATUSES

    def directory(self):
        """This run's own work area. Everything it writes lives under here."""
        return store.component_dir(COMPONENT, self.pk)

    def reads_path(self):
        """The head sendsketch reads: the drop moved here, or the ENA file fetched here.

        One path for both ways in, under the name the file arrived with -- which matters,
        because gzip is chosen by suffix all the way down.
        """
        return os.path.join(self.directory(), self.read_file)

    def sketch_path(self):
        """Where sendsketch writes its JSON."""
        return os.path.join(self.directory(), SKETCH_FILENAME)

    def accession_url(self):
        """ENA's browser page for the accession, or "" for a drop."""
        return ENA_BROWSER_URL % self.accession if self.accession else ""

    def truncated_log(self, text):
        if len(text) <= MAX_LOG_CHARS:
            return text
        return "…(earlier output trimmed)…\n" + text[-MAX_LOG_CHARS:]


@receiver(post_delete, sender=RefsniffRun)
def _remove_run_directory(sender, instance, **kwargs):
    """A run's files go when its row does.

    `store.component_dir` is deliberately not reaped by core, so this receiver is the whole
    lifecycle -- and, since the FK above cascades, what makes deleting an experiment reach
    the files.
    """
    try:
        shutil.rmtree(store.component_dir(COMPONENT, instance.pk), ignore_errors=True)
    except (ValueError, TypeError):
        logger.debug("no directory to remove for an unsaved RefsniffRun")
