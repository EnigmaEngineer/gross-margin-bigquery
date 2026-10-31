# No defaults on the three that identify a real project. A default here would let
# `terraform plan` run against something plausible and wrong, and the failure mode
# is a dataset created in whatever project the ambient credentials point at.
#
# TODO: fill these in once an OIDC trust exists between this repository and a
# billing enabled GCP project. Creating that trust is a one time step by somebody
# holding owner on the project and it is deliberately not something an automated
# build does. Until it exists `terraform plan` cannot run, and nothing in this
# directory has been applied.

variable "project_id" {
  description = "The GCP project that holds the datasets, the bucket and the CI identity."
  type        = string
}

variable "bucket_name" {
  # Buckets share one global namespace, so this cannot have a useful default.
  description = "Globally unique name for the billing extract landing bucket."
  type        = string
}

variable "billing_account" {
  description = "Billing account id the budget attaches to."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the repository allowed to assume the CI identity."
  type        = string
  default     = "EnigmaEngineer/gross-margin-bigquery"
}

variable "location" {
  # One value for the bucket and for every dataset. A load job cannot read a bucket
  # in a different location from the dataset it is loading into.
  description = "Location for the bucket and for every dataset."
  type        = string
  default     = "US"
}

variable "region" {
  description = "Provider region for anything that needs one rather than a location."
  type        = string
  default     = "us-central1"
}

variable "dataset_prefix" {
  # Dashes are not legal in a BigQuery dataset id, so the prefix is rewritten
  # before use. The default carries the dash so that the rewrite is exercised by
  # the default path rather than only by somebody who happens to pass one.
  description = "Prefix for the three dataset ids."
  type        = string
  default     = "gross-margin"
}

variable "budget_amount_usd" {
  # Not a forecast. It is the line past which something has gone wrong, which here
  # most likely means a scheduled query scanning a whole table every hour.
  description = "Monthly budget in USD. Alerts fire at the thresholds in budget.tf."
  type        = number
  default     = 25
}

variable "role_principals" {
  # The service account email behind each role in access-rules.json. No default,
  # for the same reason project_id has none. A plausible placeholder here would
  # grant a row access policy to an account that does not exist, and BigQuery
  # accepts that without complaint, so the policy would look applied and refuse
  # everybody.
  #
  # Keyed by role name. A role in the rule list with no entry here fails at plan
  # time on the map lookup, which is the behaviour worth having.
  description = "Role name to service account email, for every role in access-rules.json."
  type        = map(string)
}

variable "composer_image_version" {
  # No default, for the same reason as project_id. An image version names a
  # Composer release and an Airflow release together and the available set
  # changes, so a value typed here would be a guess. `gcloud composer images
  # list --location <region>` answers it against the project.
  description = "Composer image version, which pins the Airflow release with it."
  type        = string
}
