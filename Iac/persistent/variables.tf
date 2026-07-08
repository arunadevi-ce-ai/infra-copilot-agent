variable "project_id"{
    type    = string
    description = "project id in gcp platform"
}

variable "gcp_region"{
    type    = string
    description = "region where the resources will be created"
}
variable "gcp_zone"{
    type    = string
    description = "zone where the resources will be created"
}

variable "github_repo" {
  description = "Exact 'owner/repo' string for the GitHub repository allowed to use WIF (e.g. yourname/infra-copilot-agent)"
  type        = string
}