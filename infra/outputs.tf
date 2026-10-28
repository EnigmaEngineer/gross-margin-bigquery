# These are the values `cloud-ids` style notes end up needing, which is why they
# are outputs rather than things a reader has to look up in a console after an
# apply.

output "landing_bucket" {
  value = "gs://${google_storage_bucket.landing.name}"
}

output "dataset_ids" {
  description = "Dataset id per layer, which is what a dbt profile names."
  value       = { for layer, ds in google_bigquery_dataset.layer : layer => ds.dataset_id }
}

output "ci_service_account_email" {
  value = google_service_account.ci.email
}

output "workload_identity_provider" {
  description = "The resource name a workflow names to exchange its token."
  value       = google_iam_workload_identity_pool_provider.github.name
}
