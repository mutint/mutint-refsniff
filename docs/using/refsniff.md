# Identifying a reference from reads

An experiment needs a reference genome before any mutations can be called against it. When
you have sequencing reads but are not sure which published genome to use, the **Identify
Reference from Reads** tab on the Import data page finds the closest one.

The tab is offered only while the experiment has no reference. Once one is established --
by this page or any other -- it leaves the strip, because there is nothing left for it to do.

## Running it

1. Open the experiment's **Import data** page and choose **Identify Reference from Reads**.
2. Either drop one FASTQ file (`.fastq`, `.fq`, or either gzipped) -- only the first 16 MB is
   uploaded -- **or** type an SRA accession: a run (`SRR…`), experiment (`SRX…`), sample
   (`SRS…`, `SAMN…`) or study (`SRP…`, `PRJNA…`). An accession that names more than one run
   takes its first, and a paired run takes read 1; the first 16 MB of that file is fetched
   from ENA when the run starts.
3. Press **Identify reference**. The run is queued and its progress appears under **Runs**;
   the same job is listed on your **Jobs** page, where it can be cancelled.

There is nothing else to fill in. The whole head is used -- about 136 000 reads for a typical
150 bp run -- and the search takes a few seconds once a worker picks it up.

Only one run at a time can be in progress for an experiment. A second launch is refused
until the first finishes or is cancelled.

## What leaves your MutInt

**A sketch of the reads, not the reads.** MutInt hashes every k-mer of the head, keeps a few
thousand of those hashes, and sends *those* -- a few kilobytes of numbers -- to BBTools'
RefSeq sketch server at JGI, which answers with the genomes they match. No read and no
assembled sequence is sent, and nothing names you or your deployment.

Two other services are asked about things that are already public: reads named by an SRA
accession are fetched from ENA, where that run is published; and each genome in the answer is
looked up at NCBI Datasets, by its taxonomy id, to find the assembly it belongs to.

## Reading the result

Each finished run shows a table of the genomes the sketch matched, best first:

- **Organism** -- the strain, not just the species. The search asks for one row per strain,
  which is the resolution the answer is for: *E. coli* K-12 and *E. coli* B REL606 are about
  1.5% apart, and a reference that far off is one breseq will refuse to map across.
- **ANI** -- average nucleotide identity between your reads and that genome. A good match for
  a reference is 99.5% or better; the difference between 99.8% and 99.7% is the difference
  between two near-identical strains and is worth reading beside the organism names.
- **Complete** -- how much of that genome your reads covered. A low figure beside a high ANI
  usually means a draft assembly, or a genome larger than the one you sequenced.
- **Contam.** -- how much of your sketch that genome does *not* explain. It rises down the
  table as the candidates get further away; a high figure on the top row is worth a look, as
  it can mean a mixed culture.
- **Genome** -- the assembly the strain belongs to, linked to NCBI. Where NCBI lists no
  assembly, the single sequence the sketch matched is shown instead.

A **log** link on each run opens what the run reported, while it is still running. It usually
contains a `java.io.EOFException: Unexpected end of ZLIB input stream`, with a line above it
saying why: only the first 16 MB of the file is read, so its last gzip block is cut short. The
search carries on with every read before the cut, and the run is not affected.

## Using the answer

**Use as reference** on a row takes you to the **Reference Sequence** tab with that accession
already typed into its NCBI box. Check it, and press **Import** there.

It is two steps rather than one on purpose. That tab is where the reference annotators are
offered -- **ISEScan**, if it is installed -- and those are worth ticking *now*: predicting the
genome's insertion sequences before any reads are called against it is what lets breseq call an
IS insertion as one MOB instead of two junctions, and running ISEScan afterwards cannot go back
and change calls that have already been made. A button here that imported the genome by itself
had no boxes to offer and so ran none of them.

When the row names an assembly, **every sequence the assembly is made of** is imported -- the
chromosome and its plasmids -- which is what breseq wants. Once the import finishes, this tab
is no longer offered.

A row whose match is one contig of a draft assembly, and for which NCBI lists no assembly,
offers no button: importing that one contig would establish a reference missing almost all of
the genome. Choose a different row, or type an accession yourself on the Reference Sequence
tab.
