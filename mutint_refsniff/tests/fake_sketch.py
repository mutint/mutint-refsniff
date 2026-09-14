"""A scripted sendsketch: `run_tool` that writes a canned JSON instead of shelling out.

`processes.run_tool` is patched rather than `subprocess`, which is the seam the whole suite
runs tools through: the fake reads the `out=` argument off the argv it was handed, so a test
proves the argv carries one and the task proves it reads the file the tool was told to write.
`tools.require` is patched beside it, because a machine without bbmap installed must still be
able to run this suite.

`SHORT` is three hits trimmed from a real run -- one complete genome, one draft, one whose
taxid the fake Datasets knows nothing about -- so a test can assert every branch of
`sketch.import_accession` without ten NCBI lookups. `REAL` is the whole of that run, kept as
a file, and `test_sketch.py` parses it.
"""

import contextlib
import json
import os
from unittest import mock

from mutint_jobs import processes

from mutint_refsniff import assemblies, sketch

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

#: Where the fake pretends `sendsketch.sh` is. Nothing runs it.
SENDSKETCH = "/env/tools/bin/sendsketch.sh"

REL606_TAXID = 413997
OP50_TAXID = 637912
UNKNOWN_TAXID = 1813821

#: Three hits off a real run: REL606 (complete, one assembly in RefSeq), OP50 (a draft whose
#: `seqName` is one contig), and a Shigella the fake Datasets answers nothing for.
SHORT = json.dumps({
    "Name": "SRR2584863.1",
    "DB": "RefSeq",
    "SketchLen": 22632,
    "Seqs": 135798,
    "Bases": 20369700,
    "Escherichia coli B str. REL606": {
        "taxName": "Escherichia coli B str. REL606",
        "seqName": "tid|413997|NC_012967.1 Escherichia coli B str. REL606, complete genome",
        "taxonomy": "d:Bacteria;g:Escherichia;s:Escherichia coli",
        "WKID": 94.2498, "KID": 87.7033, "ANI": 99.785, "Complt": 100.0, "Contam": 1.14,
        "Matches": 19849, "Unique": 13, "TaxID": REL606_TAXID, "gSize": 4483840, "gSeqs": 1,
    },
    "Escherichia coli OP50": {
        "taxName": "Escherichia coli OP50",
        "seqName": ("tid|637912|NZ_WIDO01000031.1 Escherichia coli OP50 "
                    "NODE_31_length_52238_cov_79.492737, whole genome shotgun sequence"),
        "taxonomy": "d:Bacteria;g:Escherichia;s:Escherichia coli",
        "WKID": 92.7462, "KID": 86.55, "ANI": 99.727, "Complt": 100.0, "Contam": 2.293,
        "Matches": 19588, "Unique": 0, "TaxID": OP50_TAXID, "gSize": 4496551, "gSeqs": 276,
    },
    "Shigella sp. PAMC 28760": {
        "taxName": "Shigella sp. PAMC 28760",
        "seqName": "tid|1813821|NZ_CP014768.1 Shigella sp. PAMC 28760 chromosome, "
                   "complete genome",
        "taxonomy": "d:Bacteria;g:Shigella;s:Shigella sp. PAMC 28760",
        "WKID": 94.0058, "KID": 85.441, "ANI": 99.776, "Complt": 100.0, "Contam": 3.402,
        "Matches": 19337, "Unique": 0, "TaxID": UNKNOWN_TAXID, "gSize": 4378333, "gSeqs": 1,
    },
})

#: What the fake Datasets answers for the two taxids it knows. Matches what NCBI really
#: answers for them: one complete assembly for REL606, two contig assemblies for OP50.
ASSEMBLIES = {
    str(REL606_TAXID): [{"accession": "GCF_000017985.1",
                         "assembly_info": {"assembly_level": "Complete Genome"},
                         "organism": {"organism_name": "Escherichia coli B str. REL606"}}],
    str(OP50_TAXID): [{"accession": "GCF_004355015.1",
                       "assembly_info": {"assembly_level": "Contig"},
                       "organism": {"organism_name": "Escherichia coli OP50"}}],
}

#: The whole of that run, as sendsketch wrote it. Ten hits, unedited.
def real():
    with open(os.path.join(DATA_DIR, "sketch_rel606.json"), encoding="utf-8") as handle:
        return handle.read()


def _out_path(argv):
    """The path the argv told sendsketch to write to."""
    for argument in argv:
        if argument.startswith("out="):
            return argument[len("out="):]
    raise AssertionError("no out= in %r" % (argv,))


def fake_run_tool(text=SHORT, returncode=0, write=True, raises=None):
    """A `run_tool` that writes `text` where the argv said, and returns `returncode`.

    Records the argv of every call in `calls`.
    """
    calls = []

    def run_tool(argv, log, env=None, timeout=None, is_cancelled=None, **kwargs):
        calls.append(argv)
        # The real thing polls this while the tool runs; a fake that never asks would let a
        # cancellation test pass for the wrong reason.
        if is_cancelled is not None and is_cancelled():
            raise processes.Cancelled("sendsketch was cancelled.")
        if raises is not None:
            raise raises
        if write:
            with open(_out_path(argv), "w", encoding="utf-8") as handle:
                handle.write(text)
        return returncode

    run_tool.calls = calls
    return run_tool


@contextlib.contextmanager
def patched(text=SHORT, returncode=0, write=True, raises=None, no_tool=False):
    """`with patched() as run_tool:` -- sendsketch found, and `run_tool` scripted.

    `no_tool=True` instead makes `tools.require` raise, which is the only thing the task
    looks at before it fetches anything.
    """
    run_tool = fake_run_tool(text, returncode, write, raises)
    with mock.patch.object(processes, "run_tool", new=run_tool), \
            mock.patch.object(sketch.tools, "tool_path",
                              side_effect=lambda name: None if no_tool else SENDSKETCH), \
            mock.patch.object(sketch.tools, "require",
                              side_effect=_require(no_tool)), \
            mock.patch.object(assemblies, "_sleep", new=lambda seconds: None):
        yield run_tool


def _require(no_tool):
    from mutint_common.tools import ToolMissing

    def require(name):
        if no_tool:
            raise ToolMissing("%s is not installed. Add it to tools.txt and run "
                              "./mutint install." % name)
        return SENDSKETCH

    return require
