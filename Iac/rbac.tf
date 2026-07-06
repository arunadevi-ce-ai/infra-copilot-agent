# -----------------------------------------------------------------------------
# rbac.tf
# -----------------------------------------------------------------------------
# This file grants the agent's Kubernetes ServiceAccount (created in
# workload_identity.tf) exactly the permissions it needs to operate — nothing
# more. This is the Kubernetes-side authorization layer, separate from the
# GCP IAM permissions (monitoring.viewer) granted in workload_identity.tf.
#
# Two distinct Roles are defined, matching the two distinct write actions
# the agent can perform:
#   1. pod-restart-role       -> delete pods (to trigger a restart)
#   2. deployment/pod-patch-role  -> patch deployments/pods (to fix a bad image)
#   3. workload-read-role       -> get and list the workloads (statefulsets, cronjobs, jobs)
# Kept separate rather than combined into one broad Role, so each capability
# is independently auditable and revocable.
# -----------------------------------------------------------------------------

# --- Role 1: allow reading and deleting pods (used by restart_pod tool) -----
resource "kubernetes_role" "pod_restart_role" {
  metadata {
    name      = "pod-restart-role"
    namespace = var.k8s_namespace
  }

  rule {
    api_groups = [""]                       # "" = the core API group, where Pods live
    resources  = ["pods"]
    verbs      = ["get", "list", "delete"]  # only what restart_pod needs — no create/update
  }
}

resource "kubernetes_role_binding" "pod_restart_binding" {
  metadata {
    name      = "pod-restart-binding"
    namespace = var.k8s_namespace
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.pod_restart_role.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.agent_ksa.metadata[0].name
    namespace = var.k8s_namespace
  }
}

# --- Role 2: allow reading and patching deployments (used by set_deployment_image tool) ---
resource "kubernetes_role" "deployment_patch_role" {
  metadata {
    name      = "deployment-patch-role"
    namespace = var.k8s_namespace
  }

  rule {
    api_groups = ["apps"]                  # Deployments live in the "apps" API group
    resources  = ["deployments"]
    verbs      = ["get", "list", "patch"]  # patch only — no delete, no create
  }
}

resource "kubernetes_role_binding" "deployment_patch_binding" {
  metadata {
    name      = "deployment-patch-binding"
    namespace = var.k8s_namespace
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.deployment_patch_role.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.agent_ksa.metadata[0].name
    namespace = var.k8s_namespace
  }
}

# --- Role 3: allow reading and patching pod (used by set_pod_image tool) ---
resource "kubernetes_role" "pod_patch_role" {
  metadata {
    name      = "pod-patch-role"
    namespace = var.k8s_namespace
  }

  rule {
    api_groups = [""]                
    resources  = ["pods"]
    verbs      = ["get", "list", "patch"]  # patch only — no delete, no create
  }
}

resource "kubernetes_role_binding" "pod_patch_binding" {
  metadata {
    name      = "pod-patch-binding"
    namespace = var.k8s_namespace
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.pod_patch_role.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.agent_ksa.metadata[0].name
    namespace = var.k8s_namespace
  }
}

# ----Role 4: read workload status
resource "kubernetes_role" "workload_read_role" {
  metadata {
    name      = "workload-read-role"
    namespace = var.k8s_namespace
  }

  rule {
    api_groups = ["apps","batch"]              # core API group — where Pods live
    resources  = ["statefulsets","jobs","cronjobs"]
    verbs      = ["get", "list"]
  }
}

resource "kubernetes_role_binding" "workload_read_binding" {
  metadata {
    name      = "workload-read-binding"
    namespace = var.k8s_namespace
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.workload_read_role.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.agent_ksa.metadata[0].name
    namespace = var.k8s_namespace
  }
}
