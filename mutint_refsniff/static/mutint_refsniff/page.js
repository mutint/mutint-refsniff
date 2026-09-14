/* The Identify Reference from Reads tab: the drop zone, the launch, and the run list.
 *
 * Lifted from mutint-breseq's launch.js and cut down: one file, two boxes, no name to
 * derive. The two values it needs arrive through a `json_script` element, so nothing here
 * needs the template engine. The uploader, the JSON poster and the confirm dialog are
 * core's, loaded from base.html.
 */
(function () {
    "use strict";

    var CONFIG = JSON.parse(document.getElementById("refsniff-config").textContent);
    var EXPERIMENT_ID = CONFIG.experiment_id;
    var COMPONENT = CONFIG.component;
    var HEAD_BYTES = CONFIG.head_bytes;
    var POLL_MS = 5000;

    var runsData = JSON.parse(document.getElementById("refsniff-runs-data").textContent);
    var runsEl = document.getElementById("refsniff-runs");
    var form = document.getElementById("refsniff-form");
    var pollTimer = null;
    var pollGeneration = 0;
    // Set while "Use as reference" is importing, so a second click cannot start a second
    // import of the same genome, and so the run list's poll leaves the button alone.
    var importing = false;

    function esc(text) {
        var div = document.createElement("div");
        div.textContent = text === null || text === undefined ? "" : String(text);
        return div.innerHTML;
    }

    function whenLocal(iso) {
        if (!iso) { return ""; }
        var when = new Date(iso);
        return isNaN(when.getTime()) ? "" : when.toLocaleString();
    }

    function elapsed(run) {
        var from = run.started_at || run.created_at;
        if (!from) { return ""; }
        var end = run.finished_at ? new Date(run.finished_at) : new Date();
        var seconds = Math.max(0, Math.round((end - new Date(from)) / 1000));
        var minutes = Math.floor(seconds / 60);
        var rest = seconds % 60;
        if (minutes) { return minutes + "m " + rest + "s"; }
        return rest + "s";
    }

    function pct(value) {
        return value === null || value === undefined ? "" : Number(value).toFixed(2) + "%";
    }

    // A row that says "queued" is either a job waiting its turn or a job nothing will ever
    // pick up; where the queue can tell us, it does.
    function statusLabel(run) {
        if (run.status === "finished") {
            return '<span class="label label-success">Finished</span>';
        }
        if (run.status === "failed") {
            return '<span class="label label-danger">Failed</span>';
        }
        if (run.status === "cancelled") {
            return '<span class="label label-default">Cancelled</span>';
        }
        if (run.status === "running") {
            return '<span class="label label-info">Running</span>';
        }
        var note = "";
        if (run.queue_status === "READY" || run.queue_status === "") {
            note = ' <small style="color: #a94442;">waiting for a worker &mdash; ' +
                   'run <code>./mutint db_worker</code></small>';
        }
        return '<span class="label label-default">Queued</span>' + note;
    }

    function blastTable(run) {
        var hits = run.blast_hits || [];
        var head = "<p style=\"margin-bottom: 0.3em;\"><small>RefSeq bacterial and " +
                   "archaeal genomes";
        if (run.blast_rid) {
            head += ", NCBI search <a href=\"https://blast.ncbi.nlm.nih.gov/Blast.cgi?CMD=Get&RID=" +
                    esc(run.blast_rid) + "\" target=\"_blank\" rel=\"noopener\">" +
                    esc(run.blast_rid) + "</a>";
        }
        if (run.note) { head += " &mdash; " + esc(run.note); }
        head += "</small></p>";
        if (!hits.length) { return head; }
        var html = [head, '<table class="table table-condensed" style="margin-bottom: 1em;">',
                    "<thead><tr><th>Organism</th><th>Accession</th><th>Reads</th>",
                    "<th>Identity</th><th></th></tr></thead><tbody>"];
        hits.forEach(function (hit, index) {
            var reads = hit.reads + " of " + run.reads_sampled;
            var action = "";
            if (index === 0 && run.status === "finished" && form) {
                action = '<button type="button" class="btn btn-primary btn-xs refsniff-use" ' +
                         'data-accession="' + esc(hit.accession) + '" data-organism="' +
                         esc(hit.organism) + '"' + (importing ? " disabled" : "") +
                         ">Use as reference</button>";
            }
            html.push("<tr><td title=\"" + esc(hit.title) + "\">" + esc(hit.organism) +
                      "</td><td><a href=\"https://www.ncbi.nlm.nih.gov/nuccore/" +
                      esc(hit.accession) + "\" target=\"_blank\" rel=\"noopener\">" +
                      esc(hit.accession) + "</a></td><td>" + esc(reads) + "</td><td>" +
                      esc(pct(hit.mean_identity)) + "</td><td>" + action + "</td></tr>");
        });
        html.push("</tbody></table>");
        return html.join("");
    }

    function runBlock(run) {
        var html = ['<div class="panel panel-default"><div class="panel-heading">',
                    "<b>" + esc(run.read_file) + "</b> &nbsp;" + statusLabel(run),
                    ' <small style="color: #666;">&middot; ' + esc(run.reads_sampled) +
                    " reads sampled &middot; " + esc(whenLocal(run.started_at || run.created_at)) +
                    (elapsed(run) ? " &middot; " + esc(elapsed(run)) : "") + "</small>"];
        var links = [];
        if (run.log_url) {
            links.push('<a href="' + esc(run.log_url) + '">log</a>');
        }
        if (form && (run.status === "finished" || run.status === "failed" ||
                     run.status === "cancelled")) {
            links.push('<a href="#" class="refsniff-delete" data-id="' + run.id +
                       '">delete</a>');
        }
        if (links.length) {
            html.push(' <small style="float: right;">' + links.join(" &middot; ") + "</small>");
        }
        html.push('</div><div class="panel-body">');
        if (run.error) {
            html.push('<p style="color: #a94442;">' + esc(run.error) + "</p>");
        }
        if (run.status === "finished") { html.push(blastTable(run)); }
        if (run.log && run.status === "failed") {
            html.push('<details><summary style="cursor: pointer; color: #666;">' +
                      "<small>run output</small></summary>" +
                      '<pre style="max-height: 20em; overflow: auto; font-size: 11px;">' +
                      esc(run.log) + "</pre></details>");
        }
        html.push("</div></div>");
        return html.join("");
    }

    function renderRuns(runs) {
        if (!runs.length) {
            runsEl.innerHTML = '<p style="color: #666;">Nothing has been run for this ' +
                               "experiment yet.</p>";
            return;
        }
        runsEl.innerHTML = runs.map(runBlock).join("");
    }

    function anyUnfinished(runs) {
        return runs.some(function (run) {
            return run.status === "queued" || run.status === "running";
        });
    }

    function stopPolling() {
        if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
        pollGeneration += 1;
    }

    function startPolling() {
        stopPolling();
        var mine = pollGeneration;
        function poll() {
            fetch("/refsniff/runs?experiment_id=" + EXPERIMENT_ID)
                .then(function (resp) { return resp.ok ? resp.json() : null; })
                .then(function (body) {
                    if (mine !== pollGeneration) { return; }
                    if (body && body.runs) {
                        runsData = body.runs;
                        renderRuns(runsData);
                        syncBusy();
                        if (!anyUnfinished(runsData)) { stopPolling(); return; }
                    }
                    pollTimer = setTimeout(poll, POLL_MS);
                })
                .catch(function () {
                    if (mine !== pollGeneration) { return; }
                    pollTimer = setTimeout(poll, POLL_MS);
                });
        }
        pollTimer = setTimeout(poll, POLL_MS);
    }

    function refresh(runs) {
        runsData = runs;
        renderRuns(runsData);
        syncBusy();
        if (anyUnfinished(runsData)) { startPolling(); } else { stopPolling(); }
    }

    // Deleting a finished run, from its heading.
    runsEl.addEventListener("click", function (e) {
        var link = e.target.closest ? e.target.closest(".refsniff-delete") : null;
        if (!link) { return; }
        e.preventDefault();
        mutintPostJson("/refsniff/run/" + link.getAttribute("data-id") + "/delete", {})
            .then(function (body) { refresh(body.runs || []); })
            .catch(function (err) { window.alert(err.message || String(err)); });
    });

    // "Use as reference": core's own accession import, driven from here. The two calls are
    // exactly what the Reference Sequence tab's accession box makes -- open a session for
    // the `reference` type carrying the accession and no files, then finalize -- so the
    // permission, the lock and a busy importer are all core's answers. Then the Reference
    // tab, because that is where the genome that just arrived is described, and this tab
    // is about to disappear from the strip.
    runsEl.addEventListener("click", function (e) {
        var button = e.target.closest ? e.target.closest(".refsniff-use") : null;
        if (!button || importing) { return; }
        e.preventDefault();
        var accession = button.getAttribute("data-accession");
        var organism = button.getAttribute("data-organism");
        window.mutintConfirm(
            "Use " + accession + " as the reference?",
            organism + " (" + accession + ") will be downloaded from NCBI and become this " +
            "experiment's reference genome. The tab you are on will then close, because " +
            "there is nothing left for it to identify.",
            "Import reference"
        ).then(function (go) {
            if (!go) { return; }
            importing = true;
            button.disabled = true;
            button.textContent = "Importing…";
            return mutintUpload([], {
                experimentId: EXPERIMENT_ID,
                importType: "reference",
                accessions: accession
            }).then(function (uploadId) {
                return mutintPostJson("/import/uploads/" + uploadId + "/finalize",
                                      {annotators: {}});
            }).then(function (summary) {
                if (summary && summary.needs_confirmation) {
                    // Cannot happen for an experiment with no reference, which is the only
                    // kind this page launches from; if it somehow does, the Reference tab
                    // is where the question is asked and answered.
                    window.location = "/import/?experiment_id=" + EXPERIMENT_ID +
                                      "&tab=reference";
                    return;
                }
                var failed = (summary && summary.files || []).filter(function (row) {
                    return row.error;
                });
                if (failed.length) {
                    throw new Error(failed.map(function (row) {
                        return row.file + ": " + row.error;
                    }).join("; "));
                }
                window.location = "/mutations/reference?experiment_id=" + EXPERIMENT_ID;
            }).catch(function (err) {
                importing = false;
                button.disabled = false;
                button.textContent = "Use as reference";
                window.alert("The reference could not be imported: " +
                             (err.message || String(err)));
            });
        });
    });

    renderRuns(runsData);
    if (anyUnfinished(runsData)) { startPolling(); }

    // Everything below is the form, which a reader without write access does not get.
    if (!form) { return; }

    var selected = null;
    var uploading = false;
    var dropzone = document.getElementById("refsniff-dropzone");
    var fileInput = document.getElementById("refsniff-file-input");
    var fileListEl = document.getElementById("refsniff-file-list");
    var submitBtn = document.getElementById("refsniff-submit");
    var resetBtn = document.getElementById("refsniff-reset");
    var readsInput = document.getElementById("refsniff-reads");
    var emailInput = document.getElementById("refsniff-email");
    var progressEl = document.getElementById("refsniff-progress");
    var progressBar = document.getElementById("refsniff-progress-bar");
    var progressText = document.getElementById("refsniff-progress-text");
    var errorEl = document.getElementById("refsniff-error");

    // One run at a time per experiment: the server refuses a second with a 409, and the
    // button says so first rather than letting somebody upload 16 MB to be told.
    function syncBusy() {
        if (!form) { return; }
        var busy = anyUnfinished(runsData);
        submitBtn.disabled = uploading || busy || !selected;
        submitBtn.title = busy ? "A run is already in progress for this experiment." : "";
    }

    function isFastqName(name) {
        var lower = name.toLowerCase();
        return [".fastq", ".fq", ".fastq.gz", ".fq.gz"].some(function (suffix) {
            return lower.endsWith(suffix);
        });
    }

    function renderList() {
        if (resetBtn) { resetBtn.disabled = uploading || !selected; }
        syncBusy();
        if (!selected) { fileListEl.innerHTML = ""; return; }
        var size = selected.file.size;
        var shown = size > HEAD_BYTES
            ? " (" + (HEAD_BYTES / 1048576) + " MB of " + (size / 1048576).toFixed(0) +
              " MB will be uploaded)" : "";
        fileListEl.innerHTML = "<b>" + esc(selected.path) + "</b>" + esc(shown);
    }

    function addEntries(entries) {
        var fastqs = entries.filter(function (entry) { return isFastqName(entry.path); });
        if (!fastqs.length) {
            renderError("Drop a FASTQ file: .fastq, .fq, .fastq.gz or .fq.gz.");
            return;
        }
        errorEl.innerHTML = "";
        if (fastqs.length > 1) {
            renderError("One file at a time; " + fastqs[0].path + " was taken.");
        }
        // Only the head goes up. A Blob carries no name, so the entry keeps the original
        // path and the sliced blob stands in for the file.
        var file = fastqs[0].file;
        var head = file.size > HEAD_BYTES ? file.slice(0, HEAD_BYTES) : file;
        selected = {path: fastqs[0].path.split("/").pop(), file: head, original: file};
        renderList();
    }

    function resetSelection() {
        if (uploading) { return; }
        selected = null;
        errorEl.innerHTML = "";
        renderList();
    }

    function setProgress(done, total, label) {
        progressEl.style.display = "block";
        var p = total ? Math.floor((done / total) * 100) : 0;
        progressBar.style.width = p + "%";
        progressBar.textContent = p + "%";
        progressText.textContent = label;
    }

    function renderError(message) {
        errorEl.innerHTML = '<div class="alert alert-danger" style="margin-top: 1em;">' +
                            esc(message) + "</div>";
    }

    dropzone.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", function () {
        addEntries(mutintFromFileList(fileInput.files));
        fileInput.value = "";
    });
    resetBtn.addEventListener("click", resetSelection);
    ["dragenter", "dragover"].forEach(function (evt) {
        dropzone.addEventListener(evt, function (e) {
            e.preventDefault(); dropzone.style.background = "#eef6ff";
        });
    });
    ["dragleave", "drop"].forEach(function (evt) {
        dropzone.addEventListener(evt, function (e) {
            e.preventDefault(); dropzone.style.background = "#fafafa";
        });
    });
    dropzone.addEventListener("drop", function (e) {
        mutintCollectDropped(e.dataTransfer).then(addEntries);
    });

    form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (!selected || uploading) { return; }
        errorEl.innerHTML = "";
        if (!readsInput.checkValidity()) {
            renderError("Reads to sample has to be a whole number between " +
                        CONFIG.reads_min + " and " + CONFIG.reads_max + ".");
            readsInput.focus();
            return;
        }
        if (!emailInput.value.trim() || !emailInput.checkValidity()) {
            renderError("NCBI asks for a contact email with every search.");
            emailInput.focus();
            return;
        }
        uploading = true;
        renderList();
        setProgress(0, 1, "Preparing…");
        mutintUpload([selected], {
            experimentId: EXPERIMENT_ID,
            consumer: COMPONENT,
            onProgress: setProgress
        }).then(function (uploadId) {
            setProgress(1, 1, "Sampling the reads and queueing the run…");
            return mutintPostJson("/refsniff/launch?experiment_id=" + EXPERIMENT_ID, {
                upload_id: uploadId,
                reads: readsInput.value,
                email: emailInput.value.trim()
            });
        }).then(function (body) {
            progressEl.style.display = "none";
            selected = null;
            refresh(body.runs || []);
        }).catch(function (err) {
            progressEl.style.display = "none";
            renderError(err.message || String(err));
            if (err.body && err.body.field === "email") { emailInput.focus(); }
        }).then(function () {
            uploading = false;
            renderList();
        });
    });

    renderList();
}());
