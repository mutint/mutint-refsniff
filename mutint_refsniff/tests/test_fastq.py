"""Reading the head of a FASTQ: names, sniffing, and sampling a cut-off file."""

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

    def test_the_read_count_box(self):
        self.assertEqual(fastq.DEFAULT_READS, fastq.clean_read_count(""))
        self.assertEqual(fastq.DEFAULT_READS, fastq.clean_read_count(None))
        self.assertEqual(250, fastq.clean_read_count(" 250 "))
        self.assertEqual(1000, fastq.clean_read_count(1000))
        for bad in ("99", "1001", "x", "2.5"):
            with self.assertRaises(fastq.ReadCountError, msg=bad):
                fastq.clean_read_count(bad)


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

    def _sample(self, path, count):
        out_fasta = os.path.join(self.dir, "query.fasta")
        result = fastq.sample(path, count, out_fasta)
        with open(out_fasta, "rb") as handle:
            query = handle.read()
        return result, query

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

    def test_sample_takes_exactly_the_count_and_renumbers_the_fasta(self):
        (count, bases), query = self._sample(self._write("a.fastq", records(10)), 3)
        self.assertEqual((3, 30), (count, bases))
        self.assertEqual(b">read_1\nACGTACGTAC\n>read_2\nACGTACGTAC\n>read_3\nACGTACGTAC\n",
                         query)

    def test_sample_takes_fewer_when_the_file_is_short(self):
        (count, _bases), _q = self._sample(self._write("a.fastq", records(2)), 200)
        self.assertEqual(2, count)

    def test_a_record_cut_mid_line_is_dropped(self):
        data = records(2) + b"@r3\nACGTAC"
        (count, _b), query = self._sample(self._write("a.fastq", data), 200)
        self.assertEqual(2, count)
        self.assertEqual(b">read_1\nACGTACGTAC\n>read_2\nACGTACGTAC\n", query)

    def test_a_gzip_member_cut_short_ends_the_sample_rather_than_raising(self):
        """The page uploads the first 16 MB of a gzip, so the last member has no footer."""
        whole = gzip.compress(records(400))
        path = self._write("a.fastq.gz", whole[: len(whole) // 2])
        self.assertIsNone(fastq.sniff(path))
        (count, _b), query = self._sample(path, 1000)
        self.assertGreater(count, 0)
        self.assertLess(count, 400)
        self.assertEqual(count, query.count(b">read_"))

    def test_windows_line_endings_are_stripped(self):
        data = b"@r1\r\nACGT\r\n+\r\nIIII\r\n"
        (count, bases), query = self._sample(self._write("a.fastq", data), 5)
        self.assertEqual((1, 4), (count, bases))
        self.assertEqual(b">read_1\nACGT\n", query)
