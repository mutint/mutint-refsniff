# CLAUDE.md — mutint-refsniff

Guidance for Claude Code working in this repository.

It is a **submodule of `mutint`**. Edit it **here**, in the suite-root checkout, never in
`mutint/mutint-refsniff` — that copy is on a detached HEAD and a commit made there is
reachable only by SHA inside that one clone. See the suite `CLAUDE.md`.

---

## What this is

An Import data tab, **Identify Reference from Reads**, offered only while an experiment has
no reference genome. Drop one FASTQ; the first N reads (100–1000, default 200) are sampled at
launch; a job sends them to NCBI BLAST (the Common URL API, over stdlib `urllib`) against
RefSeq bacterial and archaeal genomes and tallies which genome each read chose; a finished
run offers **Use as reference** on the top hit, which imports that accession through core's
own NCBI import, after which the tab leaves the strip.

**It was two steps for a day.** BBTools `sendsketch` ran first -- a MinHash sketch to JGI's
RefSeq sketch server, seconds -- with BLAST as the confirmation. It went because it saved no
NCBI searches (BLAST ran regardless), cost a JDK in every install, and on 200 reads ranked
a near-identical genome of another name above the right one at 99.97% versus 99.95% ANI:
strain-level resolution is the sketch's ceiling, where BLAST's per-read votes are more
robust to a near tie. What was worth keeping from it is the *shape*: the tab, the head-only
upload, the one-run rule and the poll floor were all built around it and stand on their own.

---

## The pieces

| file | what |
|---|---|
| `fastq.py` | pure: is it FASTQ, and the first N whole records of it as renumbered FASTA |
| `blast.py` | pure: put / status / fetch / parse / tally, and the wait between, with an injectable transport and clock |
| `tasks.py` | the `@task`: one search, recorded on the row |
| `views.py` | the page, the launch endpoint, the run list the page polls, deleting a run |
| `models.py` | `RefsniffRun`, and the receiver that owns its directory |
| `templates/refsniff/page.html`, `static/mutint_refsniff/page.js` | the tab and its script, lifted from mutint-breseq's launcher |

---

## Things that are load-bearing

### The tab exists only without a reference, and that needed a core flag

`register_import_tab` had `requires_reference=True` (hide until there is one) and no inverse.
This plugin added `only_without_reference=True` to core, named after the same flag on
`import_registry`'s handlers. The page still handles a typed URL after a reference arrives —
a banner instead of the form, and the launch answers 409 — because a tab leaving the strip
does not close a window somebody left open.

### One run per experiment at a time, refused rather than superseded

`views.launch` answers 409 while a `RefsniffRun` for the experiment is queued or running.
mutint-breseq *supersedes* a run for the same sample name because its output would be
overwritten anyway; here there is no name to collide on, a second identical search is pure
waste against NCBI's per-day cap, and the page disables its button while a run is in flight
for the same reason.

### Every refusal comes before `staging.claim`, and a bad file abandons the session

The read count is checked first and costs nothing (the session stays open). Then the staged
directory is *looked at* — exactly one file, a FASTQ name, `fastq.sniff` — before it is
claimed; a bad file abandons the session, since the fix is a different file. Only then is the
row created, the head sampled into `store.component_dir` as FASTA, and the session closed. The
drop is gone by the time the job is queued; what a run keeps is a few hundred reads, and the
task deletes even those on every ending.

### The page uploads only the head of the file

`page.js` sends `file.slice(0, 16 MiB)` under the original name. A FASTQ can be gigabytes and
the launch wants a few hundred records off the front. `fastq.sample` therefore treats a
truncated tail as expected — a record cut mid-line is dropped, and a gzip member with no
footer raises `EOFError` from inside `readline`, which ends the file the same way. The page
says so beside the drop zone.

### The BLAST wait is NCBI's rules, and it is cancellable in slices

`blast.wait` never polls more often than `POLL_FLOOR_SECONDS` (60), waits NCBI's own `RTOE`
estimate first when it is longer, and sleeps in `SLICE_SECONDS` (5) steps calling
`check_cancelled()` between them, which raises `JobCancelled`. The transport, the sleep and
the clock are resolved from module names at call time (`default_http`, `_sleep`, `_clock`),
which is what lets `tests/fake_blast.patched()` reach a call made through `run` and the task.
`FORMAT_TYPE=XML` is the legacy single-document format on purpose: XML2 and JSON2 come back
zipped per query.

**Every request carries `tool=` and `email=`, never `api_key`** — the URL API has no such
parameter, and a key in a query string is what `mutint_sample.ncbi.redact` scrubs out of
messages. Every `BlastError` still goes through `redact`, because the email is in the URLs.

**The email is the person's, not the deployment's.** NCBI's question is who to contact about
a search, and a search is launched by somebody. The form's box is prefilled from
`request.user.email` (falling back to `MUTINT_NCBI_EMAIL`), the launch refuses a blank or
malformed one, the run records what was sent, and the task passes it to every request. Core
grew **Change Email** in the account block for this: a `User` made by `start.py` carries a
placeholder and one made in Django admin may carry nothing, and until that page there was
nowhere a person could set their own.

### A failed search fails the run; no hits does not

`BlastError` -- NCBI refused the submission, reported the search FAILED or lost it, or the
timeout passed -- fails the run with NCBI's own sentence and re-raises, as every task in the
suite does. A search that ran and matched nothing is a **finished** run with an empty table
and a `note`: it is an answer, and the page shows it as one.

### "Use as reference" is core's importer, driven from the page

There is no server code for it here. `page.js` makes the two calls the Reference Sequence
tab's accession box makes — `mutintUpload([], {importType: "reference", accessions})`, then
`POST /import/uploads/<id>/finalize` — so permission, the lock and a busy importer are all
core's answers, and the genome arrives exactly as a typed accession would. It imports one
record; a reference that needs a chromosome and its plasmids is done on the Reference tab.

---

## What it deliberately does not do

- **No import handler**: the drop is job input, not a mutation file.
- **No nav entry**: the tab is the entry point.
- **No rebuilder, storage kind or export type**: it derives nothing, keeps nothing, adds no type.
- **No tool**: `tools.txt` is empty on purpose, and says why.

---

## Tests

57 tests. Once the plugin is a submodule of `mutint`:

```bash
cd mutint && ./mutint test mutint_refsniff
```

Until then, from mutint-core with a settings module that adds the app:

```bash
cd mutint-core
cat > /tmp/refsniff_settings.py <<'PY'
from config.settings_local import *  # noqa: F401,F403
INSTALLED_APPS = INSTALLED_APPS + ["mutint_refsniff"]
PY
DJANGO_SETTINGS_MODULE=refsniff_settings PYTHONPATH=/tmp:../mutint-refsniff ./mutint test mutint_refsniff
```

`tests/fake_blast.py` is a scripted NCBI and a clock that never waits, installed on the
`blast` module by `patched()`; nothing here is patched by name inside `blast.py` itself,
because the module resolves its transport and clock at call time for exactly this.
