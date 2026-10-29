{#
  The quarter a volume rebate was earned in. A rebate cannot be raised until the
  quarter closes, so the quarter containing the invoice is the earning period and
  the credit's own posting period is somewhere after it.

  DuckDB and BigQuery both have date_trunc with a quarter part and they disagree
  about the argument order, which is the whole reason this is a macro.
#}
{% macro quarter_start(column) %}
  {%- if target.type == 'bigquery' -%}
    date_trunc({{ column }}, quarter)
  {%- else -%}
    cast(date_trunc('quarter', {{ column }}) as date)
  {%- endif -%}
{% endmacro %}


{#
  Three type names the allocation model needs and the two warehouses spell
  differently. money.sql already does this for the decimal cast. These are here
  rather than there because they are only used by the allocation chain.
#}
{% macro int_type() %}{{ 'int64' if target.type == 'bigquery' else 'bigint' }}{% endmacro %}

{% macro exact_type() %}{{ 'numeric' if target.type == 'bigquery' else 'decimal(38, 9)' }}{% endmacro %}

{% macro text_type() %}{{ 'string' if target.type == 'bigquery' else 'varchar' }}{% endmacro %}


{#
  Cents as an exact integer. Allocation arithmetic has to land on whole cents and
  sum back to the credit exactly, so every step after this one is integer work.
#}
{% macro to_cents(column) %}
  cast(round({{ column }} * 100) as {{ int_type() }})
{% endmacro %}


{#
  Cents back out to dollars, and it is a division everywhere except here.

  DuckDB's `/` returns DOUBLE whatever its operands are, so
  `cast(cents as decimal(38, 9)) / 100` is a float and every sum over it is a
  float sum. That is how the allocated margin column shipped as a DOUBLE on the
  day it was written, in a repository whose first rule is that money is exact.
  It was found by running the build twice and diffing the measurement file, and
  the count of vendor periods whose two margins differ came back 309 and 310 and
  311 on three runs of the same code over the same rows.

  Multiplying by a decimal literal keeps the type on DuckDB. BigQuery has no such
  problem with NUMERIC division and no decimal literal of that shape, so the two
  sides are spelled differently on purpose.
#}
{% macro cents_to_usd(column) %}
  {%- if target.type == 'bigquery' -%}
    cast({{ column }} as numeric) / 100
  {%- else -%}
    cast({{ column }} as bigint) * 0.01
  {%- endif -%}
{% endmacro %}


{#
  The incremental predicate, and the thing worth arguing about in this project.

  A credit arrives late. The invoice it adjusts can be eighteen months old. So a
  lookback written against the invoice date reprocesses rows whose answers did not
  change and misses the rows whose answers did, which is the wrong half of the
  table in both directions.

  The window is on posted_date, the date the credit arrived, and the model
  replaces whole credits rather than whole partitions. A credit that lands today
  rewrites its allocations however old the lines are.

  What this window has to be large enough for is a credit arriving out of order,
  posted before the newest row already in the table. That is an arrival order
  depth and it is NOT the invoice to posting lag, which is the quantity 180 was
  originally justified against. The two are different measurements and the
  confusion is worth naming, because the number happened to be generous enough
  either way.

  Nothing in this corpus can size this window. Arrival order is posting order
  here, so the out of order depth is zero by construction, and scripts/lookback_probe.py
  publishes that zero with the reason attached rather than as a result. So 180
  stays, declared as a margin against a feed that does not exist yet rather than
  as anything measured.

  The window that IS measured is the reconciliation's, in
  transform/macros/reconciliation.sql. It is set from a centile rather than from
  a maximum, and scripts/window_spread.py is the reason why.
#}
{% macro allocation_lookback_start() %}
  {%- set days = var('allocation_lookback_days') | int -%}
  (
    select
      {%- if target.type == 'bigquery' %}
      date_sub(max(posted_date), interval {{ days }} day)
      {%- else %}
      max(posted_date) - {{ days }}
      {%- endif %}
    from {{ this }}
  )
{% endmacro %}
