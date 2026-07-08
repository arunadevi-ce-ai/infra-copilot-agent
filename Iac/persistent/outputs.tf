# These outputs give you the exact values to paste into GitHub's repo
# Variables (Settings -> Secrets and variables -> Actions -> Variables) —
# run `terraform output` after apply to get them.
output "github_wif_provider" {
  description = "Value for the GCP_WORKLOAD_IDENTITY_PROVIDER GitHub repo variable"
  value       = google_iam_workload_identity_pool_provider.github_provider.name
}

output "github_ci_service_account" {
  description = "Value for the GCP_CI_SERVICE_ACCOUNT GitHub repo variable"
  value       = google_service_account.github_ci.email
}
output "project_id" {
  value = var.project_id
}