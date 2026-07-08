"""
Infra Copilot — Kubernetes/GCP Monitoring & Remediation Agent
----------------------------------------------------------------
An LLM agent that can:
  - Read pod health and resource usage from a live GKE cluster
    (get_pod_status, get_resource_usage, get_workload_status)
  - Take corrective WRITE actions on pods/deployments
    (restart_pod, set_deployment_image, set_pod_image)

Safety design:
  - Write tools PROPOSE an action (storing it with a confirmation_id) and
    only actually execute it inside the separate /confirm endpoint, after
    explicit human approval.
  - RBAC on the agent's Kubernetes ServiceAccount is scoped narrowly —
    get/list/delete on pods, get/list/patch on deployments, read-only on
    other workload types.
  - AUDIT LOGGING: every proposed action, confirm/reject decision, and
    execution result is logged as a single-line JSON object to stdout,
    automatically collected by GKE's Cloud Logging.

CHANGE IN THIS VERSION: pending_actions moved from an in-memory Python
dict to Redis. This fixes a real limitation: with more than one agent pod
replica, an in-memory dict is NOT shared between pods — a /confirm call
could land on a different pod than the /ask call that created the
confirmation_id, incorrectly reporting "unknown confirmation_id". Redis
gives all replicas a single shared store, and TTL-based expiry means
forgotten proposals don't linger forever either.
"""

import os
import time
import json
import logging
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI
from pydantic import BaseModel
from kubernetes import client, config
from google.cloud import monitoring_v3
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
import uuid
import redis

# ---------------------------------------------------------------------------
# STRUCTURED AUDIT LOGGING
# ---------------------------------------------------------------------------
audit_logger = logging.getLogger("infra_copilot.audit")
audit_logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
audit_logger.addHandler(_handler)
audit_logger.propagate = False


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


# ---------------------------------------------------------------------------
# REDIS CLIENT — shared pending-action store across all agent replicas
# ---------------------------------------------------------------------------
# REDIS_HOST defaults to "redis-service" — the Kubernetes Service name from
# redis-deployment.yaml. Kubernetes' internal DNS resolves this automatically
# within the cluster, so no IP address or extra config is needed.
REDIS_HOST = os.environ.get("REDIS_HOST", "redis-service")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,  # returns str instead of bytes — simpler to work with
)

# How long a proposed-but-unconfirmed action is kept before it's treated as
# expired. Prevents old, forgotten proposals from lingering indefinitely.
PENDING_ACTION_TTL_SECONDS = 3600  # 1 hour


def store_pending_action(confirmation_id: str, action: dict) -> None:
    """Save a proposed action to Redis with an expiry."""
    redis_client.setex(
        f"pending_action:{confirmation_id}",
        PENDING_ACTION_TTL_SECONDS,
        json.dumps(action),
    )


def get_pending_action(confirmation_id: str):
    """Retrieve a proposed action from Redis, or None if it doesn't exist
    (never created, already used, or expired)."""
    raw = redis_client.get(f"pending_action:{confirmation_id}")
    return json.loads(raw) if raw else None


def delete_pending_action(confirmation_id: str) -> None:
    """Remove a pending action after it's been confirmed or rejected —
    same one-time-use behavior as before, just backed by Redis now."""
    redis_client.delete(f"pending_action:{confirmation_id}")


# --- Setup: Kubernetes clients ---
# load_kube_config() reuses the same credentials your `kubectl` command uses
# (set up via `gcloud container clusters get-credentials`). No separate auth
# code needed here.
try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

v1 = client.CoreV1Api()
apps_v1 = client.AppsV1Api()
batch_v1 = client.BatchV1Api()

monitoring_client = monitoring_v3.MetricServiceClient()
PROJECT_ID = os.environ.get("GCP_PROJECT_ID")


# ---------------------------------------------------------------------------
# READ-ONLY TOOLS
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
# WRITE TOOLS — now store proposals in Redis instead of an in-memory dict.
# ---------------------------------------------------------------------------

@tool
def restart_pod(pod_name: str, namespace: str = "default") -> str:
    """Propose restarting a pod by deleting it (Kubernetes will recreate it
    if managed by a Deployment). This does NOT execute immediately — it
    returns a confirmation_id that must be approved via the /confirm
    endpoint before the action runs."""
    confirmation_id = str(uuid.uuid4())
    action = {"action": "restart_pod", "pod_name": pod_name, "namespace": namespace}
    store_pending_action(confirmation_id, action)

    audit_log("action_proposed", confirmation_id=confirmation_id, action="restart_pod",
               pod_name=pod_name, namespace=namespace)

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
    action = {
        "action": "set_deployment_image",
        "deployment_name": deployment_name,
        "container_name": container_name,
        "new_image": new_image,
        "namespace": namespace,
    }
    store_pending_action(confirmation_id, action)

    audit_log("action_proposed", confirmation_id=confirmation_id, action="set_deployment_image",
               deployment_name=deployment_name, container_name=container_name,
               new_image=new_image, namespace=namespace)

    return (
        f"Proposed action: set_deployment_image '{deployment_name}' '{container_name}' in namespace "
        f"'{namespace}' with new image '{new_image}'. Confirmation ID: {confirmation_id}. "
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
    action = {
        "action": "set_pod_image",
        "pod_name": pod_name,
        "container_name": container_name,
        "new_image": new_image,
        "namespace": namespace,
    }
    store_pending_action(confirmation_id, action)

    audit_log("action_proposed", confirmation_id=confirmation_id, action="set_pod_image",
               pod_name=pod_name, container_name=container_name,
               new_image=new_image, namespace=namespace)

    return (
        f"Proposed action: set_pod_image '{pod_name}' '{container_name}' in namespace '{namespace}' "
        f"with new image '{new_image}'. Confirmation ID: {confirmation_id}. "
        f"Call /confirm with this ID and approve=true to execute."
    )


def execute_action(action: dict) -> str:
    """Actually performs a previously-proposed write action. Called only
    from the /confirm endpoint, only after explicit human approval."""
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
# Build the agent
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
# FastAPI app
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
    the confirmation_id returned from /ask. Now reads/deletes from Redis,
    so this works correctly regardless of which agent replica handles the
    request — all replicas read from the same shared store.
    """
    action = get_pending_action(request.confirmation_id)

    if action is None:
        audit_log("confirm_unknown_id", confirmation_id=request.confirmation_id)
        return {"error": "Unknown, already-used, or expired confirmation_id."}

    if not request.approve:
        audit_log("action_rejected", confirmation_id=request.confirmation_id, action=action.get("action"))
        delete_pending_action(request.confirmation_id)
        return {"result": "Action rejected — nothing was changed."}

    audit_log("action_confirmed", confirmation_id=request.confirmation_id, action=action.get("action"))

    result = execute_action(action)
    delete_pending_action(request.confirmation_id)  # one-time use — can't replay it
    return {"result": result}


@app.get("/health")
def health():
    """Health check — also verifies Redis connectivity, since the agent
    can't function correctly (write actions would silently fail) if Redis
    is unreachable."""
    try:
        redis_client.ping()
        redis_status = "ok"
    except redis.exceptions.ConnectionError:
        redis_status = "unreachable"
    return {"status": "ok", "redis": redis_status}