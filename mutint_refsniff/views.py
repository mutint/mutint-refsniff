"""The tab page, the launch endpoint, and the run list the page polls.

Shaped like mutint-breseq's: function-based views, permission checked inline, hand-written
Bootstrap posting to a `@require_POST` JSON endpoint through `mutintPostJson`.
"""

import json
import logging
import os
import shutil

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

import mutint_sample.views.common
from mutint_common import store
from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_edit_experiment, experiment_lock_refusal
from mutint_import import reference_store, sra_fetch, staging
from mutint_import.accessions import AccessionError
from mutint_import.upload_session import UploadError
from mutint_jobs import jobs as jobs_api

from mutint_refsniff import ena, fastq, sketch, tasks
from mutint_refsniff.models import (
    COMPONENT,
    STATUS_QUEUED,
    STATUS_RUNNING,
    RefsniffRun,
)

logger = logging.getLogger("mutint_refsniff.views")

HEAD_BYTES = fastq.HEAD_BYTES


def _experiment_or_none(request):
    try:
        return mutint_sample.views.common.get_experiment(request)
    except (Experiment.DoesNotExist, ValueError):
        return None


def _active_run(experiment):
    """The run queued or running for this experiment, or None. There is at most one."""
    return (RefsniffRun.objects.filter(experiment=experiment,
                                       status__in=(STATUS_QUEUED, STATUS_RUNNING))
            .order_by("-created_at").first())


def _queue_status(run):
    """What the queue thinks, or "" when there is nothing to ask -- the page's only way of
    telling a job waiting its turn from one no worker will ever reach."""
    if not run.task_result_id or run.is_finished:
        return ""
    try:
        return tasks.run_refsniff.get_result(run.task_result_id).status.value
    except Exception:
        return ""


def _run_rows(experiment):
    runs = list(RefsniffRun.objects.filter(experiment=experiment))
    log_urls = jobs_api.log_urls(run.task_result_id for run in runs)
    rows = []
    for run in runs:
        rows.append({
            "id": run.pk,
            "read_file": run.read_file,
            "accession": run.accession,
            "accession_url": run.accession_url(),
            "reads_sketched": run.reads_sketched,
            "bases_sketched": run.bases_sketched,
            "note": run.note,
            "hits": run.hits or [],
            "best_accession": run.best_accession,
            "best_organism": run.best_organism,
            "status": run.status,
            "queue_status": _queue_status(run),
            "created_at": run.created_at.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "error": run.error,
            "log": run.log,
            "log_url": log_urls.get(run.task_result_id, ""),
        })
    return rows


@ensure_csrf_cookie
def refsniff(request):
    """The tab: drop a FASTQ, sketch it, find the nearest genome. Lists the runs.

    Signed in first, then the experiment, as mutint-breseq's page does: the launch creates
    data, and a signed-out visitor is told that rather than being sent to a page about an
    experiment they cannot use.
    """
    if not request.user.is_authenticated:
        return render(request, "403.html", get_user_context(request.user), status=403)

    context = get_user_context(request.user)
    try:
        experiment = mutint_sample.views.common.get_experiment(request)
    except Experiment.DoesNotExist:
        return mutint_sample.views.common.no_experiment_selected(
            request, context, logger, "refsniff")

    # Said on the page rather than only at launch: the tool is a conda package, and a
    # deployment that has not installed it should learn so before uploading 16 MB.
    tool_available, tool_missing = sketch.available()

    context.update(experiment.experiment_context())
    context.update({
        "experiment": experiment,
        "experiment_id": experiment.id,
        # The tab is hidden once there is a reference, but the URL can still be typed or
        # left open in another window; the page then says so instead of offering a form
        # whose launch would refuse.
        "has_reference": reference_store.has_reference(experiment),
        "can_launch": can_edit_experiment(request.user, experiment),
        "lock_refusal": experiment_lock_refusal(experiment),
        "tool_available": tool_available,
        "tool_missing": tool_missing,
        "busy": _active_run(experiment) is not None,
        "config": {
            "experiment_id": experiment.id,
            "component": COMPONENT,
            "head_bytes": HEAD_BYTES,
        },
        "runs": _run_rows(experiment),
    })
    return render(request, "refsniff/page.html", context)


