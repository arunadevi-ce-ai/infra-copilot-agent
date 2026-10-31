# -----------------------------------------------------------------------------
# finops_copilot.tf used for Finops project 
# -----------------------------------------------------------------------------
# FinOps Copilot Service account - additions on top of the existing agent_gsa and finops_gcp_k8s_sa (k8s_finops_namespace specific)
# 

#
# Also creates the BigQuery dataset/tables backing FinOps Copilot's
# optional BigQuery backend (detector.py reads billing_export,
# utilization_metrics, and resource_metadata from here when
# BIGQUERY_DATASET is set — see the finops-copilot-agent README), and
# grants agent_gsa access to all three.
# -----------------------------------------------------------------------------

# --- Create the finops-copilot namespace ------------------------------------
resource "kubernetes_namespace" "finops_copilot" {
  metadata {
    name = var.k8s_finops_namespace
  }
}

# --- Allow the finops-copilot namespace's KSA to impersonate the same
#     GSA Infra Copilot's KSA already uses -----------------------------------
resource "google_service_account_iam_member" "finops_workload_identity_binding" {
  service_account_id = google_service_account.agent_gsa.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "serviceAccount:${var.project_id}.svc.id.goog[${var.k8s_finops_namespace}/${var.k8s_finops_service_account_name}]"
}

# --- Create that namespace-local ServiceAccount object ----------------------
resource "kubernetes_service_account" "finops_gcp_k8s_sa" {
  metadata {
    name      = var.k8s_finops_service_account_name
    namespace = kubernetes_namespace.finops_copilot.metadata[0].name

    annotations = {
      "iam.gke.io/gcp-service-account" = google_service_account.agent_gsa.email
    }
  }

  depends_on = [
    google_service_account.agent_gsa,
    google_service_account_iam_member.finops_workload_identity_binding,
    kubernetes_namespace.finops_copilot
  ]
}

# --- BigQuery dataset + table for the billing backend ------------------------
resource "google_bigquery_dataset" "finops_copilot" {
  project    = var.project_id
  dataset_id = "finops_copilot"
  location   = var.gcp_region
}

resource "google_bigquery_table" "billing_export" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.finops_copilot.dataset_id
  table_id   = "billing_export"

  schema = jsonencode([
    { name = "date",            type = "DATE",    mode = "NULLABLE" },
    { name = "project_id",      type = "STRING",  mode = "NULLABLE" },
    { name = "team",            type = "STRING",  mode = "NULLABLE" },
    { name = "service",         type = "STRING",  mode = "NULLABLE" },
    { name = "sku_description", type = "STRING",  mode = "NULLABLE" },
    { name = "resource_name",   type = "STRING",  mode = "NULLABLE" },
    { name = "region",          type = "STRING",  mode = "NULLABLE" },
    { name = "cost_usd",        type = "FLOAT64", mode = "NULLABLE" },
  ])

  deletion_protection = false # mock/generated data, safe to recreate
}

resource "google_bigquery_table" "utilization_metrics" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.finops_copilot.dataset_id
  table_id   = "utilization_metrics"

  schema = jsonencode([
    { name = "date",          type = "DATE",    mode = "NULLABLE" },
    { name = "resource_name", type = "STRING",  mode = "NULLABLE" },
    { name = "avg_cpu_pct",   type = "FLOAT64", mode = "NULLABLE" },
    { name = "avg_mem_pct",   type = "FLOAT64", mode = "NULLABLE" },
  ])

  deletion_protection = false # mock/generated data, safe to recreate
}

# extra_attributes is a JSON string holding resource-type-specific
# fields (machine_type, disk_type, size_gb, autoscaling_enabled,
# storage_class, tier, purpose) that vary by resource — a flat schema
# can't give each one its own real column.
resource "google_bigquery_table" "resource_metadata" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.finops_copilot.dataset_id
  table_id   = "resource_metadata"

  schema = jsonencode([
    { name = "resource_name",           type = "STRING",  mode = "NULLABLE" },
    { name = "team",                    type = "STRING",  mode = "NULLABLE" },
    { name = "attached_instance",       type = "BOOLEAN", mode = "NULLABLE" },
    { name = "last_accessed_days_ago",  type = "INTEGER", mode = "NULLABLE" },
    { name = "extra_attributes",        type = "STRING",  mode = "NULLABLE" },
  ])

  deletion_protection = false # mock/generated data, safe to recreate
}

# --- Grant the existing agent_gsa access to query/load this table -----------
# Adds BigQuery permissions on top of its existing monitoring.viewer role.
resource "google_bigquery_dataset_iam_member" "finops_data_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.finops_copilot.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.agent_gsa.email}"
}

resource "google_project_iam_member" "finops_bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.agent_gsa.email}"
}
