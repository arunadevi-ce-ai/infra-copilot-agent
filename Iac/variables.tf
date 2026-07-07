variable "project_id"{
    type    = string
    description = "project id in gcp platform"
}
variable "project_number"{
    type    = string
    description = "project number in gcp platform"
}
variable "gcp_region"{
    type    = string
    description = "region where the resources will be created"
}
variable "gcp_zone"{
    type    = string
    description = "zone where the resources will be created"
}
variable "service_account_name"{
    type    = string
    description = "name of kubernetes service account"
}
variable "k8s_namespace"{
    type    = string
    description = "cluster namespace where all the kubernetes objects live"
}
variable "github_repo" {
  description = "Exact 'owner/repo' string for the GitHub repository allowed to use WIF (e.g. yourname/infra-copilot-agent)"
  type        = string
}