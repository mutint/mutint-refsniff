"""What reaches `sendsketch.sh`'s command line, and what to make of the JSON it writes.

Pure, in the shape `mutint_isescan.runner` is: the rules most likely to be changed by
accident are the argv and the reading of the output, and both are testable without the tool.
It runs nothing -- `mutint_jobs.processes.run_tool` does -- and it reaches no network, which
is the point of keeping the NCBI Datasets lookup in `assemblies.py` rather than here.

**What leaves the deployment is a sketch, not reads.** `sendsketch.sh` hashes every k-mer of
the input, keeps the smallest few thousand hashes, and sends *those* to JGI's RefSeq sketch
server (`address=refseq`). A few kilobytes of integers go out and a table of genomes comes
back. This is the reason the page can search the whole 16 MB head rather than a few hundred
reads, and the reason core's rule that sequence does not leave the deployment is bent rather
than set aside.

**The four settings on the command line are each load-bearing:**

- `level=1` is one record per **strain**. The default, `level=2`, collapses hits to one per
  species, which answers "E. coli" to a question whose whole point is which E. coli.
- `-Xmx1g` because the default auto-picks about 5.6 GB of heap, and this runs beside a pool
  of background workers and a database on the same machine. A 16 MB head sketches in it with
  room to spare.
- `records=10` is how many rows come back, which is how many rows the page shows.
- `format=json printname0=t printtaxa=t` is what makes the output parseable and makes each
  hit carry the `TaxID` `assemblies.assembly_for` needs and the `seqName` its accession is
  read off.

**A gzip member cut short is expected, not an error.** The page uploads and the task fetches
only the first `fastq.HEAD_BYTES`, so the last member has no footer; sendsketch prints
`java.io.EOFException: Unexpected end of ZLIB input stream` into the log, loads every whole
record before that point, and exits 0. Measured on a real 16 MiB head: 135 798 reads, 3.4
seconds, exit 0. **Do not read that traceback in the log as a failure**; the returncode is
what says whether the run worked.
"""

import json
import os

from django.conf import settings

from mutint_common import tools
from mutint_common.tools import ToolMissing

#: The script bioconda's `bbmap` package installs.
SENDSKETCH = 'sendsketch.sh'

#: JGI's RefSeq sketch server, the one sendsketch knows by name.
ADDRESS = 'refseq'

#: One record per strain. See the module docstring: `level=2` answers the species.
LEVEL = '1'

#: How many genomes come back, and how many rows the page shows.
RECORDS = 10

#: Java heap. Not the default, deliberately; see the module docstring.
HEAP = '1g'

#: Where the run's answer is written, inside the run's own directory.
SKETCH_FILENAME = 'sketch.json'

#: Where bioconda's `openjdk` puts the JVM inside the tools prefix. **Not** `bin/`: that
#: package installs nothing there at all and relies on a conda *activation script* to export
#: `JAVA_HOME`, which nothing in this suite runs. See `tool_environment`.
JVM_DIR = os.path.join('lib', 'jvm')

DEFAULT_TIMEOUT_SECONDS = 10 * 60

#: How a draft assembly's one contig announces itself in a `seqName`.
_SHOTGUN = 'whole genome shotgun'


class SketchError(Exception):
    """sendsketch answered something this cannot read. The message is fit to show."""


def sendsketch_path():
    """Where `sendsketch.sh` is, or raise `ToolMissing` naming what installs it."""
    return tools.require(SENDSKETCH)


def available():
    """`(True, '')` when sendsketch can be run here, else `(False, <why>)`."""
    if tools.tool_path(SENDSKETCH):
        return True, ''
    try:
        tools.require(SENDSKETCH)
    except ToolMissing as missing:
        return False, str(missing)
    return False, '%s is not installed.' % SENDSKETCH


