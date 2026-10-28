# One service account for continuous integration. It runs the loads and the
# transformations and it is the only identity that writes.

resource "google_service_account" "ci" {
  account_id   = "margin-ci"
  display_name = "Loads billing extracts and builds the margin marts"
}

# Read the landing bucket and nothing else on it. A load job reads, it does not
# write, and it has no reason to delete an object the lifecycle rule will remove.
resource "google_storage_bucket_iam_member" "ci_reads_landing" {
  bucket = google_storage_bucket.landing.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.ci.email}"
}

# Write the tables inside each dataset. Not dataOwner, which carries the right to
# change who else can read the dataset, and not admin, which carries the right to
# delete the dataset itself. dataEditor is the grant that matches what a build does.
resource "google_bigquery_dataset_iam_member" "ci_writes_tables" {
  for_each = google_bigquery_dataset.layer

  dataset_id = each.value.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.ci.email}"
}

# Running a query is a project level permission and it is separate from the right
# to write the answer anywhere. jobUser gives the first and none of the second.
resource "google_project_iam_member" "ci_runs_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.ci.email}"
}
