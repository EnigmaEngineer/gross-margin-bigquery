# The trust that makes a key file unnecessary. A workflow run presents its own
# token, this pool validates it, and the run gets a short lived credential. No long
# lived key exists to leak.

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # Without this the pool trusts a token from any repository on GitHub that names
  # the provider. The mapping above reads like a restriction and is not one. It
  # only makes the claim available to this condition.
  attribute_condition = "assertion.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# The member is a principal set rather than a principal. It names every identity
# the pool will mint for this repository, which is one per workflow run, so there
# is no single account to grant and the grant is over the set.
resource "google_service_account_iam_member" "github_may_impersonate_ci" {
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}
