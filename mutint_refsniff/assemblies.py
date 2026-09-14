"""From a sketch hit's taxid to the RefSeq assembly a person would actually import.

**Why this step exists at all.** A sketch hit names a *sequence*: `seqName` carries one
nucleotide accession, which for a complete genome is the chromosome -- without its plasmids
-- and for a draft is one contig out of hundreds. What an experiment's reference should be is
the **assembly**: core's accession import resolves a `GCF_`/`GCA_` token to every sequence it
is made of and establishes them together, which is the reference breseq wants. So each hit's
taxid is turned into an assembly accession here, and that is what **Use as reference**
imports.

**NCBI Datasets is the only API that answers it**, and core already talks to it for the
other direction -- assembly to sequences, in `mutint_import.ncbi_fetch`. `_datasets_json` is
reused rather than re-written: it carries the API key in the header Datasets wants, maps 404,
429 and a non-JSON body to `FetchError`, and redacts the key out of every message. Reaching
past the leading underscore is deliberate and is the coupling to notice if that module is
reorganised; the alternative is a second HTTP client for one GET.

**A failure here is not a failed run.** The sketch is the answer; the assembly is a
convenience on top of it. Every lookup that fails leaves `assembly` empty, says so in the
job log, and the row still renders -- falling back to the nucleotide accession for a complete
genome (`sketch.import_accession`).

**Only strain-level taxids.** `level=1` hits carry one, which is what makes this worth
asking: taxid 413997 has exactly one RefSeq assembly, while the *species* taxid 562 has
52 489. Nothing here guards against being handed a species taxid, because nothing produces
one.
"""

import logging
import time

from mutint_import.ncbi_fetch import FetchError, _datasets_json

from mutint_refsniff.sketch import import_accession

logger = logging.getLogger('mutint_refsniff.assemblies')

DATASETS_TAXON_REPORTS = (
    'https://api.ncbi.nlm.nih.gov/datasets/v2/genome/taxon/%s/dataset_report')

#: How many assemblies to look at for one taxid. A strain taxid usually has one or two; this
#: is a ceiling on the ranking below rather than a page loop, because the choice between the
#: twentieth and the fortieth assembly of a strain is not one this can make well anyway.
PAGE_SIZE = 5

#: Seconds between lookups, the same value and the same reasoning as
#: `ncbi_fetch.POLITE_DELAY_SECONDS`: ten rows means ten GETs in a row, and NCBI's keyless
#: limit is three a second.
POLITE_DELAY_SECONDS = 0.4

#: What NCBI calls an assembly built to a single contiguous sequence per replicon.
COMPLETE = 'Complete Genome'

_PREFERRED_CATEGORIES = ('reference genome', 'representative genome')


def _rank(index, report):
    """Sort key: complete genomes first, then NCBI's own reference/representative, then the
    order Datasets listed them in."""
    info = report.get('assembly_info') or {}
    complete = (info.get('assembly_level') or '') == COMPLETE
    preferred = (info.get('refseq_category') or '').lower() in _PREFERRED_CATEGORIES
    return (0 if complete else 1, 0 if preferred else 1, index)


def assembly_for(taxid):
    """The best current RefSeq assembly for `taxid` as `{'accession', 'level', 'organism'}`,
    or `None` when NCBI lists none. Raises `FetchError` for anything NCBI did or did not do.
    """
    if not taxid:
        return None
    payload = _datasets_json(DATASETS_TAXON_REPORTS % taxid,
                             {'filters.assembly_source': 'refseq', 'page_size': PAGE_SIZE},
                             str(taxid))
    reports = payload.get('reports') or []
    if not reports:
        return None
    best = sorted(enumerate(reports), key=lambda pair: _rank(*pair))[0][1]
    info = best.get('assembly_info') or {}
    return {
        'accession': best.get('accession') or '',
        'level': info.get('assembly_level') or '',
        'organism': (best.get('organism') or {}).get('organism_name') or '',
    }


def _sleep(seconds):
    time.sleep(seconds)


def annotate(hits, report=None, sleep=None):
    """Fill each hit's `assembly` and `import_accession` in place. Returns `hits`.

    One GET per hit, `POLITE_DELAY_SECONDS` apart -- not before the first, so a single hit
    costs no wait. A hit NCBI has no assembly for, or one the lookup could not reach, keeps
    an empty `assembly` and falls back through `sketch.import_accession`.

    **Deliberately not cancellable.** Ten lookups is about six seconds of requests and polite
    delays, at the very end of a job whose two long steps -- the ENA fetch and sendsketch --
    are both polled. A `check` argument here would be a third cancellation seam for a wait
    nobody would reach the button in time for.

    `sleep` is resolved from this module at call time rather than bound as a default, so a
    test patching `assemblies._sleep` reaches a call the **task** made and not only one it
    made itself -- which is the whole reason ten rows do not cost four seconds of real
    waiting in the suite.
    """
    sleep = _sleep if sleep is None else sleep
    for index, hit in enumerate(hits):
        if index:
            sleep(POLITE_DELAY_SECONDS)
        try:
            found = assembly_for(hit.get('taxid'))
        except FetchError as failed:
            # Already redacted by `_datasets_json`.
            logger.info('no assembly for taxid %s: %s', hit.get('taxid'), failed)
            if report is not None:
                report('Could not look up an assembly for %s: %s' % (hit.get('name'), failed))
            found = None
        if found:
            hit['assembly'] = found['accession']
        hit['import_accession'] = import_accession(hit)
    return hits
