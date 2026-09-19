"""What **Use as reference** does with a hit, which is now one navigation.

There is no server code for it here and there never was. What changed is where it goes: it
used to make the two calls the Reference Sequence tab's accession box makes -- open a session
for the `reference` type carrying the accession, then finalize -- and land on the Reference
page. It now fills that box instead and lets the person press Import themselves.

**What could not survive the old shape is the reason.** A finalize from here had no annotator
panels on it, so it posted `{annotators: {}}` -- and this page is offered *only* while an
experiment has no reference, which makes it the one path that knows for certain the genome is
about to arrive for the first time, and was also the one path that silently skipped every
annotator registered against it. ISEScan is the one that matters: predicting IS elements is
worth doing before any reads are called against the genome, and not afterwards.

Asserted against the script on disk, as `PageTestCase` asserts against the rendered page: this
is client behaviour, and the file is the only place it is written down.
"""

import os
import unittest

PLUGIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(PLUGIN, "mutint_refsniff", "static", "mutint_refsniff", "page.js")


def _script():
    with open(SCRIPT) as handle:
        return handle.read()


class UseAsReferenceTestCase(unittest.TestCase):
    def setUp(self):
        self.script = _script()
        start = self.script.index('".refsniff-use"')
        self.handler = self.script[start:start + 600]

    def test_it_navigates_to_the_reference_tab_with_the_accession(self):
        self.assertIn('"/import/?experiment_id=" + EXPERIMENT_ID + "&tab=reference"',
                      self.handler)
        self.assertIn('"&accession=" +', self.handler)
        self.assertIn("encodeURIComponent", self.handler)

    def test_it_names_the_tab_explicitly_so_a_remembered_one_cannot_win(self):
        """`import_view` redirects a bare visit to whichever tab this reader used last, and
        builds that URL from the registry -- which would not carry `?accession=`. Naming the
        tab is what keeps the redirect out of the way."""
        self.assertIn("&tab=reference", self.handler)

    def test_it_no_longer_imports_anything_itself(self):
        self.assertNotIn("mutintUpload", self.handler)
        self.assertNotIn("finalize", self.handler)
        self.assertNotIn("needs_confirmation", self.script)

    def test_the_import_state_it_had_to_track_is_gone(self):
        """The flag existed only so a second click could not start a second import and so the
        five-second poll would leave the button alone mid-import. A navigation needs neither."""
        self.assertNotIn("var importing", self.script)

    def test_there_is_no_confirm_dialog_and_no_sweetalert_behind_it(self):
        """Which dialog a control gets is decided by whether the person can undo it
        themselves, and a navigation is undone with Back. The commitment moved to the Import
        button on the tab it lands on."""
        self.assertNotIn("mutintConfirm", self.script)
        page = os.path.join(PLUGIN, "mutint_refsniff", "templates", "refsniff", "page.html")
        with open(page) as handle:
            self.assertNotIn("sweetalert", handle.read())

    def test_the_dialog_s_warning_survives_as_a_standing_sentence(self):
        """It was this plugin's knowledge and nothing on core's page has it: that an assembly
        brings every sequence it is made of, and that this tab closes once there is a
        reference. A standing fact about the table rather than about one press."""
        self.assertIn("plasmids included", self.script)
        self.assertIn("Reference Sequence tab", self.script)
