# Three datasets rather than one, because the access rules differ and a dataset is
# the unit BigQuery grants on. Raw holds what arrived. Staging holds the cast and
# deduplicated version. Marts hold the margin tables finance reads.
#
# Every dataset pins its location to the same variable the bucket uses. A load job
# cannot read a bucket in a different location from its dataset, and the error
# arrives at load time rather than at plan time.

locals {
  datasets = {
    raw = {
      description = "Billing, partner and plan extracts exactly as they landed"
      # Raw is replayable from the bucket for thirty days and from the source system
      # after that, so a table here is a cache. Expiring it keeps storage from
      # growing without anybody deciding to let it.
      table_expiration_ms = 7776000000
      # Forty eight hours is the floor BigQuery allows. Time travel storage is
      # billed, and on a dataset that can be rebuilt by rerunning a load it buys
      # very little.
      time_travel_hours = 48
    }
    staging = {
      description         = "Cast and deduplicated, rebuilt from raw on every run"
      table_expiration_ms = 7776000000
      time_travel_hours   = 48
    }
    marts = {
      description = "Gross margin by vendor, partner and product line"
      # No expiration. A mart is the thing somebody reads in a report and a table
      # that disappears on a timer is the worst possible way to find that out.
      table_expiration_ms = null
      # The default of a week. A mart is the one place here where recovering a
      # table somebody dropped is worth paying storage for.
      time_travel_hours = null
    }
  }
}

resource "google_bigquery_dataset" "layer" {
  for_each = local.datasets

  dataset_id  = "${replace(var.dataset_prefix, "-", "_")}_${each.key}"
  description = each.value.description
  location    = var.location

  default_table_expiration_ms = each.value.table_expiration_ms
  max_time_travel_hours       = each.value.time_travel_hours

  # Stated rather than left to the provider default. A destroy that silently drops
  # the tables under a dataset is the single most expensive mistake available here,
  # and the only way a reader can tell it is off is if the file says so.
  delete_contents_on_destroy = false

  labels = {
    layer   = each.key
    managed = "terraform"
  }
}