def runs(request):
    """The run list as JSON, for the page's poll."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)
    experiment = _experiment_or_none(request)
    if experiment is None:
        return JsonResponse({"error": "Unknown experiment."}, status=404)
    return JsonResponse({"runs": _run_rows(experiment)})


def _payload(request):
    try:
        return json.loads((request.body or b"{}").decode("utf-8")) or {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _staged_files(root):
    """Every file under the staged directory, as paths."""
    found = []
    for directory, _dirs, names in os.walk(root):
        for name in names:
            found.append(os.path.join(directory, name))
    return sorted(found)


@require_POST
def launch(request):
    """Take a staged FASTQ or an SRA accession, and queue the identification.

    Body: `{upload_id | accession}` -- exactly one of the two. A drop is looked at and moved
    into the run's directory here, because the bytes are already on local disk; an accession
    is resolved here (one ENA round trip, so a typo is a 400 beside the box) and fetched by
    the task, since ENA can stall and the job log and cancel poll are there. Every refusal
    comes **before** `staging.claim`, so a bad box costs nothing and the session stays open
    to try again; a bad *file* abandons the session, since the fix is a different file.

    Gated on `can_edit_experiment`: what this leads to is the experiment's reference, which
    is as shared as a write gets, and a locked experiment must refuse it.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    experiment = _experiment_or_none(request)
    if experiment is None:
        return JsonResponse({"error": "Unknown experiment."}, status=404)
    if not can_edit_experiment(request.user, experiment):
        return JsonResponse(
            {"error": experiment_lock_refusal(experiment)
                      or "You cannot add data to this experiment."}, status=403)

    if reference_store.has_reference(experiment):
        return JsonResponse(
            {"error": "This experiment already has a reference genome, so there is nothing "
                      "to identify. Reload the page."}, status=409)

    # **One run at a time per experiment.** A second identical sketch answers the same
    # thing, and there is no sample name to supersede on, so this refuses rather than
    # cancelling the first.
    if _active_run(experiment) is not None:
        return JsonResponse(
            {"error": "A run is already in progress for this experiment. Wait for it to "
                      "finish, or cancel it on the Jobs page."}, status=409)

    payload = _payload(request)
    upload_id = (payload.get("upload_id") or "").strip()
    accession = (payload.get("accession") or "").strip().upper()
    if upload_id and accession:
        return JsonResponse({"error": "Drop a file or type an accession, not both.",
                             "field": "accession"}, status=400)
    if accession:
        return _launch_accession(request, experiment, accession)
    if not upload_id:
        return JsonResponse({"error": "Drop a FASTQ file or type an SRA accession first."},
                            status=400)
    session, error = staging.session_for(request, upload_id, COMPONENT)
    if error:
        return error
    if session.experiment_id != experiment.id:
        return JsonResponse({"error": "That upload belongs to another experiment."},
                            status=409)

    # Looked at before it is claimed: a session left open is reaped on the TTL, a claimed
    # one is this plugin's to delete, and a refusal here abandons it anyway.
    staged = _staged_files(store.staging_dir(session.id))
    if len(staged) != 1:
        staging.abandon(session)
        return JsonResponse(
            {"error": "Drop exactly one FASTQ file; %d were uploaded." % len(staged),
             "field": "upload"}, status=400)
    path = staged[0]
    name = os.path.basename(path)
    if not fastq.is_fastq_name(name):
        staging.abandon(session)
        return JsonResponse(
            {"error": "%s is not named like a FASTQ file (.fastq, .fq, .fastq.gz, .fq.gz)."
                      % name, "field": "upload"}, status=400)
    reason = fastq.sniff(path)
    if reason:
        staging.abandon(session)
        return JsonResponse({"error": "%s: %s" % (name, reason), "field": "upload"},
                            status=400)

    try:
        staging.claim(session)
    except UploadError as exc:
        return JsonResponse({"error": str(exc)}, status=409)

    run = RefsniffRun.objects.create(
        experiment=experiment,
        created_by=request.user,
        read_file=name,
        status=STATUS_QUEUED)
    # Moved rather than copied, and under its own name: sendsketch reads the whole head and
    # chooses gzip by suffix. `shutil.move` across the store is a rename when the staging
    # area and the component directory share a filesystem, which they do.
    try:
        store.ensure_dir(run.directory())
        shutil.move(path, run.reads_path())
    except OSError as exc:
        logger.exception("could not take the reads for a refsniff launch in experiment %s",
                         experiment.id)
        run.delete()
        staging.abandon(session)
        return JsonResponse({"error": "The uploaded reads could not be read: %s" % exc},
                            status=500)

    # The drop is gone by here; the head lives under the run's own directory.
    staging.close(session)
    return _enqueue(request, experiment, run, name)


def _launch_accession(request, experiment, accession):
    """The accession way in: resolve it now, fetch on the worker.

    `sra_fetch.resolve` is the whole of the validation -- the token's shape, whether ENA
    knows it, whether the run has FASTQ there -- and its sentences are written for a box.
    The first run's first read file is what is sketched (`ena.first_read_file`), and the row
    records that file and its URL so the task fetches what was found.
    """
    try:
        plan = sra_fetch.resolve([accession])[0]
    except (AccessionError, sra_fetch.FetchError) as refusal:
        return JsonResponse({"error": str(refusal), "field": "accession"}, status=400)
    _run, entry = ena.first_read_file(plan)

    run = RefsniffRun.objects.create(
        experiment=experiment,
        created_by=request.user,
        accession=accession,
        read_file=entry["name"],
        read_url=entry["url"],
        status=STATUS_QUEUED)
    return _enqueue(request, experiment, run, accession)


def _enqueue(request, experiment, run, label):
    """Queue the sketch for `run`, record the queue's id on the row, answer the page."""
    job = jobs_api.enqueue(
        tasks.run_refsniff, run.pk,
        user=request.user,
        label="Identify reference — %s" % label,
        component=COMPONENT,
        experiment=experiment,
        cancellable=True)
    run.task_result_id = job.task_result_id
    run.save(update_fields=["task_result_id"])

    return JsonResponse({"run_id": run.pk, "runs": _run_rows(experiment)})


@require_POST
def run_delete(request, pk):
    """Forget a run. The row's post_delete receiver removes whatever files are left."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)

    run = RefsniffRun.objects.filter(pk=pk).select_related("experiment").first()
    if run is None:
        return JsonResponse({"error": "Unknown run."}, status=404)
    if not can_edit_experiment(request.user, run.experiment):
        return JsonResponse(
            {"error": experiment_lock_refusal(run.experiment)
                      or "You cannot change this experiment."}, status=403)
    if not run.is_finished:
        return JsonResponse(
            {"error": "That run has not finished. Cancel it on the Jobs page first, then "
                      "delete it."}, status=409)

    experiment = run.experiment
    run.delete()
    return JsonResponse({"deleted": True, "runs": _run_rows(experiment)})
