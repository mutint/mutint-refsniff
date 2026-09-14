# CLAUDE.md — mutint-refsniff

Guidance for Claude Code working in this repository.

It is a **submodule of `mutint`**. Edit it **here**, in the suite-root checkout, never in
`mutint/mutint-refsniff` — that copy is on a detached HEAD and a commit made there is
reachable only by SHA inside that one clone. See the suite `CLAUDE.md`.

---

## What this is

An Import data tab, **Identify Reference from Reads**, offered only while an experiment has
no reference genome. Drop one FASTQ, or type an SRA accession; the first 16 MiB of it — a
drop's head, uploaded by the page, or an ENA file's head, fetched by the worker — is handed
to BBTools' `sendsketch.sh`, which sends a MinHash sketch of it to JGI's RefSeq sketch server
and gets back the ten nearest strains with an ANI for each. Each hit's taxid is turned into a
RefSeq assembly accession at NCBI Datasets, and **Use as reference** imports that assembly
through core's own accession import, after which the tab leaves the strip.

---

## The pieces

| file | what |
|---|---|
| `fastq.py` | pure: is it FASTQ, does it hold a whole record, `HEAD_BYTES` |
| `ena.py` | which of a run's files is sketched, and the first `HEAD_BYTES` of it from ENA |
| `sketch.py` | pure: sendsketch's argv, and reading the JSON it writes; no network |
| `assemblies.py` | taxid → the RefSeq assembly to import, at NCBI Datasets |
| `tasks.py` | the `@task`: fetch, sketch, look up, recorded on the row |
| `views.py` | the page, the launch endpoint, the run list the page polls, deleting a run |
| `models.py` | `RefsniffRun`, and the receiver that owns its directory |
| `templates/refsniff/page.html`, `static/mutint_refsniff/page.js` | the tab and its script, lifted from mutint-breseq's launcher |

---

## Things that are load-bearing

### Why it is sendsketch and not BLAST, which it was

This ran NCBI BLAST over the Common URL API for a day. Three findings killed it, and they are
worth keeping because the first one will look like a bug in this code to anyone who does not
know it:

1. **NCBI's URL API never returns an answer from a genome database.** Measured 2026-09-14,
   fifteen searches, final poll two hours after submission: every search against `nt`
   finished (a single read in under three minutes), and **every** search against
   `refseq_genomes`, `nt_prok`, `prok_complete_genomes` or `refseq_representative_genomes`
   was still `WAITING` after two hours — with and without the Bacteria/Archaea
   `ENTREZ_QUERY`, with and without `MEGABLAST`, at 200 reads and at **one**. Megablast, the
   Entrez restriction, `EXPECT` and the read count were all irrelevant; NCBI queues its
   genome databases separately and, today, effectively forever. The `RTOE` estimates were
   noise, from 1 s to 158 s and uncorrelated with what finished.
2. **Votes over a few hundred short reads cannot pick a strain.** Thousands of *E. coli*
   assemblies tie at 100% on a 150 bp read, and best-hit-among-ties is arbitrary. The `nt`
   control's top hit for a REL606 read was a random clinical isolate.
3. **Species-level is not enough for a reference.** K-12 and REL606 are about 1.5% divergent,
   above the `--max-percent-divergence 1.0` mutint-breseq now passes by default.

sendsketch answers all three on the full head: 3.4 seconds, REL606 top at ANI 99.79% with 13
unique k-mers, OP50 and BL21(DE3) behind it, K-12 nowhere in the table.

**This reverses an earlier note in this file**, which said sendsketch had been tried and
removed. It had, and both of its reasons are now gone: *it saves no BLAST search* (there is
no BLAST search), and *200 reads is too few to separate strains* (it reads ~136 000). The
third reason — that it costs a JDK in every install — stands and is simply the price.

### What leaves the deployment is a sketch

