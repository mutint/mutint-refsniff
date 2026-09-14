# mutint-refsniff

Identify an experiment's reference genome from a sample of its reads, from inside
[MutInt](https://github.com/mutint/mutint-core). An **Identify Reference from Reads** tab on
the Import data page, offered while an experiment has no reference: drop one FASTQ, or name a
run in the SRA, and the first 16 MB of it is sketched against RefSeq. The answer is a ranked
table of the closest genomes — organism, average nucleotide identity, completeness — and
**Use as reference** imports the best one, as an assembly, through core's own accession
import.

**It resolves strains, which is the point.** A sketch of ~136 000 reads separates
*E. coli* B REL606 from K-12 — about 1.5% divergent, which is more than breseq will map
across — where a vote over a few hundred reads cannot.

**What leaves the deployment is a sketch, not reads.** A few kilobytes of k-mer hashes go to
BBTools' RefSeq sketch server at JGI, which answers with the genomes those hashes match. No
read and no assembled sequence is sent, and no contact address is asked for. The page says so
above its button.

**It needs a worker** — the fetch and the sketch outlive a request — so it is enqueued on
`django.tasks`; `./mutint start` runs one for you. One run per experiment at a time.

**One tool**: `bbmap`, from bioconda, installed into `env/tools` like every other. It depends
on a JDK, which is most of what it costs to install.

## Installing

```bash
git submodule add ../mutint-refsniff mutint-refsniff
```

MIT licensed. See [mutint-core](https://github.com/mutint/mutint-core) for the platform.
