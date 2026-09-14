"""Identifying the reference: one sketch, on a worker.

**The argument is a primary key, not a model**, and a failure **re-raises** after recording
itself, the contract every task in the suite states: the row is what a person reads and the
queue's own record is what says a worker tried and could not. A cancellation is not a
failure and is not re-raised. "No hits" is not a failure either: the sketch ran and answered.

The order of the three steps is the order of what each costs. The tool is looked for
**first**, before anything is fetched, because a missing `sendsketch.sh` should not cost a
16 MB download to discover. Then the reads -- already on disk for a drop, fetched from ENA
for an accession. Then one call to sendsketch, and then one NCBI Datasets lookup per hit,
which is the only step whose failure leaves the run finished anyway (see `assemblies`).
"""

import logging
import os
import shutil
import subprocess

from django.tasks import task
from django.utils import timezone

from mutint_common import store
from mutint_common.tools import ToolMissing
from mutint_import import sra_fetch
from mutint_jobs import jobs, logs, processes
from mutint_sample import ncbi

from mutint_refsniff import assemblies, ena, fastq, sketch
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
    """The head and the sketch. Every ending calls this: the row holds the answer, and
    16 MB of somebody else's reads are not worth keeping beside it."""
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


def _fetch_head(run, log, queue_id):
    """For a run named by accession: the head of its file from ENA, where a drop already is.

    The drop path moves its file into the run's directory in the view, because the bytes are
    already on local disk. Here the bytes are at ENA, so the fetch happens on the worker,
    inside the job log, with the cancel poll between chunks. Raises `sra_fetch.FetchError`
    for anything ENA did or did not do, and `processes.Cancelled` for a cancel that landed
    mid-fetch.
    """
    store.ensure_dir(run.directory())
    ena.fetch_head(run.read_url, run.reads_path(), fastq.HEAD_BYTES, run.read_file,
                   is_cancelled=lambda: jobs.is_cancelled(queue_id),
                   report=lambda text: logs.write(log, text))
    if not fastq.has_a_record(run.reads_path()):
        raise sra_fetch.FetchError(
            "%s holds no complete FASTQ record in its first %d MB."
            % (run.read_file, fastq.HEAD_BYTES // (1024 * 1024)))


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

    # Before anything is downloaded: the sentence names `tools.txt` and the command that
    # installs it, which is the whole of what an operator needs.
    try:
        sendsketch = sketch.sendsketch_path()
    except ToolMissing as missing:
        _fail(run, str(missing))
        raise

    with logs.open_log(queue_id) as log:
        if run.accession:
            try:
                _fetch_head(run, log, queue_id)
            except processes.Cancelled:
                _cancelled(run, log=_tail(queue_id, run))
                return None
            except sra_fetch.FetchError as refused:
                message = ncbi.redact(refused)
                logs.write(log, "ENA fetch failed: %s" % message)
                _fail(run, message, log=_tail(queue_id, run))
                raise RuntimeError("refsniff run %s: %s" % (run.pk, message))

        if not os.path.isfile(run.reads_path()):
            # Every ending discards the files, so a run handed to a worker twice has
            # nothing to run on. A sentence rather than a traceback out of the tool.
            _fail(run, "This run's reads are no longer on disk. Launch it again.")
            raise RuntimeError("run %s has no reads" % run.pk)

        # Before the tool, not after: a reader scrolling a failed-looking log should meet the
        # explanation above the traceback it explains. Says nothing when there is nothing to
        # explain -- see `sketch.truncation_note`.
        note = sketch.truncation_note(run.read_file, os.path.getsize(run.reads_path()),
                                      fastq.HEAD_BYTES)
        if note:
            logs.write(log, note)

        argv = sketch.build_argv(sendsketch, run.reads_path(), run.sketch_path())
        try:
            returncode = processes.run_tool(
                argv, log, env=sketch.tool_environment(), timeout=sketch.timeout_seconds(),
                is_cancelled=lambda: jobs.is_cancelled(queue_id), what="sendsketch")
        except processes.Cancelled:
            _cancelled(run, log=_tail(queue_id, run))
            return None
        except subprocess.TimeoutExpired:
            message = ("sendsketch did not finish within %d seconds and was stopped."
                       % sketch.timeout_seconds())
            _fail(run, message, log=_tail(queue_id, run))
            raise RuntimeError(message)
        except OSError as failed:
            message = "sendsketch could not be started: %s" % failed
            _fail(run, message, log=_tail(queue_id, run))
            raise

        if returncode != 0:
            # The log is the account. A truncated gzip member puts a ZLIB traceback in it and
            # still exits 0; a nonzero status is the tool saying it could not answer.
            message = "sendsketch exited with status %d." % returncode
            _fail(run, message, log=_tail(queue_id, run))
            raise RuntimeError(message)

        try:
            with open(run.sketch_path(), "r", encoding="utf-8", errors="replace") as handle:
                result = sketch.parse(handle.read())
        except OSError:
            message = "sendsketch wrote no result file."
            _fail(run, message, log=_tail(queue_id, run))
            raise RuntimeError(message)
        except sketch.SketchError as bad:
            _fail(run, str(bad), log=_tail(queue_id, run))
            raise RuntimeError("refsniff run %s: %s" % (run.pk, bad))

        logs.write(log, "Sketched %d reads (%d bases) from %s%s; %d genomes matched."
                   % (result["reads"], result["bases"], run.read_file,
                      " (%s)" % run.accession if run.accession else "",
                      len(result["hits"])))
        # The assemblies are a convenience on the answer, not the answer: a lookup that
        # fails leaves the row intact and the run finished.
        assemblies.annotate(result["hits"], report=lambda text: logs.write(log, text))
        output = _tail(queue_id, run)

    hits = result["hits"]
    run.reads_sketched = result["reads"]
    run.bases_sketched = result["bases"]
    run.hits = hits
    run.note = "" if hits else "The sketch matched no genome in RefSeq."
    if hits:
        run.best_accession = hits[0]["import_accession"]
        run.best_organism = hits[0]["name"]
    run.status = STATUS_FINISHED
    run.log = output
    run.error = ""
    run.finished_at = timezone.now()
    run.save(update_fields=["reads_sketched", "bases_sketched", "hits", "note",
                            "best_accession", "best_organism", "status", "log", "error",
                            "finished_at"])
    _discard_files(run)
    logger.info("refsniff run %s finished: %s", run.pk, run.best_organism or "no hits")
    return run.best_accession or None
