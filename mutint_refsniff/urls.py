from django.urls import re_path

import mutint_refsniff.views


urlpatterns = [
    re_path(r'^$', mutint_refsniff.views.refsniff, name='refsniff'),
    re_path(r'^runs$', mutint_refsniff.views.runs, name='refsniff_runs'),
    re_path(r'^launch$', mutint_refsniff.views.launch, name='refsniff_launch'),
    re_path(r'^run/(?P<pk>\d+)/delete$', mutint_refsniff.views.run_delete,
            name='refsniff_run_delete'),
]
