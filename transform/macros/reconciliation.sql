{#
  The reconciliation window, and the one number in this project that had to be
  argued for rather than measured once.

  The allocation lookback was first set to 180 days and called a bound rather
  than a threshold, on the grounds that it was wider than the largest credit
  arrival anybody had seen, which was 164 days. scripts/window_spread.py refits
  the same corpus at five seeds and that reasoning does not survive it. The
  quantiles barely move. The maximum moves 52 days, from 164 to 216, and 180
  leaves a credit outside itself on two of the five.

  A sample maximum is not a bound. It is the largest draw so far, and on a
  lognormal arrival it grows with the number of draws, so a window set at the
  maximum plus a margin is a window set at an unstable number plus a margin.

  So this window is derived from the quantile instead, which is the part that
  holds still. The worst pooled 99th centile over those five seeds is 131 days.
  150 carries 19 days above it. The window is declared with its coverage and
  NOT as a bound, and the credits outside it are what the alert is for. That is
  the honest division: the window covers the body of the distribution and the
  alert covers the tail, because nothing covers the tail by being wide enough.
#}
{% macro recon_reopen_days() %}{{ var('recon_reopen_days') | int }}{% endmacro %}


{#
  How far back the reconciliation reopens closed periods, as a date.

  Anchored on the newest ledger posting rather than on today, so a rebuild of a
  historical corpus reopens the same periods it did the first time. Anchoring on
  current_date would make every published figure a function of the day the build
  ran, which is the 10-13 reproducibility rule.
#}
{% macro recon_window_start(as_of_column) %}
  {%- if target.type == 'bigquery' -%}
    date_sub({{ as_of_column }}, interval {{ recon_reopen_days() }} day)
  {%- else -%}
    {{ as_of_column }} - {{ recon_reopen_days() }}
  {%- endif -%}
{% endmacro %}


{#
  The period a date falls in, as the text the corpus uses. The two warehouses
  spell the formatting differently and the staging layer already carries the
  generator's own `period` column, so this is only needed where a model derives
  a period from a date the generator did not label.
#}
{% macro period_of(column) %}
  {%- if target.type == 'bigquery' -%}
    format_date('%Y-%m', {{ column }})
  {%- else -%}
    strftime({{ column }}, '%Y-%m')
  {%- endif -%}
{% endmacro %}
