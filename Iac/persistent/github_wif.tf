# -----------------------------------------------------------------------------
# github_wif.tf
# -----------------------------------------------------------------------------
# Sets up keyless authentication from GitHub Actions to GCP — the same
# Workload Identity principle used for the agent's pod (workload_identity.tf),
# applied here to the CI/CD pipeline instead. No downloaded JSON service
# account key is ever stored in GitHub.
#
# Three pieces, same pattern as before:
#   1. A Workload Identity Pool + Provider that trusts GitHub's OIDC tokens,
#      restricted to ONE specific repo via attribute_condition.
#   2. A dedicated CI service account with only the permissions the
#      pipeline needs — nothing broader.
#   3. A binding allowing GitHub Actions (from that repo only) to
#      impersonate the CI service account.
# -----------------------------------------------------------------------------

# --- Step 1: Workload Identity Pool -----------------------------------------
resource "google_iam_workload_identity_pool" "github_pool" {
  project                   = var.project_id
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions Pool"
  description               = "Trusts OIDC tokens issued by GitHub Actions"
}

# --- Step 2: Workload Identity Provider (trusts GitHub's OIDC issuer) ------
resource "google_iam_workload_identity_pool_provider" "github_provider" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub Provider"

  # Maps GitHub's OIDC token claims to attributes GCP can check/use.
  # "attribute.repository" is what attribute_condition below filters on.
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # CRITICAL: restricts this provider to only accept tokens from these
  # exact repos. Without this, ANY GitHub repo's Actions workflow could
  # attempt to impersonate the CI service account. Extended from a
  # single equality check to a list membership check so FinOps
  # Copilot's repo can use the same pool/provider without loosening
  # this to accept every repo under the account.
  attribute_condition = "assertion.repository in ['${var.github_repo}', '${var.finops_github_repo}']"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# --- Step 3: Dedicated CI service account -----------------------------------
resource "google_service_account" "github_ci" {
  project      = var.project_id
  account_id   = "github-actions-ci"
  display_name = "GitHub Actions CI"
}

# Push images to Artifact Registry
resource "google_project_iam_member" "ci_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.github_ci.email}"
}

# Get cluster credentials + apply manifests (used by the deploy/sync workflows)
resource "google_project_iam_member" "ci_gke_developer" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.github_ci.email}"
}

# --- Step 4: Allow GitHub Actions (this repo only) to impersonate the CI SA -
resource "google_service_account_iam_member" "ci_wif_binding" {
  service_account_id = google_service_account.github_ci.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/${var.github_repo}"
}

# --- Step 5: Same impersonation grant, extended to FinOps Copilot's repo ---
# The attribute_condition change above only decides which repos the
# provider will accept a token from at all — this binding is the
# separate, required grant of *this specific* repo's principal onto
# the CI service account. A single google_service_account_iam_member
# only takes one member, so this is a second resource, not a change to
# the one above (which stays exactly as Infra Copilot already had it).
resource "google_service_account_iam_member" "ci_wif_binding_finops" {
  service_account_id = google_service_account.github_ci.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/${var.finops_github_repo}"
}
