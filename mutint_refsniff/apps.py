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
        # and what the page has to say about it -- that a sketch of it leaves the deployment
        # -- no handler can carry. The drop goes through `mutint_import.staging`, the way
        # mutint-breseq's does.
        #
        # **No nav entry.** The tab *is* the entry point, and it is only meaningful on an
        # experiment that has no reference yet, which a sidebar entry cannot express.
        #
        # **No rebuilder, no storage kind, no export type.** It derives nothing from the
        # mutations, keeps a 16 MB head per run and deletes it the moment the run ends
        # whatever the ending, and adds no mutation type. `storage_registry` is for what a
        # component *keeps*.
        #
        # **One tool**, and it is in `tools.txt` rather than registered: installation
        # happens before Django exists, so there is no registry for it to be in.
        #
        # **No importer of its own.** "Use as reference" drives core's accession import
        # from the page, so the genome arrives the way a typed accession does.
