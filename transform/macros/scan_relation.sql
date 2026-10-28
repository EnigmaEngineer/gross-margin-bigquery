{#
  The scan probe runs each query in analyses/ twice, once against each half of
  the pair. Rather than keep two copies of every query, the relation is a
  variable and the probe sets it.

  Defaults to the unclustered half so that a bare `dbt compile` produces valid
  SQL instead of failing on a missing var.
#}
{% macro scan_relation() %}
  {{ ref(var('scan_model', 'scan_margin_lines_unclustered')) }}
{% endmacro %}
