"""Reading the head of a FASTQ: names, sniffing, and a file cut mid-record."""

import gzip
import os
import shutil
import tempfile

from django.test import SimpleTestCase

from mutint_refsniff import fastq

RECORD = b"@r%d some description\nACGTACGTAC\n+\nIIIIIIIIII\n"


def records(count):
    return b"".join(RECORD % n for n in range(1, count + 1))


class NameTestCase(SimpleTestCase):
    def test_the_four_suffixes_and_nothing_else(self):
        for name in ("a.fastq", "a.fq", "A.FASTQ.GZ", "reads_1.fq.gz"):
            self.assertTrue(fastq.is_fastq_name(name), name)
        for name in ("a.fasta", "a.gz", "a.fastq.zip", "a.txt", "fastq"):
            self.assertFalse(fastq.is_fastq_name(name), name)


class FileTestCase(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _write(self, name, data, gz=False):
        path = os.path.join(self.dir, name)
        opener = gzip.open if gz else open
        with opener(path, "wb") as handle:
            handle.write(data)
        return path

    def test_sniff_accepts_plain_and_gzipped_fastq(self):
        self.assertIsNone(fastq.sniff(self._write("a.fastq", records(3))))
        self.assertIsNone(fastq.sniff(self._write("a.fastq.gz", records(3), gz=True)))

    def test_sniff_names_what_is_wrong(self):
        self.assertIn("FASTA", fastq.sniff(self._write("a.fastq", b">x\nACGT\n")))
        self.assertIn("empty", fastq.sniff(self._write("a.fastq", b"")))
        self.assertIn("start with @", fastq.sniff(self._write("a.fastq", b"hello\nworld\n")))
        # A gzip name over plain bytes.
        self.assertIn("gzip", fastq.sniff(self._write("a.fastq.gz", records(2))))
        # A header with nothing after it.
        self.assertIn("four", fastq.sniff(self._write("a.fastq", b"@r1\nACGT\n")))

    def test_has_a_record(self):
        self.assertTrue(fastq.has_a_record(self._write("a.fastq", records(2))))
        self.assertTrue(fastq.has_a_record(self._write("b.fastq.gz", records(2), gz=True)))
        # Cut before the first record is whole, which is what a Range fetch of a tiny file
        # can leave; and a file that is not FASTQ at all.
        self.assertFalse(fastq.has_a_record(self._write("c.fastq", b"@r1\nACG")))
        self.assertFalse(fastq.has_a_record(self._write("d.fastq", b"")))
        self.assertFalse(fastq.has_a_record(self._write("e.fastq.gz", b"not gzip at all")))

    def test_a_gzip_member_cut_short_still_reads_its_whole_records(self):
        """The page uploads the first 16 MB of a gzip and the task fetches the same slice,
        so the last member has no footer. sendsketch loads what is whole and prints a ZLIB
        traceback about the rest; this is the same question asked here."""
        whole = gzip.compress(records(400))
        path = self._write("a.fastq.gz", whole[: len(whole) // 2])
        self.assertIsNone(fastq.sniff(path))
        self.assertTrue(fastq.has_a_record(path))

    def test_the_head_is_sixteen_mebibytes(self):
        """Named in three places -- the page's slice, the Range request and the sentence a
        failed fetch prints -- and measured: that slice of a real run held 135 798 reads."""
        self.assertEqual(16 * 1024 * 1024, fastq.HEAD_BYTES)
