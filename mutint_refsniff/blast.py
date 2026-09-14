"""NCBI's BLAST Common URL API, with the standard library and nothing else.

Four verbs -- `put`, `status`, `fetch`, `parse` -- and `run`, which strings them together
with a wait between. Every function takes `http=`, a callable `(url, data=None) -> text`,
so the tests hand in a scripted one and nothing here is patched. `default_http` is urllib.

**The usage rules are NCBI's and the numbers are theirs**: no more than one request every
ten seconds, poll a search no more than once a minute, and no more than a hundred searches
a day per site or the site is throttled. One run is one search -- every sampled read goes
into one multi-FASTA `QUERY`, which is what NCBI asks for short sequences -- so the
per-day cap is a cap on runs, and the poll floor is `POLL_FLOOR_SECONDS`.

**Waiting is cancellable in five-second slices.** A search can sit at WAITING for many
minutes, and a job somebody cannot give up on is its own kind of broken; the wait sleeps in
`SLICE_SECONDS` steps and calls `check_cancelled()` between them, which raises. `sleep` and
`clock` are arguments so the tests run in milliseconds and can count the polls.

**Legacy `FORMAT_TYPE=XML`**, deliberately. It is one document that `xml.etree` reads whole;
XML2 and JSON2 come back as a zip of one file per query for a multi-query search, which is
an unpacking step for nothing this needs.

Every request identifies who is asking with `tool=` and `email=` -- NCBI asks for both --
and **never with `api_key`**: the URL API has no such parameter, and a key in a query string
is what `mutint_sample.ncbi.redact` exists to scrub out of messages. Every error here still
goes through it, since the email is in the URLs too. The address is the *person's*, passed
in by the caller from their account, with the deployment's `MUTINT_NCBI_EMAIL` as the
fallback: a search is launched by somebody, and NCBI's question is who to contact about it.
"""

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.etree import ElementTree

from django.conf import settings

from mutint_sample import ncbi

BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"

PROGRAM = "blastn"
#: RefSeq genomes, restricted to Bacteria and Archaea by the Entrez query. The hits are
#: chromosome and plasmid records, so a title names a strain.
DATABASE = "refseq_genomes"
ENTREZ_QUERY = "txid2[ORGN] OR txid2157[ORGN]"
HITLIST_SIZE = 5

POLL_FLOOR_SECONDS = 60
SLICE_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 30 * 60
HTTP_TIMEOUT_SECONDS = 120

_RID = re.compile(r"^\s*RID = (\S+)", re.MULTILINE)
_RTOE = re.compile(r"^\s*RTOE = (\d+)", re.MULTILINE)
_STATUS = re.compile(r"Status=(\w+)")
_HITS = re.compile(r"ThereAreHits=(\w+)")
_ERROR = re.compile(r"<p class=\"error\">(.*?)</p>|Error: ?(.*?)<", re.DOTALL)

#: Where a RefSeq title stops naming the organism and starts describing the record.
_TITLE_TAIL = re.compile(
    r",? (complete (genome|sequence)|chromosome\b|plasmid\b|genome assembly|"
    r"whole genome|DNA, complete|genomic sequence|strain [^,]+ chromosome).*$",
    re.IGNORECASE)


class BlastError(Exception):
    """NCBI refused, failed or lost the search. The message is fit to show."""


def blast_url():
    return getattr(settings, "MUTINT_REFSNIFF_BLAST_URL", BLAST_URL)


