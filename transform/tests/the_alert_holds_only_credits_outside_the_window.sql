-- An alert table whose rows are inside the threshold is worse than no alert,
-- because every row in it then has to be re-checked by hand. The window is a
-- project variable, so this reads it rather than naming a number.
select
    credit_id,
    observable_lag_days,
    reopen_window_days,
    days_past_the_window
from {{ ref('alert_late_credit_arrival') }}
where observable_lag_days <= {{ recon_reopen_days() }}
   or days_past_the_window <= 0
