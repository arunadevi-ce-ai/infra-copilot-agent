terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0" 
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.gcp_region
  zone    = var.gcp_zone
}

data "google_client_config" "default" {}