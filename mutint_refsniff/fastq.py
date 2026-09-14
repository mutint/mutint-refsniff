"""Reading the head of a FASTQ: is it one, and the first N records of it.

Pure, and raw: four lines a record, `gzip` chosen by suffix, no Biopython -- the shape
mutint-breseq's `pairing.py` and `mate_check.py` read reads in, and for the same reason: the
whole job is a few hundred records off the front of a file, and a parser that validates the
lot would read the lot.

**A truncated tail is expected, not an error.** The page uploads only the first few megabytes
of a file that may be gigabytes, so the last record is usually cut mid-line and a gzip member
ends without its footer. `sample` stops at the first record it cannot read whole and reports
how many it took; `sniff` looks only at the first record, which the slice always holds.
"""

import gzip

FASTQ_SUFFIXES = (".fastq", ".fq", ".fastq.gz", ".fq.gz")

DEFAULT_READS = 200
MIN_READS = 100
MAX_READS = 1000


class ReadCountError(Exception):
    """The Reads box did not hold a count this can be run with."""


def clean_read_count(raw):
    """The Reads box as an int within bounds, or the default for blank."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return DEFAULT_READS
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise ReadCountError("Reads to sample has to be a whole number between %d and %d."
                             % (MIN_READS, MAX_READS))
    if not MIN_READS <= value <= MAX_READS:
        raise ReadCountError("Reads to sample has to be between %d and %d."
                             % (MIN_READS, MAX_READS))
    return value


def is_fastq_name(name):
    return name.lower().endswith(FASTQ_SUFFIXES)


def open_lines(path):
    """The file open for reading bytes, through gzip when its name says so."""
    opener = gzip.open if path.lower().endswith(".gz") else open
    return opener(path, "rb")


def _records(handle):
    """Yield `(header, sequence, plus, quality)` for each whole record, stopping at the first
    that is not one. A cut-off gzip member raises `EOFError` from inside `readline`, which
    ends the file the same way."""
    while True:
        try:
            lines = [handle.readline() for _ in range(4)]
        except (EOFError, OSError, gzip.BadGzipFile):
            return
        if not lines[0]:
            return
        if any(not line.endswith(b"\n") for line in lines):
            # Cut mid-record: the slice ended here.
            return
        header, sequence, plus, quality = (line.rstrip(b"\r\n") for line in lines)
        if not header.startswith(b"@") or not plus.startswith(b"+"):
            return
        if len(sequence) != len(quality):
            return
        yield header, sequence, plus, quality


def sniff(path):
    """None when the file opens as FASTQ, else a sentence saying why not."""
    try:
        with open_lines(path) as handle:
            first = handle.readline()
            if not first.strip():
                return "The file is empty."
            if first.startswith(b">"):
                return "That looks like FASTA, not FASTQ: sequences without qualities."
            if not first.startswith(b"@"):
                return "That does not look like FASTQ: its first line does not start with @."
            record = next(_records(_Rewound(first, handle)), None)
    except (EOFError, OSError, gzip.BadGzipFile) as exc:
        return "The file could not be read as %s: %s" % (
            "gzip" if path.lower().endswith(".gz") else "text", exc)
    if record is None:
        return "That does not look like FASTQ: the first record is not four well-formed lines."
    return None


class _Rewound:
    """A handle whose first `readline` answers a line already taken off it."""

    def __init__(self, first, handle):
        self._first = first
        self._handle = handle

    def readline(self):
        if self._first is not None:
            line, self._first = self._first, None
            return line
        return self._handle.readline()


def sample(path, count, fasta_out):
    """Write the first `count` whole records of `path` to `fasta_out` as FASTA with ids
    `read_1`, `read_2`, .... Returns `(records, bases)`.

    Renumbered ids, because the originals carry spaces, slashes and colons that BLAST's
    query parser reads as something else, and nothing downstream needs the original names.
    """
    records = 0
    bases = 0
    with open_lines(path) as handle, open(fasta_out, "wb") as fasta:
        for _header, sequence, _plus, _quality in _records(handle):
            if records >= count:
                break
            records += 1
            bases += len(sequence)
            fasta.write(b">read_%d\n%s\n" % (records, sequence))
    return records, bases
