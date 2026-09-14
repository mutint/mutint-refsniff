# Identifying a reference from reads

An experiment needs a reference genome before any mutations can be called against it. When
you have sequencing reads but are not sure which published genome to use, the **Identify
Reference from Reads** tab on the Import data page finds the closest one.

The tab is offered only while the experiment has no reference. Once one is established --
by this page or any other -- it leaves the strip, because there is nothing left for it to do.

## Running it

1. Open the experiment's **Import data** page and choose **Identify Reference from Reads**.
2. Drop one FASTQ file (`.fastq`, `.fq`, or either gzipped). Only the first 16 MB is
   uploaded, which is far more than the reads sampled.
3. **Reads to sample** is how many reads from the start of the file are searched, between
   100 and 1000. The default of 200 is usually enough; more reads make the search slower and
   rarely change the answer.
4. **Contact email for NCBI** is prefilled from your account. NCBI asks that every automated
   search name someone it can contact. Change it for one run here, or for good with **Change
   Email** under your name in the sidebar.
5. Press **Identify reference**. The run is queued and its progress appears under **Runs**;
   the same job is listed on your **Jobs** page, where it can be cancelled.

Only one run at a time can be in progress for an experiment. A second launch is refused
until the first finishes or is cancelled.

## What leaves your MutInt

The sampled reads themselves are sent to NCBI's BLAST service and searched against RefSeq
bacterial and archaeal genomes. The page says so above the button. Do not use this tab for
reads that may not be sent to a public service.

A search takes a few minutes: NCBI's service is shared, and MutInt asks it for the result no
more than once a minute, as NCBI's rules require. NCBI also limits a site to about a hundred
automated searches a day. Each search carries the contact email from the form; a deployment
may set `MUTINT_NCBI_EMAIL` as the address offered to an account that has none.

## Reading the result

Each finished run shows a table of the genomes the reads matched: organism, accession, how
many of the sampled reads had that genome as their best hit, and the mean identity of those
hits. The top row is the genome most reads chose. When the top rows are close -- two strains
of one species splitting the reads -- the identity column and the counts say how divided the
evidence is; a few hundred reads cannot separate strains that differ by a handful of bases,
and any of the close candidates is a workable reference.

A **log** link on each run opens what the search reported, while it is still in progress,
and the NCBI search id links to NCBI's own view of the result for a day or so.

## Using the answer

**Use as reference** on the top hit downloads that accession from NCBI and establishes it as
the experiment's reference genome, exactly as typing the accession into the Reference
Sequence tab's box would. You land on the experiment's Reference page, and this tab is no
longer offered.

If the top hit is a plasmid, or the reads matched several chromosomes of the same species,
choose the accession yourself on the Reference Sequence tab instead: the button imports one
record, and a reference that needs a chromosome and its plasmids is several.
