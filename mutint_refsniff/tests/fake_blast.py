"""A scripted NCBI: the pages the URL API answers with, and a clock that never waits.

`FakeHttp` is the `http` callable `blast.py` takes. It records every request, answers a Put
with a page carrying a RID and RTOE, answers `SearchInfo` with WAITING `waits` times and
then READY, and answers a fetch with `XML`. `FakeClock` stands in for `_sleep`/`_clock`:
sleeping advances it, so the poll floor is honoured in fake seconds.

`patched()` installs both on the module for the length of a `with`, which is what the view
and task tests use: the task reaches NCBI only through the module's names.
"""

import contextlib
from unittest import mock
from urllib.parse import parse_qs, urlparse

from mutint_refsniff import blast

RID = "ABC123XYZ"

PUT_PAGE = """<html><body>
<!--QBlastInfoBegin
    RID = %s
    RTOE = 23
QBlastInfoEnd
--></body></html>""" % RID

REFUSED_PAGE = """<html><body><p class="error">Message ID#24 Error: Failed to read the Blast
query: Nucleotide FASTA provided for protein sequence</p></body></html>"""


def info_page(state, hits=None):
    """NCBI's SearchInfo page: `ThereAreHits` appears only once the search is READY."""
    lines = ["<!--QBlastInfoBegin", "\tStatus=%s" % state]
    if hits is not None:
        lines.append("\tThereAreHits=%s" % hits)
    lines += ["QBlastInfoEnd", "-->"]
    return "\n".join(lines)


def _iteration(number, query, hits):
    body = ["<Iteration><Iteration_iter-num>%d</Iteration_iter-num>"
            "<Iteration_query-def>%s</Iteration_query-def><Iteration_hits>" % (number, query)]
    for accession, title, identity, length in hits:
        body.append(
            "<Hit><Hit_num>1</Hit_num><Hit_id>ref|%s|</Hit_id><Hit_def>%s</Hit_def>"
            "<Hit_accession>%s</Hit_accession><Hit_len>4641652</Hit_len><Hit_hsps>"
            "<Hsp><Hsp_num>1</Hsp_num><Hsp_bit-score>277.0</Hsp_bit-score>"
            "<Hsp_identity>%d</Hsp_identity><Hsp_align-len>%d</Hsp_align-len></Hsp>"
            "<Hsp><Hsp_num>2</Hsp_num><Hsp_bit-score>50.0</Hsp_bit-score>"
            "<Hsp_identity>30</Hsp_identity><Hsp_align-len>40</Hsp_align-len></Hsp>"
            "</Hit_hsps></Hit>" % (accession, title, accession, identity, length))
    body.append("</Iteration_hits></Iteration>")
    return "".join(body)


K12 = ("NC_000913.3", "Escherichia coli str. K-12 substr. MG1655, complete genome")
REL606 = ("NC_012967.1", "Escherichia coli B str. REL606, complete genome")

XML = ('<?xml version="1.0"?><!DOCTYPE BlastOutput PUBLIC "-//NCBI//NCBI BlastOutput/EN" '
       '"http://www.ncbi.nlm.nih.gov/dtd/NCBI_BlastOutput.dtd"><BlastOutput>'
       "<BlastOutput_program>blastn</BlastOutput_program><BlastOutput_iterations>"
       + _iteration(1, "read_1", [K12 + (150, 150), REL606 + (149, 150)])
       + _iteration(2, "read_2", [K12 + (148, 150)])
       + _iteration(3, "read_3", [REL606 + (150, 150)])
       + _iteration(4, "read_4", [])
       + "</BlastOutput_iterations></BlastOutput>")


class FakeHttp:
    def __init__(self, waits=1, xml=XML, put_page=PUT_PAGE, final="READY", hits="yes",
                 forever=False):
        self.waits = waits
        self.xml = xml
        self.put_page = put_page
        self.final = final
        self.hits = hits
        self.forever = forever
        self.requests = []
        self.status_calls = 0

    def __call__(self, url, data=None):
        self.requests.append((url, data))
        if data is not None:
            return self.put_page
        query = parse_qs(urlparse(url).query)
        if query.get("FORMAT_OBJECT") == ["SearchInfo"]:
            self.status_calls += 1
            if self.forever or self.status_calls <= self.waits:
                return info_page("WAITING")
            return info_page(self.final, self.hits)
        if query.get("FORMAT_TYPE") == ["XML"]:
            return self.xml
        raise AssertionError("unexpected request: %s" % url)

    @property
    def put_data(self):
        return [data for _url, data in self.requests if data is not None]

    @property
    def gets(self):
        return [url for url, data in self.requests if data is None]


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


@contextlib.contextmanager
def patched(http=None, clock=None):
    """`blast` talking to `http` and sleeping on `clock`, for a `with` block."""
    http = FakeHttp() if http is None else http
    clock = FakeClock() if clock is None else clock
    with mock.patch.object(blast, "default_http", http), \
            mock.patch.object(blast, "_sleep", clock.sleep), \
            mock.patch.object(blast, "_clock", clock.clock):
        yield http, clock
