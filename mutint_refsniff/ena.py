"""The head of a run's reads from ENA, for a run named by accession rather than dropped.

**No tool, on purpose.** The obvious way to take the first N spots of an SRA run is
`fastq-dump -X N`, which is a conda package to provision, a first-run configuration step, and
a second sampling path beside the one the drop already has. ENA mirrors every run's reads as
ordinary `fastq.gz` over HTTPS and honours `Range` (measured: `206 Partial Content`,
`Accept-Ranges: bytes`), so the first `HEAD_BYTES` of the file is one request, and what
arrives is exactly what the page uploads for a drop -- a gzip member cut short, which
sendsketch reads to the last whole record. Core made the same choice for whole runs in
`mutint_import.sra_fetch`, whose `resolve` is what finds the file here.

`sra_fetch._fetch_file` is not reused because it verifies the size and MD5 ENA promised, and
a head is a fragment by design.
"""

import requests

from mutint_import.sra_fetch import FetchError, timeout_seconds
from mutint_jobs.processes import Cancelled
from mutint_sample.ncbi import redact

CHUNK_BYTES = 1 << 20

_READ_ONE_SUFFIXES = ("_1.fastq.gz", "_1.fq.gz", "_1.fastq", "_1.fq")


def first_read_file(plan):
    """`(run, file entry)` a search samples from: the plan's first run, and its read-1 file.

    A paired run is listed as `_1` and `_2`, and only `_1` is wanted: a sketch is a set of
    k-mer hashes, and a mate covering the same fragment contributes very largely the same
    ones -- two files would double the fetch to sharpen nothing. ENA lists an orphan
    `<run>.fastq.gz` *before* `_1`/`_2` for some paired runs, so "the first file" is not the
    rule -- "the `_1` file, else the first" is. `resolve` guarantees at least one run with at
    least one file.
    """
    run = plan.runs[0]
    for entry in run.files:
        if entry["name"].lower().endswith(_READ_ONE_SUFFIXES):
            return run, entry
    return run, run.files[0]


def fetch_head(url, path, limit, name, is_cancelled=None, report=None):
    """Stream at most `limit` bytes of `url` to `path`. Returns the bytes written.

    Asks for the range and stops reading at `limit` regardless: a server that ignores
    `Range` answers 200 with the whole file, and closing the response after `limit` bytes is
    what keeps that from being a download. `is_cancelled` is asked between chunks -- at most
    sixteen of them, and the answer is one indexed read, so there is no poll gap -- and a
    True answer raises `Cancelled`, the class `run_tool` raises, so a task fetching and a
    task shelling out are cancelled the same way. Every `requests` failure is a `FetchError`
    with the sentence through `redact`, the rule for anything that puts a request failure in
    front of a person.
    """
    if report is not None:
        report("Fetching the first %d MB of %s from ENA…" % (limit // (1024 * 1024), name))
    try:
        response = requests.get(url, headers={"Range": "bytes=0-%d" % (limit - 1)},
                                stream=True, timeout=timeout_seconds())
    except requests.RequestException as error:
        raise FetchError("Could not reach ENA to fetch %s: %s" % (name, redact(error)))

    written = 0
    try:
        if response.status_code not in (200, 206):
            raise FetchError("ENA answered HTTP %s fetching %s." % (response.status_code, name))
        with open(path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                if is_cancelled is not None and is_cancelled():
                    raise Cancelled("This run was cancelled while its reads were fetched.")
                if not chunk:
                    continue
                room = limit - written
                if len(chunk) > room:
                    chunk = chunk[:room]
                handle.write(chunk)
                written += len(chunk)
                if written >= limit:
                    break
    except requests.RequestException as error:
        raise FetchError("The fetch of %s stopped partway: %s" % (name, redact(error)))
    finally:
        response.close()
    return written
