"""Identifying the reference: one BLAST search, on a worker.

**The argument is a primary key, not a model**, and a failure **re-raises** after recording
itself, the contract every task in the suite states: the row is what a person reads and the
queue's own record is what says a worker tried and could not. A cancellation is not a
failure and is not re-raised. "No hits" is not a failure either: the search ran and answered.
"""

import logging
import os
import shutil

from django.tasks import task
from django.utils import timezone

from mutint_jobs import jobs, logs
from mutint_sample import ncbi

from mutint_refsniff import blast
from mutint_refsniff.models import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_FINISHED,
    STATUS_RUNNING,
    RefsniffRun,
)

logger = logging.getLogger("mutint_refsniff.tasks")


def _queue_id(context, run):
    """This task's own queue result id -- from the context, because the view writes the
    row's copy *after* `enqueue` returns, and an immediate backend runs the task inside it."""
    from_context = getattr(getattr(context, "task_result", None), "id", None)
    return str(from_context) if from_context else (run.task_result_id or "")


def _discard_files(run):
    """The sampled reads. Every ending calls this: the row holds the answer, and a few
    hundred reads are not worth keeping beside it."""
    shutil.rmtree(run.directory(), ignore_errors=True)


def _cancelled(run, log=None):
    run.status = STATUS_CANCELLED
    run.error = ""
    run.finished_at = timezone.now()
    if log is not None:
        run.log = log
    run.save(update_fields=["status", "error", "finished_at", "log"])
    _discard_files(run)
    logger.info("refsniff run %s cancelled", run.pk)


def _fail(run, message, log=None):
    run.status = STATUS_FAILED
    run.error = message
    run.finished_at = timezone.now()
    if log is not None:
        run.log = log
    run.save(update_fields=["status", "error", "finished_at", "log"])
    _discard_files(run)


def _tail(queue_id, run):
    text, _truncated = logs.read_tail(queue_id)
    return run.truncated_log(text)


@task(takes_context=True)
def run_refsniff(context, run_id):
    """Identify the reference for one `RefsniffRun`."""
    run = RefsniffRun.objects.filter(pk=run_id).select_related("experiment").first()
    if run is None:
        logger.info("refsniff run %s is gone; nothing to do", run_id)
        return None

    queue_id = _queue_id(context, run)
    if jobs.is_cancelled(queue_id):
        _cancelled(run)
        return None

    run.status = STATUS_RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    if not os.path.isfile(run.query_path()):
        # Every ending discards the files, so a run handed to a worker twice has nothing
        # to run on. A sentence rather than a traceback out of `open`.
        _fail(run, "This run's sampled reads are no longer on disk. Launch it again.")
        raise RuntimeError("run %s has no sampled reads" % run.pk)

    with logs.open_log(queue_id) as log:
        logs.write(log, "%d reads (%d bases) sampled from %s"
                   % (run.reads_sampled, run.bases_sampled, run.read_file))
        with open(run.query_path(), "r", encoding="utf-8", errors="replace") as handle:
            fasta = handle.read()
        try:
            result = blast.run(
                fasta, run.reads_sampled, http=blast.default_http, email=run.contact_email,
                check_cancelled=lambda: jobs.check_cancelled(
                    queue_id, "This run was cancelled while it waited for NCBI."),
                log_write=lambda text: logs.write(log, text))
        except jobs.JobCancelled:
            _cancelled(run, log=_tail(queue_id, run))
            return None
        except blast.BlastError as refused:
            message = ncbi.redact(refused)
            logs.write(log, "BLAST failed: %s" % message)
            _fail(run, message, log=_tail(queue_id, run))
            raise RuntimeError("refsniff run %s: %s" % (run.pk, message))

    run.blast_rid = result["rid"]
    run.blast_hits = result["hits"]
    run.note = "" if result["hits"] else "BLAST found no matching genome."
    if result["hits"]:
        run.best_accession = result["hits"][0]["accession"]
        run.best_organism = result["hits"][0]["organism"]
    run.status = STATUS_FINISHED
    run.log = _tail(queue_id, run)
    run.error = ""
    run.finished_at = timezone.now()
    run.save(update_fields=["blast_rid", "blast_hits", "note", "best_accession",
                            "best_organism", "status", "log", "error", "finished_at"])
    _discard_files(run)
    logger.info("refsniff run %s finished: %s", run.pk, run.best_organism or "no hits")
    return run.best_accession or None