Core's rule is that sequence does not leave, and this is the one page that bends it. A sketch
is a few thousand k-mer hashes: the reads cannot be read back out of it. The page says so in
as many words above its button, and `test_launch.PageTestCase` asserts the sentence. Nothing
names the person or the deployment, because JGI's server asks for nothing — which is why the
contact-email box and its `MUTINT_NCBI_EMAIL` fallback went with BLAST. (`MUTINT_NCBI_EMAIL`
stays in core's settings; core's own NCBI code uses it. Core's **Change Email** page stays
too; it is core's.)

### Four settings on the command line, each load-bearing

`sketch.build_argv` is four lines and every one of them was chosen:

- **`level=1`** is one record per *strain*. sendsketch's default, `level=2`, collapses to one
  per species — which answers "E. coli" to the question this page exists to ask.
- **`-Xmx1g`**, because left alone it auto-picks about 5.6 GB, beside a pool of background
  workers and a PostgreSQL cluster on the same machine.
- **`format=json printname0=t printtaxa=t`**, which is what makes the output parseable and
  what puts `TaxID` and `seqName` on every hit.
- **`out=<run>/sketch.json`** rather than reading the job log. `run_tool` gives the log to
  `Popen` as a descriptor and hands back only a returncode, so the answer has to be a file.

### The JVM is not in `env/tools/bin`, and `tool_environment` is why this works

**bioconda's `openjdk` installs nothing into the prefix's `bin/`.** The JVM is at
`<prefix>/lib/jvm/bin/java`, and conda exports `JAVA_HOME` from an `etc/conda/activate.d`
script that nothing here runs -- `processes.run_tool` runs an argv with an env, not an
activated environment. `sendsketch.sh` is a shell wrapper that ends in a bare `java`.

So the `tool_environment()` every other component in the suite copies -- prepend
`<prefix>/bin` -- puts everything on PATH *except the one binary the tool executes*, and the
run then turns on whether the host happens to have a `java`. **A developer Mac has one**
(`/usr/bin/java`, Java 17), so that version works locally and fails on a clean machine, or
silently uses a host Java 8 that bbmap needs 17+ to beat. This was found by looking at what
the package installed (`conda-meta/openjdk-*.json` lists no `bin/` entry), not by a test: a
test on a machine with a system JDK cannot show it.

`sketch.tool_environment` therefore adds `<prefix>/lib/jvm/bin` to PATH as well and sets
`JAVA_HOME` and `JAVA_LD_LIBRARY_PATH` to what `openjdk_activate.sh` would have --
`test_sketch.ToolEnvironmentTestCase` pins all of it, against a fake prefix, so it is
asserted on a machine with a system Java and on one without alike. Verified live:
`shutil.which('java', path=tool_environment()['PATH'])` resolves to the provisioned JDK,
and the run is exit 0.

### A truncated gzip is expected and prints a traceback

The head is `file.slice(0, 16 MiB)` or a `Range` fetch, so its last gzip member has no
footer. sendsketch loads every whole record before the cut, prints
`java.io.EOFException: Unexpected end of ZLIB input stream`, and **exits 0**. The returncode
is what the task reads; that traceback in a log is not a failure. Measured: 135 798 reads
loaded from a real 16 MiB head.

**And the log says so, above it.** A stack trace in the middle of a job log reads as a broken
run to anyone who has not been told otherwise, and being told in this file is no use to
somebody looking at `/jobs/<pk>/log`. `sketch.truncation_note` is the sentence and
`tasks.run_refsniff` writes it **before** `run_tool`, so a reader meets the explanation first.

It cannot be suppressed at the source, and that is worth knowing before anybody tries:
`processes.run_tool` hands the tool the log file's *descriptor* rather than a pipe -- which is
the whole reason a running job's output is visible at all -- so those bytes never pass through
any code of ours. Explaining beats filtering, and filtering would cost the live log.

Two conditions on the note, and both earn their place. **Gzipped**, because a plain FASTQ cut
mid-record raises nothing: `fastq._records` stops and the tool loses one read. **At the
limit**, because a smaller file arrived whole -- saying it anyway would teach a reader to skip
the line, which is exactly what it exists to stop them doing.

### A hit names a sequence; a reference wants an assembly

`seqName` carries one nucleotide accession — for a complete genome the chromosome *without*
its plasmids, and for a draft one contig out of hundreds. So `assemblies.annotate` turns each
hit's taxid into a RefSeq assembly (`GCF_…`) at NCBI Datasets, and that is what the button
imports: core resolves an assembly to every sequence it is made of, which is the reference
breseq wants. `sketch.import_accession` is the fallback rule, and its third case is the one
that matters — **a draft with no assembly offers no button at all**, because importing one
contig of a 276-contig assembly is worse than offering nothing.

**Only strain-level taxids are ever looked up**, which is what makes this tractable: taxid
413997 (REL606) has exactly one RefSeq assembly, while the *species* taxid 562 has 52 489.
`level=1` is what guarantees the taxid is a strain's.

**A failed lookup does not fail the run.** The sketch is the answer; the assembly is a
convenience on it. `annotate` catches `FetchError` per hit, says so in the job log, and
leaves that row falling back through `import_accession`.

`ncbi_fetch._datasets_json` is reused past its leading underscore, deliberately: it carries
the API key in the header Datasets wants, maps 404/429/non-JSON to `FetchError` and redacts
the key out of every message. That is the coupling to notice if core's `ncbi_fetch` is
reorganised.

### The tab exists only without a reference, and that needed a core flag

`register_import_tab` had `requires_reference=True` (hide until there is one) and no inverse.
This plugin added `only_without_reference=True` to core, named after the same flag on
`import_registry`'s handlers. The page still handles a typed URL after a reference arrives —
a banner instead of the form, and the launch answers 409 — because a tab leaving the strip
does not close a window somebody left open.

### One run per experiment at a time, refused rather than superseded

`views.launch` answers 409 while a `RefsniffRun` for the experiment is queued or running.
mutint-breseq *supersedes* a run for the same sample name because its output would be
overwritten anyway; here there is no name to collide on and a second identical sketch answers
the same thing, so this refuses. The page disables its button while a run is in flight for
the same reason.

### Every refusal comes before `staging.claim`, and a bad file abandons the session

A body naming both an upload and an accession is refused before either is looked at, and the
session stays open. Then the staged directory is *looked at* — exactly one file, a FASTQ
name, `fastq.sniff` — before it is claimed; a bad file abandons the session, since the fix is
a different file. Only then is the row created, the drop **moved** (not copied) into
`store.component_dir` under the name it arrived with, and the session closed. The name
matters: gzip is chosen by suffix, in `fastq.open_lines` and inside sendsketch alike.

### A second way in: an SRA accession, fetched from ENA, not `fastq-dump`

Type a run, experiment, sample or study accession instead of dropping a file. The obvious
tool for "the first N spots of an SRA run" is `fastq-dump -X N`, and it was the first thing
asked for. It is not what this does, for two reasons: it is a conda package in every install
plus sra-tools' first-run configuration, on top of the JDK this already costs; and core
already chose ENA over sra-tools for whole runs, in `mutint_import.sra_fetch`'s docstring.
ENA mirrors every run's reads as `fastq.gz` over HTTPS and **honours `Range`** — measured:
`206 Partial Content`, `Accept-Ranges: bytes` — so the first 16 MiB is one request, and what
arrives is exactly what the page uploads for a drop.

**Resolved in the view, fetched on the worker.** `views._launch_accession` calls core's
`sra_fetch.resolve([token])` — one ENA portal call, whose sentences are written for a box —
so an unknown accession, a token of no SRA shape, or a run ENA holds no FASTQ for is a 400
with `field: "accession"` and no row. The row then records the accession, the file
`ena.first_read_file` chose and its URL, and the task does the fetch: `ftp.sra.ebi.ac.uk` can
stall, and the job log and the cancel poll are there. mutint-breseq splits the same way.

**The first run, and its read-1 file.** An experiment, sample or study takes its first run; a
paired run takes `_1` only, since a mate covering the same fragment contributes very largely
the same k-mers and would double the fetch to sharpen nothing. ENA lists an orphan
`<run>.fastq.gz` *before* `_1`/`_2` for some paired runs, so "the first file" is not the rule
— "the `_1` file, else the first" is.

`ena.fetch_head` asks for the range and stops reading at the limit regardless, closing the
response, so a server that answers 200 with the whole file costs some buffered bytes and not
a download. `sra_fetch._fetch_file` is not reused because it verifies the size and MD5 ENA
promised, and a head is a fragment by design. Every `requests` failure is a `FetchError`
through `redact`; the task fails the run with that sentence and re-raises. A head with no
whole record fails the run rather than handing sendsketch nothing.

### The tool is looked for first, before anything is downloaded

`sketch.sendsketch_path()` is the task's first act after marking the run running — ahead of
the ENA fetch — so a deployment that has not installed `bbmap` learns that without paying for
16 MB. The page asks the same question through `sketch.available()` and puts a banner up, so
it is usually known before a file is even chosen.

### A failed sketch fails the run; no hits does not

A nonzero exit, a timeout, an unreadable JSON, a missing output file — each fails the run
with its own sentence and re-raises, as every task in the suite does. A sketch that ran and
matched nothing is a **finished** run with an empty table and a `note`: it is an answer, and
the page shows it as one.

### "Use as reference" is core's importer, driven from the page

There is no server code for it here. `page.js` makes the two calls the Reference Sequence
tab's accession box makes — `mutintUpload([], {importType: "reference", accessions})`, then
`POST /import/uploads/<id>/finalize` — so permission, the lock and a busy importer are all
core's answers, and the genome arrives exactly as a typed accession would.

---

## What it deliberately does not do

- **No import handler**: the drop is job input, not a mutation file.
- **No nav entry**: the tab is the entry point.
- **No rebuilder, storage kind or export type**: it derives nothing from the mutations, keeps
  its head only until the run ends, and adds no mutation type.
- **No second opinion.** BLAST as a confirmation step on top of the sketch is what the first
  version of this was, and finding (1) above is why there is no second service to fall back
  to. A page that offers two answers that can disagree has to say which one to believe.

---

## Tests

103 tests. Once the plugin is a submodule of `mutint`:

```bash
cd mutint && ./mutint test mutint_refsniff
```

From the suite root, against the uncommitted checkout rather than the submodule clone:

```bash
cd mutint
PYTHONPATH=/path/to/mutint-code/mutint-refsniff ./mutint test mutint_refsniff
```

**Two fakes, and the reason each is one module rather than two.**

`tests/fake_http.py` is a scripted web — ENA's file report, ENA's file bytes, and NCBI
Datasets — as **one** `requests.get`. One, because `requests.get` is the single patch point
core's `sra_fetch.resolve`, this plugin's `ena.fetch_head` and core's
`ncbi_fetch._datasets_json` all reach; patching it twice would leave the inner patch
answering for the outer's service. It wraps core's `_row` and `_Response`, because core's own
fake accepts neither the `headers=` a Range request sends nor a Datasets URL.

`tests/fake_sketch.py` patches `processes.run_tool` — the seam the whole suite shells out
through — to write a canned JSON at whatever path the argv's `out=` names, so a test proves
the argv carries one and the task proves it reads what the tool was told to write. It patches
`tools.require`/`tool_path` beside it, so the suite is green on a machine with no bbmap, and
`assemblies._sleep`, so ten rows do not cost four seconds of real waiting.

`tests/data/sketch_rel606.json` is **unedited output from a real run** — the first 16 MiB of
SRR2584863 read 1, 2026-09-14 — and `test_sketch.py` parses it. Its last two rows are not in
`Matches` order, which is what a test asserts: sorting hits on any one column here would
reorder sendsketch's own ranking.
