# -----------------------------------------------------------------------------
# workload_identity.tf
# -----------------------------------------------------------------------------
# Workload Identity links a Kubernetes ServiceAccount (KSA) to a GCP Service
# Account (GSA), so pods can call GCP APIs (Cloud Monitoring, etc.) securely
# WITHOUT a downloaded JSON key file.
#
# There are three pieces wired together here:
#   1. The GCP Service Account (GSA) — the identity with real GCP permissions
#   2. An IAM binding that says "the Kubernetes ServiceAccount is ALLOWED
#      to impersonate this GSA" (trust direction: GCP -> allows KSA)
#   3. The Kubernetes ServiceAccount itself, annotated to point at the GSA
#      (trust direction: KSA -> points to GSA)
# Both directions must be set up correctly, or authentication will fail.
# -----------------------------------------------------------------------------

# --- Step 1: Create the GCP Service Account ---------------------------------
resource "google_service_account" "agent_gsa" {
  project      = var.project_id
  account_id   = var.service_account_name          # becomes part of the email: infra-copilot-agent-sa@<project>.iam.gserviceaccount.com
  display_name = "google Service Account"
}

# --- Step 2: Grant this GSA the actual GCP permissions it needs -------------
# monitoring.viewer lets it READ metrics (CPU/memory usage) but not modify
# anything — this is the "least privilege" principle in practice: the agent
# only gets read access, since it's meant to observe, not change infra.
resource "google_project_iam_member" "monitoring_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.agent_gsa.email}"
}

# --- Step 3: Allow the Kubernetes ServiceAccount to impersonate this GSA ----
# This is the core Workload Identity trust relationship. It says: "a
# Kubernetes ServiceAccount named <k8s_service_account_name>, in namespace
# <k8s_namespace>, in this specific GKE cluster's identity pool, is allowed
# to act as this GCP service account."
#
# The "member" string format is fixed/required by Google:
#   serviceAccount:<PROJECT_ID>.svc.id.goog[<NAMESPACE>/<KSA_NAME>]
resource "google_service_account_iam_member" "workload_identity_binding" {
  service_account_id = google_service_account.agent_gsa.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "serviceAccount:${var.project_id}.svc.id.goog[${var.k8s_namespace}/${var.service_account_name}]"
}

# --- Step 4: Create the Kubernetes ServiceAccount, annotated to point back --
# to the GSA. This requires the `kubernetes` Terraform provider to be
# configured (pointing at your GKE cluster) — see note below.
#
# The annotation "iam.gke.io/gcp-service-account" is what GKE's metadata
# server reads to know which GSA a pod using this KSA should impersonate.
resource "kubernetes_service_account" "agent_ksa" {
  metadata {
    name      = var.service_account_name
    namespace = var.k8s_namespace

    annotations = {
      "iam.gke.io/gcp-service-account" = google_service_account.agent_gsa.email
    }
  }

  # Ensures Terraform creates the GSA and its IAM binding BEFORE creating
  # this KSA, since the annotation references the GSA's email.
  depends_on = [
    google_service_account.agent_gsa,
    google_service_account_iam_member.workload_identity_binding,
    google_container_cluster.autopilot_cluster
  ]
}
