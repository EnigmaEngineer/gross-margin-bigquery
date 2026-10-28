# The budget goes in on the first day rather than after a surprise. On BigQuery the
# surprise is not storage. It is a query scanning a table nobody clustered, run on a
# schedule, and the bill for that arrives before anybody notices the pattern.

resource "google_billing_budget" "margin_platform" {
  billing_account = var.billing_account
  display_name    = "gross-margin-bigquery"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_amount_usd)
    }
  }

  # Half, all, and then double. The third one is the threshold that matters. A bill
  # at twice the budget means something is scanning far more than it should, and
  # that is a different problem from a slow climb in storage.
  dynamic "threshold_rules" {
    for_each = [0.5, 1.0, 2.0]
    content {
      threshold_percent = threshold_rules.value
    }
  }
}
