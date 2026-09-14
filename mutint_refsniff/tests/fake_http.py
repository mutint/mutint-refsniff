"""A scripted web: ENA's file report, ENA's file bytes, and NCBI Datasets, as one
`requests.get`.

**One fake because there is one patch point.** `requests.get` is what core's
`sra_fetch.resolve`, this plugin's `ena.fetch_head` and core's `ncbi_fetch._datasets_json`
all reach -- each imports the module rather than the function -- so patching it twice would
mean the inner patch answering for the outer's service. Three endpoints, one `get`.

What is reused from core's `mutint_import.tests.test_sra_fetch.fake_ena` is `_row` (one
read-run report row, with the MD5s ENA would carry) and `_Response`; its own `get` is not,
because it accepts neither the `headers=` the Range request sends nor a Datasets URL.
"""

from unittest import mock

import requests

from mutint_import import sra_fetch
from mutint_import.tests.test_sra_fetch import _Response, _row  # noqa: F401

from mutint_refsniff.assemblies import DATASETS_TAXON_REPORTS

ROW = _row

#: Everything before the taxid, so a URL can be recognised and the taxid read off it.
DATASETS_PREFIX = DATASETS_TAXON_REPORTS.split("%s")[0]


def report(accession="GCF_000017985.1", level="Complete Genome", category=None,
           organism="Escherichia coli B str. REL606"):
    """One NCBI Datasets assembly report, shaped as `assemblies._rank` reads it."""
    info = {"assembly_level": level, "assembly_status": "current"}
    if category:
        info["refseq_category"] = category
    return {"accession": accession, "assembly_info": info,
            "organism": {"organism_name": organism}}


def fake_http(rows_by_token=None, bytes_by_name=None, assemblies=None, chunk=16, status=206,
              raise_after=None, unreachable=False, datasets_status=200):
    """`requests.get` answering all three endpoints.

    `rows_by_token` is ENA's file report; `bytes_by_name` the file bytes, in `chunk`-sized
    pieces so a cancellation has somewhere to land; `assemblies` maps a taxid (as a string)
    to a list of `report()` dicts, and a taxid not in it answers no reports at all.

    `status` is what a file GET answers (206 honours the range; 200 ignores it, and serves
    everything); `raise_after` raises a `ConnectionError` after that many chunks;
    `unreachable` makes the file GET itself raise; `datasets_status` is what Datasets
    answers. Records every Range header in `ranges`, every file requested in `files`, and
    every taxid looked up in `taxids`.
    """
    rows_by_token = rows_by_token or {}
    bytes_by_name = bytes_by_name or {}
    assemblies = {} if assemblies is None else assemblies
    ranges = []
    files = []
    taxids = []

    def get(url, params=None, timeout=None, stream=False, headers=None):
        if url == sra_fetch.FILEREPORT_URL:
            return _Response(payload=rows_by_token.get(params["accession"], []))
        if url.startswith(DATASETS_PREFIX):
            taxid = url[len(DATASETS_PREFIX):].split("/")[0]
            taxids.append(taxid)
            if datasets_status != 200:
                return _Response(status_code=datasets_status, payload={})
            return _Response(payload={"reports": assemblies.get(taxid, [])})
        name = url.rsplit("/", 1)[1]
        files.append(name)
        ranges.append((headers or {}).get("Range"))
        if unreachable:
            raise requests.ConnectionError(
                "ftp.sra.ebi.ac.uk refused (https://x/?api_key=SECRET&email=o@e.com)")
        if name not in bytes_by_name:
            return _Response(status_code=404)
        data = bytes_by_name[name]
        return _Response(status_code=status,
                         chunks=[data[i:i + chunk] for i in range(0, len(data), chunk)],
                         raise_after=raise_after)

    get.ranges = ranges
    get.files = files
    get.taxids = taxids
    return get


def patched(rows_by_token=None, bytes_by_name=None, **kwargs):
    """`with patched(...) as get:` -- `requests.get` replaced for the block."""
    return mock.patch("requests.get", new=fake_http(rows_by_token, bytes_by_name, **kwargs))
