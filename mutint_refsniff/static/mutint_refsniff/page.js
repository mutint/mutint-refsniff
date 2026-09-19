/* The Identify Reference from Reads tab: the drop zone, the launch, and the run list.
 *
 * Lifted from mutint-breseq's launch.js and cut down: one file or one accession, and no
 * name to derive. The few values it needs arrive through a `json_script` element, so nothing
 * here needs the template engine. The uploader and the JSON poster are core's, loaded from
 * base.html; there is no confirm dialog here any more, and `page.html` therefore loads no
 * sweetalert -- see the Use as reference handler for why.
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

    function num(value, digits) {
        return value === null || value === undefined
            ? "" : Number(value).toFixed(digits === undefined ? 2 : digits);
    }

    // The genome column: the assembly when one was found, because that is what "Use as
    // reference" imports and what resolves to a chromosome *and* its plasmids. Failing that
    // the nucleotide accession the sketch matched, which for a draft is one contig of many
    // -- said so, since importing it would be wrong.
    function genomeCell(hit) {
        if (hit.assembly) {
            return '<a href="https://www.ncbi.nlm.nih.gov/datasets/genome/' +
                   esc(hit.assembly) + '" target="_blank" rel="noopener">' +
                   esc(hit.assembly) + "</a>";
        }
        var nuccore = '<a href="https://www.ncbi.nlm.nih.gov/nuccore/' + esc(hit.accession) +
                      '" target="_blank" rel="noopener">' + esc(hit.accession) + "</a>";
        if (hit.draft) {
            return nuccore + ' <small style="color: #a94442;">one contig of a draft</small>';
        }
        return nuccore;
    }

    function hitsTable(run) {
        var hits = run.hits || [];
        var head = '<p style="margin-bottom: 0.3em;"><small>RefSeq sketch server, ' +
                   esc(run.reads_sketched) + " reads sketched";
        if (run.note) { head += " &mdash; " + esc(run.note); }
        head += "</small></p>";
        if (!hits.length) { return head; }
        var html = [head, '<table class="table table-condensed" style="margin-bottom: 1em;">',
                    "<thead><tr><th>Organism</th><th>ANI</th><th>Complete</th>",
                    "<th>Contam.</th><th>Genome</th><th></th></tr></thead><tbody>"];
        hits.forEach(function (hit) {
            var action = "";
            if (hit.import_accession && run.status === "finished" && form) {
                action = '<button type="button" class="btn btn-primary btn-xs refsniff-use" ' +
                         'data-accession="' + esc(hit.import_accession) + '" data-organism="' +
                         esc(hit.name) + '">Use as reference</button>';
            }
            html.push("<tr><td title=\"" + esc(hit.title) + "\">" + esc(hit.name) +
                      "</td><td>" + esc(num(hit.ani)) + "%</td><td>" +
                      esc(num(hit.completeness, 1)) + "%</td><td>" +
                      esc(num(hit.contamination, 1)) + "%</td><td>" + genomeCell(hit) +
                      "</td><td>" + action + "</td></tr>");
        });
        html.push("</tbody></table>");
        // What the confirm dialog used to say at the moment of pressing. It is a standing
        // fact about every row of this table rather than about one press, and it is this
        // plugin's knowledge -- nothing on the page it hands over to knows that the accession
        // arrived from a strain match, or that this tab closes once a reference exists.
        if (form) {
            html.push('<p style="margin-bottom: 1em;"><small>Use as reference fills the ' +
                      "accession into the Reference Sequence tab, where you press Import " +
                      "and can tick the annotators to run on it. Importing an assembly " +
                      "brings every sequence it is made of, plasmids included, and this tab " +
                      "closes once the experiment has a reference.</small></p>");
        }
        return html.join("");
    }

    // A run named by accession is headed by the accession, linked to ENA's page for it,
    // with the file it resolved to beside it; a drop is headed by the file.
    function runTitle(run) {
        if (!run.accession) { return "<b>" + esc(run.read_file) + "</b>"; }
        return '<b><a href="' + esc(run.accession_url) + '" target="_blank" ' +
               'rel="noopener">' + esc(run.accession) + "</a></b> " +
               '<small style="color: #666;">' + esc(run.read_file) + "</small>";
    }

    function runBlock(run) {
        var html = ['<div class="panel panel-default"><div class="panel-heading">',
                    runTitle(run) + " &nbsp;" + statusLabel(run),
                    ' <small style="color: #666;">&middot; ' + esc(run.reads_sketched) +
                    " reads sketched &middot; " + esc(whenLocal(run.started_at || run.created_at)) +
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
        if (run.status === "finished") { html.push(hitsTable(run)); }
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

    // "Use as reference": hand the accession to core's Reference Sequence tab, filled into
    // the box a person would otherwise have typed it into, and let them press Import there.
    //
    // **It used to do the import itself**, with the two calls that box makes, and what it
    // could not carry is the reason it no longer does: the reference tabs are where the
    // registered annotators are offered, and a POST from here had no boxes to tick, so it
    // sent `{annotators: {}}`. The one path that knows for certain this experiment has no
    // reference was therefore also the one path that silently skipped every annotator --
    // ISEScan among them, whose whole point is to run *before* any reads are called against
    // the genome.
    //
    // No confirm dialog, and that follows the house rule rather than dropping one: which
    // dialog a control gets is decided by whether the person can undo it themselves, and a
    // navigation is undone with Back. What the old one warned about -- every sequence the
    // assembly is made of, plasmids included -- is a standing fact about this table rather
    // than about one press, so the page says it under the table instead.
    runsEl.addEventListener("click", function (e) {
        var button = e.target.closest ? e.target.closest(".refsniff-use") : null;
        if (!button) { return; }
        e.preventDefault();
        window.location = "/import/?experiment_id=" + EXPERIMENT_ID + "&tab=reference" +
                          "&accession=" +
                          encodeURIComponent(button.getAttribute("data-accession"));
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
    var accessionInput = document.getElementById("refsniff-accession");
    var progressEl = document.getElementById("refsniff-progress");
    var progressBar = document.getElementById("refsniff-progress-bar");
    var progressText = document.getElementById("refsniff-progress-text");
    var errorEl = document.getElementById("refsniff-error");

    // One run at a time per experiment: the server refuses a second with a 409, and the
    // button says so first rather than letting somebody upload 16 MB to be told.
    function accessionText() {
        return accessionInput.value.trim();
    }

    function syncBusy() {
        if (!form) { return; }
        var busy = anyUnfinished(runsData);
        submitBtn.disabled = uploading || busy || !(selected || accessionText());
        submitBtn.title = busy ? "A run is already in progress for this experiment." : "";
    }
    accessionInput.addEventListener("input", syncBusy);

    function isFastqName(name) {
        var lower = name.toLowerCase();
        return [".fastq", ".fq", ".fastq.gz", ".fq.gz"].some(function (suffix) {
            return lower.endsWith(suffix);
        });
    }

    function renderList() {
        if (resetBtn) { resetBtn.disabled = uploading || !(selected || accessionText()); }
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
        accessionInput.value = "";
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

    // Both ways in post to the same endpoint; only which field is filled differs, and
    // the server refuses a body naming both.
    function postLaunch(uploadId) {
        return mutintPostJson("/refsniff/launch?experiment_id=" + EXPERIMENT_ID, {
            upload_id: uploadId || "",
            accession: uploadId ? "" : accessionText()
        });
    }

    form.addEventListener("submit", function (e) {
        e.preventDefault();
        if (uploading || !(selected || accessionText())) { return; }
        errorEl.innerHTML = "";
        if (selected && accessionText()) {
            renderError("Drop a file or type an accession, not both.");
            accessionInput.focus();
            return;
        }
        uploading = true;
        renderList();
        var started;
        if (selected) {
            setProgress(0, 1, "Preparing…");
            started = mutintUpload([selected], {
                experimentId: EXPERIMENT_ID,
                consumer: COMPONENT,
                onProgress: setProgress
            }).then(function (uploadId) {
                setProgress(1, 1, "Queueing the run…");
                return postLaunch(uploadId);
            });
        } else {
            setProgress(1, 1, "Asking ENA about the accession and queueing the run…");
            started = postLaunch("");
        }
        started.then(function (body) {
            progressEl.style.display = "none";
            selected = null;
            accessionInput.value = "";
            refresh(body.runs || []);
        }).catch(function (err) {
            progressEl.style.display = "none";
            renderError(err.message || String(err));
            if (err.body && err.body.field === "accession") { accessionInput.focus(); }
        }).then(function () {
            uploading = false;
            renderList();
        });
    });

    renderList();
}());