def timeout_seconds():
    return getattr(settings, "MUTINT_REFSNIFF_BLAST_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)


def default_email():
    """The deployment's address, for a caller that has no better one."""
    return getattr(settings, "MUTINT_NCBI_EMAIL", "") or ""


def identity(email=None):
    """`tool` and `email`, as NCBI asks; no `api_key`, which this API does not take."""
    params = {"tool": ncbi.TOOL_NAME}
    email = email or default_email()
    if email:
        params["email"] = email
    return params


def _sleep(seconds):
    time.sleep(seconds)


def _clock():
    return time.monotonic()


def default_http(url, data=None):
    """One request over urllib: GET, or POST when `data` is given. Returns the body as text."""
    body = None
    headers = {"User-Agent": "%s (%s)" % (ncbi.TOOL_NAME, identity().get("email", "no email"))}
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise BlastError("NCBI answered HTTP %s to %s." % (exc.code, ncbi.redact(url)))
    except (urllib.error.URLError, OSError) as exc:
        raise BlastError("NCBI could not be reached: %s" % ncbi.redact(exc))


def _get(http, email=None, **params):
    params.update(identity(email))
    return http(blast_url() + "?" + urllib.parse.urlencode(params))


def put(fasta_text, http=None, email=None):
    """Submit one megablast search. Returns `(rid, rtoe_seconds)`."""
    http = default_http if http is None else http
    data = {"CMD": "Put", "PROGRAM": PROGRAM, "MEGABLAST": "on", "DATABASE": DATABASE,
            "ENTREZ_QUERY": ENTREZ_QUERY, "HITLIST_SIZE": str(HITLIST_SIZE),
            "QUERY": fasta_text}
    data.update(identity(email))
    page = http(blast_url(), data)
    rid = _RID.search(page)
    if not rid:
        error = _ERROR.search(page)
        detail = ""
        if error:
            detail = re.sub(r"\s+", " ", (error.group(1) or error.group(2) or "")).strip()
        raise BlastError("NCBI did not accept the search%s" % (": " + detail if detail else "."))
    rtoe = _RTOE.search(page)
    return rid.group(1), int(rtoe.group(1)) if rtoe else 0


def status(rid, http=None, email=None):
    """`(status, there_are_hits)` -- WAITING, READY, FAILED or UNKNOWN, and a bool."""
    http = default_http if http is None else http
    page = _get(http, email, CMD="Get", FORMAT_OBJECT="SearchInfo", RID=rid)
    found = _STATUS.search(page)
    hits = _HITS.search(page)
    return (found.group(1).upper() if found else "UNKNOWN",
            bool(hits and hits.group(1).lower() == "yes"))


def fetch(rid, http=None, email=None):
    """The finished search as legacy BLAST XML."""
    http = default_http if http is None else http
    return _get(http, email, CMD="Get", FORMAT_TYPE="XML", RID=rid)


def parse(xml_text):
    """`[{query, accession, title, identity, align_len, bits}]`: the best HSP of the first hit
    of every query that had one. Raises `BlastError` for something that is not BLAST XML."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise BlastError("NCBI's answer could not be read as BLAST XML: %s" % exc)
    rows = []
    for iteration in root.iter("Iteration"):
        hit = iteration.find("./Iteration_hits/Hit")
        if hit is None:
            continue
        hsp = hit.find("./Hit_hsps/Hsp")
        if hsp is None:
            continue
        rows.append({
            "query": iteration.findtext("Iteration_query-def", ""),
            "accession": hit.findtext("Hit_accession", ""),
            "title": hit.findtext("Hit_def", ""),
            "identity": int(hsp.findtext("Hsp_identity", "0") or 0),
            "align_len": int(hsp.findtext("Hsp_align-len", "0") or 0),
            "bits": float(hsp.findtext("Hsp_bit-score", "0") or 0),
        })
    return rows


def organism_from_title(title):
    """`Escherichia coli str. K-12 substr. MG1655, complete genome` -> the part before the
    record's description."""
    return _TITLE_TAIL.sub("", title.strip()).strip(" ,")


def tally(rows, total_reads):
    """Group best hits by accession: `[{accession, title, organism, reads, fraction,
    mean_identity}]`, most reads first, ties by identity."""
    groups = {}
    for row in rows:
        entry = groups.setdefault(row["accession"], {
            "accession": row["accession"], "title": row["title"],
            "organism": organism_from_title(row["title"]),
            "reads": 0, "_identity": 0.0})
        entry["reads"] += 1
        if row["align_len"]:
            entry["_identity"] += row["identity"] / float(row["align_len"])
    result = []
    for entry in groups.values():
        reads = entry.pop("reads")
        summed = entry.pop("_identity")
        entry["reads"] = reads
        entry["fraction"] = (reads / float(total_reads)) if total_reads else 0.0
        entry["mean_identity"] = round(100.0 * summed / reads, 2) if reads else 0.0
        result.append(entry)
    result.sort(key=lambda e: (-e["reads"], -e["mean_identity"], e["accession"]))
    return result


def wait(rid, rtoe, http=None, *, email=None, check_cancelled=lambda: None, sleep=None,
         clock=None, timeout=None, log_write=lambda text: None):
    """Block until the search is READY. Returns whether there are hits.

    Never asks more often than `POLL_FLOOR_SECONDS`; the first wait is the longer of that
    and NCBI's own estimate. Sleeps in `SLICE_SECONDS` steps, calling `check_cancelled()`
    between them. Raises `BlastError` on FAILED, UNKNOWN or the timeout.
    """
    # Resolved here rather than as defaults, so a test that patches this module's `_sleep`,
    # `_clock` or `default_http` reaches a call made through `run`.
    http = default_http if http is None else http
    sleep = _sleep if sleep is None else sleep
    clock = _clock if clock is None else clock
    timeout = timeout_seconds() if timeout is None else timeout
    started = clock()
    delay = max(POLL_FLOOR_SECONDS, int(rtoe or 0))
    while True:
        deadline = clock() + delay
        while clock() < deadline:
            check_cancelled()
            sleep(min(SLICE_SECONDS, max(0.0, deadline - clock())))
        check_cancelled()
        state, hits = status(rid, http, email)
        log_write("BLAST %s: %s" % (rid, state))
        if state == "READY":
            return hits
        if state == "FAILED":
            raise BlastError("NCBI reports that BLAST search %s failed." % rid)
        if state == "UNKNOWN":
            raise BlastError("NCBI no longer knows BLAST search %s; it may have expired." % rid)
        if clock() - started >= timeout:
            raise BlastError("BLAST search %s was still running after %d seconds."
                             % (rid, timeout))
        delay = POLL_FLOOR_SECONDS


def run(fasta_text, total_reads, http=None, *, email=None, check_cancelled=lambda: None,
        sleep=None, clock=None, timeout=None, log_write=lambda text: None):
    """Submit, wait, fetch, tally. Returns `{rid, hits, matched}` where `matched` is how
    many queries had a hit at all. `email` is who NCBI may contact about the search."""
    http = default_http if http is None else http
    rid, rtoe = put(fasta_text, http, email)
    log_write("BLAST submitted as %s; NCBI estimates %d seconds." % (rid, rtoe))
    if not wait(rid, rtoe, http, email=email, check_cancelled=check_cancelled, sleep=sleep,
                clock=clock, timeout=timeout, log_write=log_write):
        log_write("BLAST %s finished with no hits." % rid)
        return {"rid": rid, "hits": [], "matched": 0}
    check_cancelled()
    rows = parse(fetch(rid, http, email))
    log_write("BLAST %s: %d of %d reads matched something." % (rid, len(rows), total_reads))
    return {"rid": rid, "hits": tally(rows, total_reads), "matched": len(rows)}
