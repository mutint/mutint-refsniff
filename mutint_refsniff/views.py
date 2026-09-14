"""The tab page, the launch endpoint, and the run list the page polls.

Shaped like mutint-breseq's: function-based views, permission checked inline, hand-written
Bootstrap posting to a `@require_POST` JSON endpoint through `mutintPostJson`.
"""

import json
import logging
import os

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

import mutint_sample.views.common
from mutint_common import store
from mutint_common.util import get_user_context
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import can_edit_experiment, experiment_lock_refusal
from mutint_import import reference_store, staging
from mutint_import.upload_session import UploadError
from mutint_jobs import jobs as jobs_api

from mutint_refsniff import blast, fastq, tasks
from mutint_refsniff.models import (
    COMPONENT,
    STATUS_QUEUED,
    STATUS_RUNNING,
    RefsniffRun,
)

logger = logging.getLogger("mutint_refsniff.views")

#: How much of the dropped file the page uploads. Only the first few hundred records are
#: wanted, and a FASTQ can be gigabytes; 16 MiB holds a thousand reads of any length that
#: exists, compressed or not, with room over.
HEAD_BYTES = 16 * 1024 * 1024


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
            "reads_requested": run.reads_requested,
            "reads_sampled": run.reads_sampled,
            "bases_sampled": run.bases_sampled,
            "contact_email": run.contact_email,
            "note": run.note,
            "blast_rid": run.blast_rid,
            "blast_hits": run.blast_hits or [],
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
    """The tab: drop a FASTQ, sample its reads, find the nearest genome. Lists the runs.

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
        # Prefilled from the account, which is where a person changes it for good; the
        # deployment's address is the fallback for an account that has none.
        "contact_email": request.user.email or blast.default_email(),
        "busy": _active_run(experiment) is not None,
        "config": {
            "experiment_id": experiment.id,
            "component": COMPONENT,
            "head_bytes": HEAD_BYTES,
            "reads_default": fastq.DEFAULT_READS,
            "reads_min": fastq.MIN_READS,
            "reads_max": fastq.MAX_READS,
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
    """Take a staged FASTQ, sample its head, and queue the identification.

    Body: `{upload_id, reads, email}`. Every refusal comes **before** `staging.claim`, so
    a bad box costs nothing and the session stays open to try again; a bad *file* abandons
    the session, since the fix is a different file.

    Gated on `can_edit_experiment`: what this leads to is the experiment's reference, which
    is as shared as a write gets, and a locked experiment must refuse it. The sampled reads
    are sent to NCBI, which the page says in as many words above its button.
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

    # **One run at a time per experiment.** A second identical search is pure waste against
    # NCBI's per-day cap, and there is no sample name to supersede on, so this refuses
    # rather than cancelling the first.
    if _active_run(experiment) is not None:
        return JsonResponse(
            {"error": "A run is already in progress for this experiment. Wait for it to "
                      "finish, or cancel it on the Jobs page."}, status=409)

    payload = _payload(request)
    try:
        reads = fastq.clean_read_count(payload.get("reads"))
    except fastq.ReadCountError as refusal:
        return JsonResponse({"error": str(refusal), "field": "reads"}, status=400)
    # Required, not merely passed on: NCBI asks that every automated search name someone to
    # contact, and a search with nobody behind it is the deployment answering for a person.
    email = (payload.get("email") or "").strip()
    try:
        if not email:
            raise ValidationError("NCBI asks for a contact email with every search.")
        validate_email(email)
    except ValidationError as refused:
        return JsonResponse({"error": " ".join(refused.messages), "field": "email"},
                            status=400)

    upload_id = (payload.get("upload_id") or "").strip()
    if not upload_id:
        return JsonResponse({"error": "Drop a FASTQ file first."}, status=400)
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
        reads_requested=reads,
        contact_email=email,
        status=STATUS_QUEUED)
    try:
        store.ensure_dir(run.directory())
        sampled, bases = fastq.sample(path, reads, run.query_path())
    except Exception as exc:
        logger.exception("could not sample reads for a refsniff launch in experiment %s",
                         experiment.id)
        run.delete()
        staging.abandon(session)
        return JsonResponse({"error": "The uploaded reads could not be read: %s" % exc},
                            status=500)
    if not sampled:
        run.delete()
        staging.abandon(session)
        return JsonResponse({"error": "%s holds no complete FASTQ record." % name,
                             "field": "upload"}, status=400)
    run.reads_sampled = sampled
    run.bases_sampled = bases
    run.save(update_fields=["reads_sampled", "bases_sampled"])

    # The drop is gone by here; the sample lives under the run's own directory.
    staging.close(session)

    job = jobs_api.enqueue(
        tasks.run_refsniff, run.pk,
        user=request.user,
        label="Identify reference — %s" % name,
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
