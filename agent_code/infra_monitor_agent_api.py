"""
Infra Copilot — Kubernetes/GCP Monitoring & Remediation Agent
----------------------------------------------------------------
An LLM agent that can:
  - Read pod health and resource usage from a live GKE cluster
    (get_pod_status, get_resource_usage, get_workload_status)
  - Take corrective WRITE actions on pods/deployments
    (restart_pod, set_deployment_image, set_pod_image)

Safety design:
  - Every WRITE tool prompts for explicit "yes/no" confirmation in the
    terminal BEFORE performing the action. Since create_agent() executes
    tools automatically, the confirmation prompt lives INSIDE each tool
    function itself — this is the only place we can reliably pause and
    ask before the real Kubernetes API call happens.
  - RBAC on the agent's Kubernetes ServiceAccount is scoped narrowly
    (see rbac.tf) — only get/list/delete on pods, and get/list/patch on
    deployments, plus read-only on other workload types. The agent
    cannot do anything outside what's explicitly granted, regardless of
    what the LLM decides to attempt.
  - AUDIT LOGGING: every proposed action, confirm/reject decision, and
    execution result is logged as a single-line JSON object to stdout.
    On GKE, stdout is automatically collected by Cloud Logging — no extra
    agent or sidecar needed — so these become queryable, filterable audit
    events without any additional infrastructure.
"""

import os
import time
import json
import logging
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()  # loads OPENAI_API_KEY and GCP_PROJECT_ID from .env — must run first
from fastapi import FastAPI
from pydantic import BaseModel
from kubernetes import client, config
from google.cloud import monitoring_v3
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
import uuid

# ---------------------------------------------------------------------------
# STRUCTURED AUDIT LOGGING
# ---------------------------------------------------------------------------
# A dedicated logger, separate from Python's default root logger, so audit
# events are clearly distinguishable from ordinary application logs (e.g.
# FastAPI/uvicorn startup messages) if you ever filter by logger name.
#
# Each event is emitted as ONE JSON object per line (not pretty-printed) —
# this is the standard format most log aggregators (Cloud Logging, ELK,
# Datadog, etc.) expect for structured logs, since each line parses cleanly
# as one independent JSON record.
audit_logger = logging.getLogger("infra_copilot.audit")
audit_logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))  # message IS the JSON — no extra prefix
audit_logger.addHandler(_handler)
audit_logger.propagate = False  # avoid double-logging via the root logger


