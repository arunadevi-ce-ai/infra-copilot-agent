# Create a regional GKE Autopilot cluster
resource "google_container_cluster" "autopilot_cluster" {
  name     = "my-gke-autopilot-cluster"
  location = var.gcp_region # Regional is highly recommended for production

  deletion_protection = false
  # Enable GKE Autopilot mode
  enable_autopilot = true

  # Set a release channel for auto-upgrades (RAPID, REGULAR, or STABLE)
  release_channel {
    channel = "REGULAR"
  }

  # Define networking constraints (VPC-native is required for Autopilot)
  network    = "default"
  subnetwork = "default"

  ip_allocation_policy {
    # Leave empty to let GKE automatically allocate subnetwork ranges for pods/services,
    # or specify custom secondary range names if using a custom VPC.
  }
  # This block is required to create the Identity Pool
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
  # Optional but recommended: Avoid accidental deletion of the cluster
  
}