def timeout_seconds():
    return getattr(settings, 'MUTINT_REFSNIFF_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS)


def tool_environment(env=None):
    """`env` with the managed tools directory -- **and its JVM** -- first on PATH.

    The tools-directory half is `mutint_isescan.runner`'s, copied; three producers is the
    argument for lifting that into `mutint_common.tools`, which nobody has done.

    **The JVM half is this plugin's and is the part that is easy to get wrong.**
    `sendsketch.sh` is a shell wrapper that ends in a bare `java`, and bioconda's `openjdk`
    puts **nothing** in the prefix's `bin/` -- the JVM is at `lib/jvm/bin/java` and conda
    exports `JAVA_HOME` from an *activation script*, which `run_tool` does not run. So
    prepending `env/tools/bin` alone provisions everything except the one binary the tool
    actually executes, and the run then succeeds or fails on whether the **host** happens to
    have a `java`. It does on a developer Mac (`/usr/bin/java`), which is precisely why this
    cannot be left to be noticed later: bbmap needs 17+, and a host with 8, or with none, is
    a failure no test on a machine that has one would ever show.

    So the JVM's `bin` goes on PATH too, ahead of the host's, and `JAVA_HOME` and
    `JAVA_LD_LIBRARY_PATH` are set to what `openjdk_activate.sh` would have set them to.
    Gated on the directory existing, so a prefix without bbmap leaves a host's own `JAVA_HOME`
    alone rather than pointing it at nothing.
    """
    env = dict(os.environ if env is None else env)
    directory = tools.tools_dir()
    if not directory:
        return env
    entries = [os.path.join(directory, 'bin')]
    jvm = os.path.join(directory, JVM_DIR)
    if os.path.isdir(os.path.join(jvm, 'bin')):
        entries.append(os.path.join(jvm, 'bin'))
        env['JAVA_HOME'] = jvm
        env['JAVA_LD_LIBRARY_PATH'] = os.path.join(jvm, 'lib', 'server')
    existing = env.get('PATH', '')
    env['PATH'] = os.pathsep.join(entries + ([existing] if existing else []))
    return env


def build_argv(sendsketch, reads_path, out_path, records=RECORDS, heap=HEAP):
    """sendsketch's command line. `out=` rather than the log, so the answer is a file."""
    return [sendsketch, '-Xmx%s' % heap, 'in=%s' % reads_path, 'address=%s' % ADDRESS,
            'level=%s' % LEVEL, 'records=%d' % records, 'format=json',
            'printname0=t', 'printtaxa=t', 'out=%s' % out_path]


def _split_seq_name(seq_name):
    """`tid|413997|NC_012967.1 Escherichia coli B str. REL606, complete genome` ->
    `('NC_012967.1', 'Escherichia coli B str. REL606, complete genome')`.

    The nucleotide accession is the token after the second `|`. For a **draft** assembly it
    is one contig of many -- `NZ_WIDO01000031.1 ... whole genome shotgun sequence` -- which
    is why `parse` marks those and why the page prefers the assembly accession.
    """
    parts = seq_name.split('|', 2)
    tail = parts[2] if len(parts) > 2 else seq_name
    accession, _space, title = tail.partition(' ')
    return accession.strip(), title.strip()


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse(json_text):
    """sendsketch's JSON as `{'reads', 'bases', 'hits': [...]}`.

    Every top-level value that is a **dict** is a hit, keyed by the strain's name; the scalar
    keys beside them describe the query. That is sendsketch's own shape and it is what makes
    "which keys are hits" answerable without a list of the ones that are not.

    Hits keep the order sendsketch wrote them, which is its ranking -- by its own score, not
    by any single column here. Sorting on `matches` or `ani` would reorder the tail.
    """
    try:
        payload = json.loads(json_text)
    except ValueError as bad:
        raise SketchError('sendsketch did not write readable JSON: %s' % bad)
    if not isinstance(payload, dict):
        raise SketchError('sendsketch wrote JSON this does not recognise.')

    hits = []
    for name, value in payload.items():
        if not isinstance(value, dict):
            continue
        accession, title = _split_seq_name(value.get('seqName') or '')
        taxid = value.get('TaxID')
        hits.append({
            'name': value.get('taxName') or name,
            'taxid': int(taxid) if isinstance(taxid, int) else 0,
            'accession': accession,
            'title': title,
            'taxonomy': value.get('taxonomy') or '',
            'ani': _number(value.get('ANI')),
            'completeness': _number(value.get('Complt')),
            'contamination': _number(value.get('Contam')),
            'matches': int(_number(value.get('Matches'))),
            'unique': int(_number(value.get('Unique'))),
            'draft': _SHOTGUN in title.lower(),
            'assembly': '',
            'import_accession': '',
        })
    return {
        'reads': int(_number(payload.get('Seqs'))),
        'bases': int(_number(payload.get('Bases'))),
        'hits': hits,
    }


def import_accession(hit):
    """The accession **Use as reference** should import for `hit`, or `''` for none.

    The assembly when one was found, because an assembly resolves to every sequence it is
    made of -- the chromosome *and* its plasmids -- which is what breseq wants. Failing that,
    the nucleotide accession, but only for a complete genome: a draft's `seqName` names one
    contig, and importing one contig of a 276-contig assembly is worse than offering nothing.
    """
    if hit.get('assembly'):
        return hit['assembly']
    if hit.get('draft'):
        return ''
    return hit.get('accession') or ''
