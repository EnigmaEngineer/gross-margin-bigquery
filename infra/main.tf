terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source = "hashicorp/google"
      # Pinned to a minor rather than floated. A provider upgrade that changes a
      # default on a dataset is a data decision and it should arrive in a diff
      # somebody reads.
      version = "~> 6.12"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# The landing zone. Billing extracts arrive here and a load job reads them into
# the raw dataset. Nothing downstream reads the bucket directly.
resource "google_storage_bucket" "landing" {
  name     = var.bucket_name
  location = var.location

  uniform_bucket_level_access = true

  versioning {
    enabled = false
  }

  # Landing files are a copy of something the source system still holds, so
  # keeping them forever is paying twice for the same bytes. Thirty days is long
  # enough to replay a month end that went wrong.
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  # A destroy that empties a bucket mid backfill is a bad afternoon. The lifecycle
  # rule above is the intended way for an object to leave.
  force_destroy = false
}
