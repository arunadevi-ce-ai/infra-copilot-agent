terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0" 
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.gcp_region
  zone    = var.gcp_zone
}

# Points the kubernetes provider at your GKE cluster's credentials.
# These data sources read the cluster info Terraform already knows about
# once your GKE cluster resource is created (assumes your cluster resource
# is named google_container_cluster.primary — adjust if named differently).
provider "kubernetes" {
  host                   = "https://${google_container_cluster.autopilot_cluster.endpoint}"
  cluster_ca_certificate = base64decode(google_container_cluster.autopilot_cluster.master_auth[0].cluster_ca_certificate)
  token                  = data.google_client_config.default.access_token
}

data "google_client_config" "default" {}