def audit_log(event: str, **fields) -> None:
    """
    Emit one structured audit event as a JSON line to stdout.

    event: a short, consistent event name, e.g. "action_proposed",
           "action_confirmed", "action_rejected", "action_executed",
           "action_failed", "question_asked".
    fields: any additional structured data relevant to this event
            (confirmation_id, action type, namespace, pod name, etc.)
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    audit_logger.info(json.dumps(record))


# --- Setup: Kubernetes clients ---
# load_kube_config() reuses the same credentials your `kubectl` command uses
# (set up via `gcloud container clusters get-credentials`). No separate auth
# code needed here.
try:
    # This works INSIDE a pod — reads the automatically-mounted
    # ServiceAccount token and cluster CA cert from
    # /var/run/secrets/kubernetes.io/serviceaccount/
    config.load_incluster_config()
except config.ConfigException:
    # Fallback for when you run this script locally on your laptop —
    # uses your local kubeconfig (from `gcloud container clusters
    # get-credentials`) instead.
    config.load_kube_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()
batch_v1 = client.BatchV1Api()

# --- Setup: Cloud Monitoring client ---
monitoring_client = monitoring_v3.MetricServiceClient()
PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

# In-memory store for pending write actions awaiting confirmation.
# Key: confirmation_id (string) -> Value: dict describing the action to run.
# NOTE: this is fine for a learning/demo project. In a real production
# system, you'd use something persistent (Redis, a database) since this
# dict is wiped if the pod restarts.
pending_actions = {}


# ---------------------------------------------------------------------------
# READ-ONLY TOOLS — safe to let the agent call automatically, no confirmation
# needed since they can't change anything. Not audit-logged individually,
# since they carry no risk — audit logging is reserved for actions that
# change cluster state or the decisions around them.
# ---------------------------------------------------------------------------

@tool
def get_pod_status(namespace: str = "default") -> str:
    """Get the status and health of pods in a given Kubernetes namespace,
    including any failure reasons like ImagePullBackOff or CrashLoopBackOff."""
    pods = v1.list_namespaced_pod(namespace=namespace)

    if not pods.items:
        return f"No pods found in namespace '{namespace}'."

    lines = []
    for pod in pods.items:
        name = pod.metadata.name
        phase = pod.status.phase

        restarts = 0
        reasons = []
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                restarts += cs.restart_count
                if cs.state.waiting:
                    reasons.append(cs.state.waiting.reason)

        status_detail = f"{phase}"
        if reasons:
            status_detail += f" ({', '.join(reasons)})"
        lines.append(f"- {name}: {status_detail}, restarts={restarts}")

    return "\n".join(lines)


@tool
def get_cpu_usage(namespace: str = "default", minutes: int = 10) -> str:
    """Get average CPU usage in cores for containers in a given Kubernetes namespace."""
    project_name = f"projects/{PROJECT_ID}"
    now = time.time()
    interval = monitoring_v3.TimeInterval({
        "end_time": {"seconds": int(now)},
        "start_time": {"seconds": int(now - minutes * 60)},
    })
    metric_filter = (
        f'metric.type = "kubernetes.io/container/cpu/core_usage_time" '
        f'AND resource.labels.namespace_name = "{namespace}"'
    )
    results = monitoring_client.list_time_series(
        request={"name": project_name, "filter": metric_filter, "interval": interval,
                  "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL}
    )
    lines = []
    for series in results:
        pod = series.resource.labels.get("pod_name", "unknown")
        container = series.resource.labels.get("container_name", "unknown")
        if series.points:
            value = series.points[0].value.double_value
            lines.append(f"- pod={pod}, container={container}: {value:.4f} CPU cores")
    return "\n".join(lines) if lines else f"No CPU data found for namespace '{namespace}'."


@tool
def get_memory_usage(namespace: str = "default", minutes: int = 10) -> str:
    """Get memory usage in MiB for containers in a given Kubernetes namespace."""
    project_name = f"projects/{PROJECT_ID}"
    now = time.time()
    interval = monitoring_v3.TimeInterval({
        "end_time": {"seconds": int(now)},
        "start_time": {"seconds": int(now - minutes * 60)},
    })
    metric_filter = (
        f'metric.type = "kubernetes.io/container/memory/used_bytes" '
        f'AND resource.labels.namespace_name = "{namespace}"'
    )
    results = monitoring_client.list_time_series(
        request={"name": project_name, "filter": metric_filter, "interval": interval,
                  "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL}
    )
    lines = []
    for series in results:
        pod = series.resource.labels.get("pod_name", "unknown")
        container = series.resource.labels.get("container_name", "unknown")
        if series.points:
            mib = series.points[0].value.int64_value / (1024 * 1024)
            lines.append(f"- pod={pod}, container={container}: {mib:.1f} MiB")
    return "\n".join(lines) if lines else f"No memory data found for namespace '{namespace}'."


@tool
def get_workload_status(namespace: str = "default") -> str:
    """Get status of Deployments, StatefulSets, DaemonSets, Jobs, and CronJobs
    in a namespace. Read-only — gives a broader picture beyond just Pods."""
    lines = []

    for d in apps_v1.list_namespaced_deployment(namespace).items:
        lines.append(f"Deployment {d.metadata.name}: {d.status.ready_replicas or 0}/{d.status.replicas} ready")

    for s in apps_v1.list_namespaced_stateful_set(namespace).items:
        lines.append(f"StatefulSet {s.metadata.name}: {s.status.ready_replicas or 0}/{s.status.replicas} ready")

    for j in batch_v1.list_namespaced_job(namespace).items:
        lines.append(f"Job {j.metadata.name}: succeeded={j.status.succeeded or 0}, failed={j.status.failed or 0}")

    for c in batch_v1.list_namespaced_cron_job(namespace).items:
        lines.append(f"CronJob {c.metadata.name}: last scheduled={c.status.last_schedule_time}")

    return "\n".join(lines) if lines else f"No workloads found in namespace '{namespace}'."


# ---------------------------------------------------------------------------
# WRITE TOOLS — these propose (not execute) a cluster change. Each one is
# audit-logged the moment it's proposed, BEFORE any human has approved it —
# this matters because even a proposal the human later rejects is worth
# having a record of (e.g. "the LLM attempted to delete pod X, but a human
# rejected it" is itself useful audit information).
# ---------------------------------------------------------------------------

@tool
def restart_pod(pod_name: str, namespace: str = "default") -> str:
    """Propose restarting a pod by deleting it (Kubernetes will recreate it
    if managed by a Deployment). This does NOT execute immediately — it
    returns a confirmation_id that must be approved via the /confirm
    endpoint before the action runs."""

    confirmation_id = str(uuid.uuid4())

    pending_actions[confirmation_id] = {
        "action": "restart_pod",
        "pod_name": pod_name,
        "namespace": namespace,
    }

    audit_log(
        "action_proposed",
        confirmation_id=confirmation_id,
        action="restart_pod",
        pod_name=pod_name,
        namespace=namespace,
    )

    return (
        f"Proposed action: restart pod '{pod_name}' in namespace '{namespace}'. "
        f"Confirmation ID: {confirmation_id}. "
        f"Call /confirm with this ID and approve=true to execute."
    )


@tool
def set_deployment_image(deployment_name: str, container_name: str, new_image: str, namespace: str = "default") -> str:
    """Propose updating the container image for a given Kubernetes Deployment.
    Use this to fix a bad/misspelled image causing ImagePullBackOff.
    This does NOT execute immediately — it returns a confirmation_id that
    must be approved via the /confirm endpoint before the action runs."""

    confirmation_id = str(uuid.uuid4())

    pending_actions[confirmation_id] = {
        "action": "set_deployment_image",
        "deployment_name": deployment_name,
        "container_name": container_name,
        "new_image": new_image,
        "namespace": namespace,
    }

    audit_log(
        "action_proposed",
        confirmation_id=confirmation_id,
        action="set_deployment_image",
        deployment_name=deployment_name,
        container_name=container_name,
        new_image=new_image,
        namespace=namespace,
    )

    return (
        f"Proposed action: set_deployment_image '{deployment_name}' '{container_name}' in namespace '{namespace}' with new image '{new_image}'. "
        f"Confirmation ID: {confirmation_id}. "
        f"Call /confirm with this ID and approve=true to execute."
    )


@tool
def set_pod_image(pod_name: str, container_name: str, new_image: str, namespace: str = "default") -> str:
    """Propose updating the container image for a standalone pod.
    Use this to fix a bad/misspelled image causing ImagePullBackOff.
    Note: this only works cleanly for standalone pods — pods managed by a
    Deployment/ReplicaSet will usually be reverted by their controller, so
    prefer set_deployment_image for managed pods. This does NOT execute
    immediately — it returns a confirmation_id that must be approved via
    the /confirm endpoint before the action runs."""

    confirmation_id = str(uuid.uuid4())

    pending_actions[confirmation_id] = {
        "action": "set_pod_image",
        "pod_name": pod_name,
        "container_name": container_name,
        "new_image": new_image,
        "namespace": namespace,
    }

    audit_log(
        "action_proposed",
        confirmation_id=confirmation_id,
        action="set_pod_image",
        pod_name=pod_name,
        container_name=container_name,
        new_image=new_image,
        namespace=namespace,
    )

    return (
        f"Proposed action: set_pod_image '{pod_name}' '{container_name}' in namespace '{namespace}' with new image '{new_image}'. "
        f"Confirmation ID: {confirmation_id}. "
        f"Call /confirm with this ID and approve=true to execute."
    )


def execute_action(action: dict) -> str:
    """Actually performs a previously-proposed write action. Called only
    from the /confirm endpoint, only after explicit human approval.

    Every outcome — success or failure — is audit-logged, including the
    specific Kubernetes API error reason on failure, so a failed remediation
    attempt is just as visible in the audit trail as a successful one."""

    action_type = action["action"]

    if action_type == "restart_pod":
        try:
            v1.delete_namespaced_pod(name=action["pod_name"], namespace=action["namespace"])
            result = f"Pod '{action['pod_name']}' deleted — Kubernetes will recreate it if managed by a Deployment."
            audit_log("action_executed", action=action_type, pod_name=action["pod_name"],
                       namespace=action["namespace"], success=True)
            return result
        except client.exceptions.ApiException as e:
            audit_log("action_failed", action=action_type, pod_name=action["pod_name"],
                       namespace=action["namespace"], success=False, error=e.reason)
            return f"Failed to restart pod: {e.reason}"

    elif action_type == "set_deployment_image":
        try:
            patch = {"spec": {"template": {"spec": {"containers": [
                {"name": action["container_name"], "image": action["new_image"]}
            ]}}}}
            apps_v1.patch_namespaced_deployment(
                name=action["deployment_name"], namespace=action["namespace"], body=patch
            )
            result = f"Deployment '{action['deployment_name']}' updated to image '{action['new_image']}'."
            audit_log("action_executed", action=action_type, deployment_name=action["deployment_name"],
                       new_image=action["new_image"], namespace=action["namespace"], success=True)
            return result
        except client.exceptions.ApiException as e:
            audit_log("action_failed", action=action_type, deployment_name=action["deployment_name"],
                       namespace=action["namespace"], success=False, error=e.reason)
            return f"Failed to update deployment: {e.reason}"

    elif action_type == "set_pod_image":
        try:
            patch = {"spec": {"containers": [
                {"name": action["container_name"], "image": action["new_image"]}
            ]}}
            v1.patch_namespaced_pod(
                name=action["pod_name"], namespace=action["namespace"], body=patch
            )
            result = f"Pod '{action['pod_name']}' container updated to image '{action['new_image']}'."
            audit_log("action_executed", action=action_type, pod_name=action["pod_name"],
                       new_image=action["new_image"], namespace=action["namespace"], success=True)
            return result
        except client.exceptions.ApiException as e:
            audit_log("action_failed", action=action_type, pod_name=action["pod_name"],
                       namespace=action["namespace"], success=False, error=e.reason)
            return f"Failed to update pod: {e.reason}"

    audit_log("action_failed", action=action_type, success=False, error="Unknown action type")
    return "Unknown action type."


# ---------------------------------------------------------------------------
# Build the agent — combining all read and write tools into one.
# ---------------------------------------------------------------------------
llm = ChatOpenAI(model="gpt-5.4-mini")

tools = [
    get_pod_status,
    get_cpu_usage,
    get_memory_usage,
    get_workload_status,
    restart_pod,
    set_deployment_image,
    set_pod_image,
]

agent = create_agent(llm, tools)

# ---------------------------------------------------------------------------
# FastAPI app — HTTP interface for the agent
# ---------------------------------------------------------------------------
app = FastAPI(title="Infra Copilot Agent")


class AskRequest(BaseModel):
    question: str


class ConfirmRequest(BaseModel):
    confirmation_id: str
    approve: bool


@app.post("/ask")
def ask(request: AskRequest):
    """
    Send a question to the agent. If the agent decides a write action is
    needed, the response will include a confirmation_id — no cluster
    change happens until that ID is submitted to /confirm with approve=true.
    """
    audit_log("question_asked", question=request.question)
    result = agent.invoke({"messages": [{"role": "user", "content": request.question}]})
    answer = result["messages"][-1].content
    return {"answer": answer}


@app.post("/confirm")
def confirm(request: ConfirmRequest):
    """
    Approve or reject a previously proposed write action, referenced by
    the confirmation_id returned from /ask.
    """
    action = pending_actions.get(request.confirmation_id)

    if action is None:
        audit_log("confirm_unknown_id", confirmation_id=request.confirmation_id)
        return {"error": "Unknown or already-used confirmation_id."}

    if not request.approve:
        audit_log("action_rejected", confirmation_id=request.confirmation_id, action=action.get("action"))
        del pending_actions[request.confirmation_id]
        return {"result": "Action rejected — nothing was changed."}

    audit_log("action_confirmed", confirmation_id=request.confirmation_id, action=action.get("action"))

    # Only NOW does the real Kubernetes API call happen.
    result = execute_action(action)
    del pending_actions[request.confirmation_id]  # one-time use — can't replay it
    return {"result": result}


@app.get("/health")
def health():
    """Simple health check — useful for Kubernetes liveness/readiness probes later."""
    return {"status": "ok"}
