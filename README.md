# mutint-refsniff

Identify an experiment's reference genome from a sample of its reads, from inside
[MutInt](https://github.com/mutint/mutint-core). An **Identify Reference from Reads** tab on
the Import data page, offered while an experiment has no reference: drop one FASTQ, and the
first few hundred reads are sent to NCBI BLAST and searched against RefSeq bacterial and
archaeal genomes. The answer is a ranked table of the closest genomes — accession, how many
reads chose each, their identity — and **Use as reference** imports the top one through
core's own accession import.

**It needs a worker** — a search is minutes — so it is enqueued on `django.tasks`;
`./mutint start` runs one for you. One run per experiment at a time, and NCBI allows a site
about a hundred searches a day.

**Nothing is installed for it.** The client is the standard library, talking to NCBI's BLAST
URL API within the limits NCBI publishes. Each search names the person who launched it, from
the email on their account (core's Change Email page is where that is set), with
`MUTINT_NCBI_EMAIL` as the deployment's fallback.

**The reads leave the deployment**, and the page says so above its button. That is the one
place core's rule that sequence never leaves is set aside, by a person, for a few hundred
reads.

## Installing

```bash
git submodule add ../mutint-refsniff mutint-refsniff
```

MIT licensed. See [mutint-core](https://github.com/mutint/mutint-core) for the platform.
