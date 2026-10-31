# The scheduler the DAG in orchestration/dags runs on, and the identity it runs
# as. Declared and validated and never applied, for the same reason as the
# rest of this directory. There is no project.
#
# Composer is the expensive resource in this repository by a wide margin. A
# small environment runs its scheduler and its web server and its workers
# continuously. Unlike BigQuery it bills whether or not anybody asks it a
# question. That is the argument against reaching for it and it is written here
# rather than in the README, because this is the file that would create it.
#
# What a run costs is NOT measured anywhere in this repository and cannot be.
# Measuring it needs an environment that exists for long enough to be billed.
# A figure read off a published price list and multiplied out is a different
# thing from a measurement, and anywhere this repository gives a cost it has to
# say which of the two it is.

# Composer runs tasks as this account rather than as the default Compute
# Engine one. The default account holds editor on the project, so a DAG with a
# bug on it can drop a dataset, and the whole access story in access.tf is
# worth nothing against a scheduler that can rewrite the tables it reads.
resource "google_service_account" "composer" {
  account_id   = "gmb-composer"
  display_name = "Cloud Composer environment and task runner"
}

# What the scheduler may do, which is read the landing bucket, run queries and
# write the datasets the models materialise into. It is NOT a reporting role and
# it is not in the access rule list, because the rule list describes who may
# read the restricted quantity and this identity builds the tables that carry
# it. Those are different questions and conflating them is how a service
# account ends up on a row access policy.
locals {
  composer_project_roles = [
    "roles/composer.worker",
    "roles/bigquery.jobUser",
  ]
}

resource "google_project_iam_member" "composer" {
  for_each = toset(local.composer_project_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.composer.email}"
}

# Dataset level rather than project level. dataEditor on the project would let
# the scheduler write raw and staging and marts and everything else in the
# project, and the three datasets this pipeline owns are the three it needs.
resource "google_bigquery_dataset_iam_member" "composer_writes_its_own_layers" {
  for_each = google_bigquery_dataset.layer

  dataset_id = each.value.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.composer.email}"
}

resource "google_storage_bucket_iam_member" "composer_reads_landing" {
  bucket = google_storage_bucket.landing.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.composer.email}"
}

resource "google_composer_environment" "orchestration" {
  name   = "gmb-orchestration"
  region = var.region

  config {
    # No default, like the other three identifiers. An image version names a
    # Composer release and an Airflow release together, and the set that
    # actually exists is a function of the day the environment is created, so a
    # value typed here would be a guess that terraform validate cannot catch.
    # `gcloud composer images list` answers it in the project.
    software_config {
      image_version = var.composer_image_version

      # The DAG is wiring over the scripts and the scripts need the project,
      # so the project is installed on the image rather than vendored into the
      # dags bucket. The pins come out of requirements.txt through
      # scripts/emit_pipeline_pins.py rather than being retyped here, so a bump
      # in one place cannot leave the scheduler on the old version.
      pypi_packages = local.pipeline_pins

      env_variables = {
        GMB_GCP_PROJECT = var.project_id
        GMB_BQ_DATASET  = replace(var.dataset_prefix, "-", "_")
      }

      # The scheduler parses every file in the dags folder on a loop, so a
      # parse that reaches the warehouse turns a listing into a billed query.
      # Nothing in orchestration/dags does, and this is the switch that makes a
      # mistake there loud rather than expensive.
      airflow_config_overrides = {
        "core-dags_are_paused_at_creation" = "True"
      }
    }

    node_config {
      service_account = google_service_account.composer.email
    }

    # The smallest shape that runs. Composer 2 bills the scheduler, the web
    # server and the workers separately and the worker count is the only one
    # that should move with the pipeline, so the minimum worker count is 1 and
    # the maximum is 2 for the one day a backfill needs it.
    workloads_config {
      scheduler {
        cpu        = 0.5
        memory_gb  = 2
        storage_gb = 1
        count      = 1
      }
      web_server {
        cpu        = 0.5
        memory_gb  = 2
        storage_gb = 1
      }
      worker {
        cpu        = 0.5
        memory_gb  = 2
        storage_gb = 1
        min_count  = 1
        max_count  = 2
      }
    }

    environment_size = "ENVIRONMENT_SIZE_SMALL"
  }

  # An environment deleted by accident takes its task history with it, and the
  # history is the only record of which runs produced which published figures.
  lifecycle {
    prevent_destroy = true
  }
}

# Generated from requirements.txt by scripts/emit_pipeline_pins.py. Read rather
# than parsed here, because the first version of this file did the parsing in
# HCL and a for expression over split and trimspace is not what a reviewer
# should have to read to answer which dbt the scheduler runs.
locals {
  pipeline_pins = jsondecode(file("${path.module}/pipeline-pins.json")).pypi_packages
}
