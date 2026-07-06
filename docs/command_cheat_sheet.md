# Command Cheat Sheet
### Quick reference — Terraform, GKE, kubectl, Docker, and project-specific commands

---

## Terraform

```bash
terraform init                     # download providers, set up working directory (run once per project)
terraform plan                     # preview what will be created/changed/destroyed
terraform apply                    # create/update infrastructure
terraform apply -auto-approve      # skip the yes/no confirmation prompt
terraform destroy                  # tear down everything Terraform created
terraform destroy -target=<resource_address>   # destroy just one specific resource
terraform state list               # list all resources Terraform is currently tracking
terraform output                   # show output values (e.g. cluster endpoint)
terraform fmt                      # auto-format .tf files consistently
terraform validate                 # check syntax without contacting the cloud provider
```

---

## gcloud CLI

```bash
gcloud init                                    # first-time setup: login + select project
gcloud config list                             # show current active account/project
gcloud config set project <PROJECT_ID>         # switch active project

# APIs
gcloud services enable container.googleapis.com
gcloud services enable monitoring.googleapis.com
gcloud services enable iam.googleapis.com
gcloud services enable iamcredentials.googleapis.com
gcloud services list --enabled                 # check which APIs are currently enabled

# Auth (for Terraform / local scripts)
gcloud auth login                              # authenticate your gcloud CLI itself
gcloud auth application-default login          # authenticate for local apps/Terraform (ADC)

# GKE cluster
gcloud container clusters list
gcloud container clusters get-credentials <cluster-name> --region <region>   # wire up kubectl

# IAM
gcloud iam service-accounts list
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:<SA_EMAIL>" \
  --role="roles/<ROLE_NAME>"

# Artifact Registry
gcloud artifacts repositories create <repo-name> \
  --repository-format=docker --location=<region>
gcloud auth configure-docker <region>-docker.pkg.dev   # let Docker push/pull via gcloud auth
```

---

## kubectl — cluster & workload inspection

```bash
kubectl get nodes                              # list cluster nodes
kubectl get pods -A                            # all pods, all namespaces
kubectl get pods -n <namespace>                # pods in one namespace
kubectl get pods -w                            # watch pod status live (updates in real time)
kubectl get deployments -n <namespace>
kubectl get svc -n <namespace>                 # list Services
kubectl get serviceaccount <name> -n <namespace> -o yaml   # inspect a ServiceAccount (check Workload Identity annotation)

kubectl describe pod <pod-name> -n <namespace> # detailed info + Events (great for debugging pull/crash issues)
kubectl logs <pod-name> -n <namespace>         # container logs
kubectl logs <pod-name> -n <namespace> -f      # follow logs live
```

## kubectl — creating/modifying resources

```bash
kubectl apply -f <file.yaml>                   # create/update from a manifest
kubectl create deployment <name> --image=<image> --replicas=<n>
kubectl run <pod-name> --image=<image>         # create a single standalone pod
kubectl delete pod <pod-name> -n <namespace>   # delete a pod (Deployment will recreate it)
kubectl delete -f <file.yaml>                  # delete everything defined in a manifest
kubectl scale deployment <name> --replicas=<n> # scale up/down
kubectl set image deployment/<name> <container-name>=<new-image>   # update image directly via CLI
kubectl rollout status deployment/<name>       # watch a rollout complete
```

## kubectl — networking / access

```bash
kubectl port-forward service/<service-name> <local-port>:<service-port>   # secure local tunnel, no external exposure
kubectl exec -it <pod-name> -- /bin/bash       # shell into a running pod (debugging only)
```

---

## Docker

```bash
docker build -t <image-tag> .                  # build an image from a Dockerfile in the current directory
docker images                                  # list local images
docker run -p 8000:8000 <image-tag>            # run locally, mapping container port to host port
docker push <image-tag>                        # push to a registry (must be authenticated first)
docker ps                                      # list running containers
docker logs <container-id>                     # view container logs
```

---

## Git

```bash
git init
git add .
git status                                     # ALWAYS check before commit — confirm .env/.tfstate aren't listed
git commit -m "message"
git remote add origin <repo-url>
git branch -M main
git push -u origin main
```

---

## Python / venv / pip

```bash
pip install <package> --break-system-packages  # if pip refuses to install system-wide
pip install -r requirements.txt
python -m uvicorn <module>:app --reload --port 8000   # run FastAPI locally (works even if `uvicorn` isn't on PATH)
```

---

## Project-specific quick reference

### Multi-Tool LLM Agent
```bash
python weather_stock_agent.py
```

### AWS Security RAG Assistant
```bash
python build_vectorstore.py    # one-time: load PDF, chunk, embed, save to ./chroma_db
python ask_rag.py              # interactive Q&A — does NOT re-embed
```

### Infra Copilot (GKE + AI Agent)
```bash
# Local testing (before containerizing)
python -m uvicorn infra_monitor_agent_api:app --reload --port 8000

# After deploying to cluster
kubectl get pods -l app=infra-agent-app
kubectl logs <pod-name>
kubectl port-forward service/infra-agent-app-service 8000:8000

# Test flow (in a separate terminal, while port-forward is running)
curl http://localhost:8000/health

curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "what pods are unhealthy right now?"}'

curl -X POST http://localhost:8000/confirm \
  -H "Content-Type: application/json" \
  -d '{"confirmation_id": "<id-from-ask-response>", "approve": true}'
```

---

## Debugging quick-reference (things you actually hit)

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: langchain.text_splitter` | LangChain package reorganization | `pip install langchain-text-splitters`, update import |
| `openai.RateLimitError: insufficient_quota` | No billing / trial credits exhausted | Add payment method / credit on OpenAI billing page |
| `openai.RateLimitError: ...tokens per min (TPM)` | Sent too many tokens in one embedding call | Batch requests (e.g. 50 chunks at a time) with delays |
| Alpha Vantage intermittently "ticker not found" | Free tier rate limit (~1 req/sec) | Add `time.sleep(2)` before each call |
| `kubernetes.config.config_exception.ConfigException: Invalid kube-config` | Running in a pod, but code uses `load_kube_config()` (laptop-only) | Use `load_incluster_config()` inside cluster, with try/except fallback for local |
| Pod stuck in `ImagePullBackOff` after pushing new image | Node's service account lacks Artifact Registry access | Grant `roles/artifactregistry.reader` to the node's Compute Engine service account |
| `/confirm` says "unknown confirmation_id" unexpectedly | Multiple replicas, in-memory state not shared | Set `replicas: 1`, or move state to Redis/DB |

---

## Cost-safety reminder

```bash
terraform destroy       # run this whenever you're stepping away for more than ~1-2 hours
```
GKE Autopilot has no idle cluster-management fee, but running pods still cost money continuously — don't leave test workloads running unattended across a break or overnight.
