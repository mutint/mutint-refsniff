"""One identification run: which reads were sampled, what NCBI said, and the answer.

A row exists because the work outlives the request that asked for it -- a BLAST search is
minutes -- and because what NCBI answered is the product: a table of candidate genomes a
person reads and picks from. `django_tasks_db` records a status and a traceback, which is
a queue's record of a job rather than the experiment's record of an analysis.

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

QUERY_FILENAME = "query.fasta"


class RefsniffRun(models.Model):
    """One launch: a sample of reads, and what BLAST made of them."""

    experiment = models.ForeignKey("mutint_experiment.Experiment",
                                   on_delete=models.CASCADE, related_name="refsniff_runs")
    # SET_NULL: deleting a person must not delete the record of an analysis.
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # The basename dropped, for a person to read. Not a path: nothing resolves it.
    read_file = models.CharField(max_length=255)
    # What the page asked for, and what was actually taken -- fewer when the file, or the
    # slice of it the page uploaded, held fewer records than asked.
    reads_requested = models.PositiveIntegerField()
    reads_sampled = models.PositiveIntegerField(default=0)
    bases_sampled = models.BigIntegerField(default=0)

    # Who NCBI may contact about this search: the launcher's account address, as the form
    # showed it, or the deployment's fallback. Recorded per run because it was sent per run.
    contact_email = models.CharField(max_length=254, blank=True)
    # NCBI's request id, so the run's log and a person can find the search on NCBI's side.
    blast_rid = models.CharField(max_length=64, blank=True)
    # `[{accession, title, organism, reads, fraction, mean_identity}]`, most reads first.
    blast_hits = models.JSONField(default=list)
    # Something worth reading about a run that finished: that the search found nothing.
    note = models.TextField(blank=True)

    # The top hit. Blank when the search answered nothing.
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

    def query_path(self):
        """The sampled reads as FASTA with numbered ids, which is what BLAST is sent."""
        return os.path.join(self.directory(), QUERY_FILENAME)

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
