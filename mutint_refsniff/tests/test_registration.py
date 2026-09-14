"""What the app registers: the tab, only without a reference; the About section."""

from mutint_common import about_registry
from mutint_common.import_tab_registry import get_import_tabs

from mutint_refsniff.tests.fixture import RefsniffFixture
from mutint_refsniff.tests.test_launch import establish_reference


class RegistrationTestCase(RefsniffFixture):
    def test_the_tab_is_offered_only_without_a_reference(self):
        keys = [tab["key"] for tab in get_import_tabs(self.experiment.id)]
        self.assertIn("refsniff", keys)
        tab = [t for t in get_import_tabs(self.experiment.id) if t["key"] == "refsniff"][0]
        self.assertEqual("Identify Reference from Reads", tab["label"])
        self.assertEqual("/refsniff/?experiment_id=%d" % self.experiment.id, tab["url"])

        establish_reference(self.experiment)

        self.assertNotIn("refsniff", [tab["key"] for tab in get_import_tabs(self.experiment.id)])

    def test_the_about_section(self):
        sections = {s["name"]: s for s in about_registry.get_about_sections()}
        self.assertIn("mutint-refsniff", sections)
        self.assertEqual("0.0.1", sections["mutint-refsniff"]["version"])
        self.assertEqual("about/sections/mutint_refsniff.html",
                         sections["mutint-refsniff"]["template"])

    def test_the_about_page_renders_the_section(self):
        response = self.client.get("/about")
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "mutint-refsniff")
        self.assertContains(response, "sketch")
