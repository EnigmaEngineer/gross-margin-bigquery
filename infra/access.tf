# Row access and column security for the margin marts.
#
# The rules are not written here. They are read out of access-rules.json, which
# scripts/emit_access_rules.py generates from gross_margin/access.py. The same
# file drives the view layer that runs on DuckDB, so a region added in one place
# appears in both enforcements without either being edited. Writing the roles out
# again in HCL would give two copies of an access rule that have to agree, which
# is the thing worth avoiding even at the cost of a generated file in the tree.
#
# None of this has been applied. There is no project behind it yet, so what has
# actually been exercised is terraform validate against the resolved provider
# schema and nothing more. The behaviour the predicates below describe has never
# run on BigQuery.

locals {
  access_rules = jsondecode(file("${path.module}/access-rules.json"))

  # Keyed on policy_id so for_each gets a stable key and a reordered list in the
  # generated file does not destroy and recreate every policy.
  row_access_policies = {
    for policy in local.access_rules.row_access_policies :
    policy.policy_id => policy
  }

  roles_that_may_read_cost = [
    for role in local.access_rules.roles : role.name if role.may_read_cost
  ]
}

# One taxonomy for the project. FINE_GRAINED_ACCESS_CONTROL is what makes a tag
# deny a read rather than merely label the column, and a taxonomy created without
# it is a documentation exercise that looks like a control.
resource "google_data_catalog_taxonomy" "margin" {
  display_name = "${var.dataset_prefix}-margin-confidentiality"
  description  = "Columns that carry vendor cost, directly or by arithmetic"
  region       = var.region

  activated_policy_types = ["FINE_GRAINED_ACCESS_CONTROL"]
}

# A single tag rather than one per column. The thing being protected is the cost
# figure and every tagged column is a route to it, so they share a tag and a
# grant. Splitting the tag per table would let somebody grant a reader the vendor
# mart and think they had withheld cost.
resource "google_data_catalog_policy_tag" "cost" {
  taxonomy     = google_data_catalog_taxonomy.margin.id
  display_name = local.access_rules.restricted_quantity
  description  = "Vendor cost, and any column it can be recovered from"
}

# Fine grained reader is granted to the roles the rule list says may read cost
# and to nobody else. Without a grant the tag denies the column to every
# principal including the dataset owner, which is the correct default and is also
# how a report breaks on the first apply.
resource "google_data_catalog_policy_tag_iam_member" "cost_reader" {
  for_each = toset(local.roles_that_may_read_cost)

  policy_tag = google_data_catalog_policy_tag.cost.name
  role       = "roles/datacatalog.categoryFineGrainedReader"
  member     = "serviceAccount:${var.role_principals[each.key]}"
}

# Masking rather than refusing, for the roles that may not read cost. A refused
# column breaks a report that selects star. A masked one returns a null and the
# report keeps running with a hole in it, which is the behaviour finance asked
# for on every tool I have seen this on.
#
# This is the one place the two enforcements genuinely differ in outcome rather
# than in spelling. The DuckDB view layer drops the column, so a select of it
# raises an error. A masking policy here returns null instead. Both refuse the
# number and a caller cannot be written to expect either.
resource "google_bigquery_datapolicy_data_policy" "cost_mask" {
  location         = var.region
  data_policy_id   = "${replace(var.dataset_prefix, "-", "_")}_cost_nullify"
  policy_tag       = google_data_catalog_policy_tag.cost.name
  data_policy_type = "DATA_MASKING_POLICY"

  data_masking_policy {
    predefined_expression = "ALWAYS_NULL"
  }
}

# The row filters. Every table carrying any policy needs one per principal that
# should read it, including the ones that read everything, because BigQuery
# evaluates these as a union and a principal with no policy on a table with
# policies reads zero rows. The generated file carries the true predicates for
# exactly that reason and they are not padding.
resource "google_bigquery_row_access_policy" "region" {
  for_each = local.row_access_policies

  dataset_id = google_bigquery_dataset.layer["marts"].dataset_id
  table_id   = each.value.table_id
  policy_id  = each.value.policy_id

  filter_predicate = each.value.filter_predicate
  grantees         = ["serviceAccount:${var.role_principals[each.value.role]}"]
}

# The dataset grants, which are what the column tags actually rest on.
#
# This was missing and it mattered. A policy tag on a marts column is worth
# nothing to a principal that can read main_staging, because stg_invoice_lines
# carries extended_cost_usd and unit_cost_usd with no tag and no arithmetic
# needed. Measured on the DuckDB build, where both columns sit two layers below
# the mart the tag protects.
#
# So every role reads the marts dataset and no role is granted raw or staging.
# dataViewer rather than dataEditor, because an analyst that can replace a mart
# can replace the one with the policy on it.
resource "google_bigquery_dataset_iam_member" "role_reads_marts" {
  for_each = { for role in local.access_rules.roles : role.name => role }

  dataset_id = google_bigquery_dataset.layer["marts"].dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${var.role_principals[each.key]}"
}

# Job user is separate from data access and both are needed to run a query. It
# is granted at the project level because a query job does not belong to a
# dataset, which is the part of BigQuery's model that surprises people.
resource "google_project_iam_member" "role_runs_jobs" {
  for_each = { for role in local.access_rules.roles : role.name => role }

  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${var.role_principals[each.key]}"
}
