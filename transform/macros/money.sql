{#
  Money arrives as text with two decimal places and has to land in an exact type.
  A float would be wrong by the time anything sums a year of line items.

  The two warehouses spell the same intent differently. BigQuery NUMERIC is a
  decimal with 38 digits and 9 of them after the point. DuckDB has no NUMERIC
  alias that means that, so it gets the explicit form. Writing the cast once here
  is the whole reason the models compile for both.
#}
{% macro money(column) %}
  {%- if target.type == 'bigquery' -%}
    cast({{ column }} as numeric)
  {%- else -%}
    cast({{ column }} as decimal(38, 9))
  {%- endif -%}
{% endmacro %}


{#
  The date columns are text in the corpus too. DuckDB will cast a yyyy-mm-dd
  string to a date implicitly and BigQuery will not, so this is not cosmetic.
#}
{% macro as_date(column) %}
  {%- if target.type == 'bigquery' -%}
    parse_date('%Y-%m-%d', {{ column }})
  {%- else -%}
    cast({{ column }} as date)
  {%- endif -%}
{% endmacro %}
