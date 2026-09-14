"""Reading the head of a FASTQ: is it one, and how much of one to take.

Pure, and raw: four lines a record, `gzip` chosen by suffix, no Biopython -- the shape
mutint-breseq's `pairing.py` and `mate_check.py` read reads in, and for the same reason: the
question is whether the first record looks right, and a parser that validates the file would
read the file.

**Nothing here selects or rewrites reads.** sendsketch is given the head exactly as it
arrived and reads all of it, so what this module answers is only whether a file is worth
handing over -- a question the view asks of a drop and the task asks of an ENA fetch.

**A truncated tail is expected, not an error.** The page uploads only the first few megabytes
of a file that may be gigabytes, and the task fetches the same slice from ENA, so the last
record is usually cut mid-line and a gzip member ends without its footer. `sniff` looks only
at the first record, which the slice always holds; sendsketch loads every whole record before
the cut and prints a ZLIB traceback about the rest, which is not a failure -- see `sketch.py`.
"""

import gzip

FASTQ_SUFFIXES = (".fastq", ".fq", ".fastq.gz", ".fq.gz")

#: How much of a file the page uploads, and how much of an ENA file the task fetches. A FASTQ
#: can be gigabytes and a sketch of the first 16 MiB is already the answer: measured on a real
#: run, that slice held 135 798 reads and separated E. coli B REL606 from K-12. Here rather
#: than in `views.py` because `tasks.py` needs it too and `views` imports `tasks`.
HEAD_BYTES = 16 * 1024 * 1024


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


def has_a_record(path):
    """Whether `path` holds at least one whole FASTQ record.

    What the task asks of a head fetched from ENA, which nothing looked at before it arrived.
    A drop was sniffed in the view; this is the same question asked of bytes nobody chose.
    """
    try:
        with open_lines(path) as handle:
            return next(_records(handle), None) is not None
    except (EOFError, OSError, gzip.BadGzipFile):
        return False


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
