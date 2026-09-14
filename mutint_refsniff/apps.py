from django.apps import AppConfig


class RefsniffConfig(AppConfig):
    name = 'mutint_refsniff'

    def ready(self):
        from django.urls import include, re_path
        from mutint_common.about_registry import register_about_section
        from mutint_common.import_tab_registry import register_import_tab
        from mutint_common.plugin_registry import register_plugin_urlpatterns
        from mutint_refsniff.version import __version__

        register_plugin_urlpatterns([
            re_path(r'^refsniff/', include('mutint_refsniff.urls')),
        ])
        # The whole UI: one more way in on the Import data page, offered only while the
        # experiment has no reference -- a page that exists to *find* one has nothing to say
        # once there is one. `only_without_reference` is the flag this plugin asked core for.
        register_import_tab('refsniff', 'Identify Reference from Reads', url_name='refsniff',
                            only_without_reference=True)
        register_about_section(self, name='mutint-refsniff', version=__version__,
                               template='about/sections/mutint_refsniff.html')

        # Nothing else is registered, and each absence is a decision:
        #
        # **No import handler.** A FASTQ is not a mutation file; it is the input to a job,
        # and the launch needs a read count and a consent box that no handler can carry. The
        # drop goes through `mutint_import.staging`, the way mutint-breseq's does.
        #
        # **No nav entry.** The tab *is* the entry point, and it is only meaningful on an
        # experiment that has no reference yet, which a sidebar entry cannot express.
        #
        # **No rebuilder, no storage kind, no export type, no tool.** It derives nothing from
        # the mutations, keeps only a few hundred reads per run and deletes even those when
        # the run ends, adds no mutation type, and its one dependency is NCBI's service.
        #
        # **No importer of its own.** "Use as reference" drives core's accession import
        # from the page, so the genome arrives the way a typed accession does.
