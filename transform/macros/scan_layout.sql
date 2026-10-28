{#
  The clustered half of the scan pair carries a BigQuery layout and the
  unclustered half carries none. Both halves have to keep running on DuckDB,
  which has no partitioning and no clustering, so the layout is chosen by
  adapter rather than written into the model.

  The keys are not arbitrary. The three queries in analyses/ all restrict on
  invoice_date and then group by vendor or partner, so the partition column is
  the one every query filters and the cluster prefix is the one the most
  selective query groups by. BigQuery clustering is prefix ordered, so
  vendor_id first costs the partner query nothing and buys the vendor query the
  block pruning.
#}
{% macro scan_layout(clustered) %}
  {%- if target.type == 'bigquery' and clustered -%}
    {{ return({
      'partition_by': {'field': 'invoice_date', 'data_type': 'date', 'granularity': 'month'},
      'cluster_by': ['vendor_id', 'partner_id', 'product_line']
    }) }}
  {%- else -%}
    {{ return({'partition_by': none, 'cluster_by': none}) }}
  {%- endif -%}
{% endmacro %}
