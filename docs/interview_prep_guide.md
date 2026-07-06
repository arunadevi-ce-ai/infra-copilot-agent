# Interview Preparation Guide
### Based on: Multi-Tool LLM Agent, AWS Security RAG Assistant, and Infra Copilot (GKE + AI Agent)

---

## How to use this document

For each project, you'll find:
- **The 30-second summary** — how to introduce the project if asked "tell me about a project you built"
- **Likely questions** — grouped by theme
- **How to answer** — key points to hit, in your own words (don't memorize verbatim — understand the "why")
- **Follow-up traps** — questions that probe whether you really understand it or just followed a tutorial

---

## Project 1: Multi-Tool LLM Agent (Weather + Stock)

### 30-second summary
"I built a small agent from scratch, without a framework, that takes a natural-language question, decides whether it needs live weather or stock data, calls the right real API, and gives back a combined natural-language answer. I did this manually first — handling the tool-call JSON, matching call IDs, and the round-trip back to the model — specifically so I understood the mechanics before using LangChain to automate it."

### Likely questions & how to answer

**"Walk me through how tool-calling actually works."**
- The model never executes code — it only returns a structured request (tool name + arguments) saying what it *wants* to call
- Your code is responsible for actually running the function and sending the result back
- The `call_id` field is what links a specific tool's result back to the specific call the model made — critical once multiple tools are called in one turn
- Answer in your own words: "The model plans, my code acts, then I tell the model what happened so it can respond in natural language."

**"What happens if the model calls a tool with bad arguments, or a question needs no tool at all?"**
- This is the "no tool needed" edge case you specifically tested — the response type is `"message"` instead of `"function_call"`, and naive code that assumes every response is a tool call will break here
- Mention you deliberately tested this edge case, not just the happy path

**"Why did you hand-build this before using LangChain?"**
- Understanding the primitives first means you're not just calling an API you don't understand — when LangChain does something unexpected, you can reason about what's likely happening underneath, instead of being stuck

**"What would you change for production?"**
- Rate-limit handling and caching (you hit this for real with Alpha Vantage — a genuine, not hypothetical, answer)
- Retry logic with backoff instead of a fixed `time.sleep()`
- Structured logging instead of `print()`

### Follow-up traps
- **"What's the difference between the `tools` list's `"type": "function"` and the output's `"type": "function_call"`?"** — Don't confuse these. One describes the *category* of tool you registered; the other describes the *kind of output item* the model produced. They're linked by convention (a function-type tool produces a function_call-type output) and by the `name` field, not by being literally the same field.
- **"Why does `arguments` come back as a string, not a dict?"** — JSON is the transport format across the API boundary; you must `json.loads()` it yourself.

---

## Project 2: AWS Security RAG Assistant

### 30-second summary
"I built a Retrieval-Augmented Generation assistant that answers questions about AWS security best practices, grounded in the actual AWS Well-Architected Security Pillar whitepaper — not the model's general training knowledge. It loads the PDF, chunks it, embeds the chunks into a vector database, and at query time retrieves the most relevant chunks and has the model answer strictly from that context."

### Likely questions & how to answer

**"Why chunk the document instead of embedding the whole thing?"**
- A single chunk/embedding represents one "unit of meaning" — a whole 240-page PDF embedded as one vector would be far too generic to match specific questions accurately
- Chunking with overlap (you used ~1000 chars, 150 overlap) balances specificity against not cutting ideas in half at chunk boundaries

**"How does the retrieval actually find relevant chunks?"**
- Both the stored chunks and the incoming question are converted to embeddings (vectors) using the same embedding model
- Similarity search finds chunks whose vectors are closest in meaning to the question's vector — this is *semantic* similarity, not keyword matching, which is why it can match a question phrased differently from the source text

**"How do you prevent the model from hallucinating or making things up?"**
- The prompt explicitly instructs: "answer using only the context below" and "if the context doesn't contain the answer, say you don't know"
- This is a real, deliberate design choice, not automatic — a plain prompt without these instructions is much more likely to blend in outside knowledge

**"What was the hardest technical problem you hit?"**
- Genuinely strong, specific answer available here: you hit a **tokens-per-minute rate limit** embedding 665 chunks in one call (109,000 tokens vs. a 40,000 TPM cap), and fixed it by batching into groups of 50 with delays between calls
- This is a great answer because it's specific, real, and shows you understand *why* it happened, not just that you copy-pasted a fix

**"Why separate `build_vectorstore.py` from `ask_rag.py`?"**
- Embedding is a one-time, costly operation; re-running it on every question wastes time and money
- Separating "build" from "use" is a basic but important production pattern

### Follow-up traps
- **"What happens if two different documents have very similar wording? Could retrieval mix them up?"** — Good opportunity to mention this as a real limitation and a natural next step (e.g., filtering by document/metadata, or citing sources per answer — one of your "possible next steps").
- **"Why Chroma and not [Pinecone/Weaviate/pgvector]?"** — Be honest: Chroma was chosen for local simplicity while learning the mechanic; you know that a production or cloud-hosted setup (e.g., pgvector on Cloud SQL/RDS) would be the natural evolution, and you specifically wrote that as a next step.

---

## Project 3: Infra Copilot — GKE + Terraform + AI Agent (your strongest project)

### 30-second summary
"I built an AI agent that monitors and can remediate issues in a live Kubernetes cluster on GCP — checking pod health, resource usage, and fixing bad container images — deployed as a containerized FastAPI service running inside the same GKE Autopilot cluster it manages. Everything is provisioned as code with Terraform, authentication uses Workload Identity instead of static keys, RBAC is scoped to only the specific actions the agent needs, and any write action requires explicit human confirmation through a propose/confirm API pattern before it executes."

### Why this is your strongest talking point
It combines cloud infra, IaC, Kubernetes, containerization, and AI agent design into one coherent system — most candidates applying for "AI + cloud" roles can only show one side of that, not both.

### Likely questions & how to answer

**"Why Workload Identity instead of a service account key file?"**
- Key files are long-lived, static secrets — if leaked, they work indefinitely until manually revoked
- Workload Identity uses automatic, short-lived token exchange between GKE and GCP IAM — no secret ever sits on disk
- Be ready to explain the two-way binding: the GCP service account trusts the specific Kubernetes ServiceAccount (via an IAM policy binding), and the KSA is annotated pointing back at the GSA

**"How did you scope permissions for the agent?"**
- Deliberately least-privilege: `monitoring.viewer` (read-only) on the GCP side; on the Kubernetes side, separate Roles for exactly what's needed — `get/list/delete` on Pods, `get/list/patch` on Deployments — nothing broader
- You explicitly decided **not** to give the agent write access to StatefulSets, CronJobs, or Jobs, reasoning that those carry higher risk (data/ordering concerns) — a genuinely good design-judgment answer if asked "why didn't you support X"

**"How do you prevent the agent from taking a destructive action on its own?"**
- The write tools don't execute directly — they store a "proposed action" with a `confirmation_id` and return that to the caller
- A separate `/confirm` endpoint executes the action only after explicit approval, and the `confirmation_id` is deleted after use (one-time use — you specifically tested that reusing an ID fails correctly)
- This is functionally the same pattern as an approval gate in a CI/CD pipeline — draw that comparison if asked

**"Why an HTTP API instead of a CLI tool?"**
- A CLI (or `kubectl exec -it`) doesn't reflect how real internal tools are consumed — a proper service, callable by other systems (Slack bot, dashboard, CI/CD), needs to be an API
- Also structurally necessary: your original CLI design used blocking `input()` for confirmation, which cannot work in a containerized, request/response HTTP model — you had to redesign the write tools to *propose* an action and return an ID, rather than block

**"Walk me through what happens when a pod fails to pull its image."**
- Great one to be fluent in, since you hit this for real: **image pulls happen at the node level**, using the node's own service account — NOT the pod's Workload Identity binding. This is a common point of confusion. You fixed it by granting `roles/artifactregistry.reader` to the node's default Compute Engine service account.

**"What's the difference between running this locally vs. inside the cluster?"**
- Locally: `config.load_kube_config()` reads your laptop's kubeconfig (from `gcloud container clusters get-credentials`)
- Inside a pod: there is no kubeconfig file — Kubernetes automatically mounts an in-cluster service account token instead, read via `config.load_incluster_config()`
- You handled both cases in one script with a try/except, so the same code works in both environments

**"What are the current limitations of this design?"**
Be upfront about these — it shows maturity, not weakness:
- `pending_actions` is stored in memory, per-pod — with `replicas > 1`, a `/confirm` call can land on a different pod than the `/ask` call that created it, causing false "unknown confirmation_id" errors. (You identified this yourself — mention that you deliberately noted it as a known limitation.)
- No authentication on the API itself — deliberately kept to `ClusterIP` + `port-forward` rather than exposing externally, specifically because of this gap
- In-memory state is lost on pod restart — a real system would use Redis or a database

**"How would you scale or productionize this?"**
- Shared state (Redis) for `pending_actions`
- API-key or IAP-based auth before any external exposure
- Structured audit logging of every proposed and executed action (important for a tool with infrastructure write access)
- Possibly a Slack integration so approvals happen where the team already works

### Follow-up traps
- **"Isn't giving an AI agent write access to production infrastructure dangerous?"** — Don't get defensive; agree it's a real risk, and describe exactly what you did about it (least-privilege RBAC, human confirmation, one-time-use IDs, no external exposure). This is your chance to show risk-awareness, which matters more to interviewers than the feature itself.
- **"What if the LLM hallucinates a pod name that doesn't exist?"** — The Kubernetes API call itself would fail with a clear `ApiException`/`404`-style error, which your `execute_action()` catches and returns as a readable error message rather than crashing — a good example of defense-in-depth beyond just RBAC.

---

## General cross-project questions to be ready for

**"What was the single hardest bug you fixed across these projects?"**
Good options to pick from, all genuinely yours:
- The `set_pod_image` tool storing the wrong action type/key (a subtle copy-paste bug you caught before it caused a real incident)
- The in-cluster vs. local kubeconfig `ConfigException`
- The Alpha Vantage and OpenAI embedding rate limits

**"How do you decide when an AI agent should ask for confirmation vs. act automatically?"**
- Read-only actions (status checks, metrics) — safe to automate fully
- Write/destructive actions (delete, patch) — always require human confirmation, especially in early-stage or high-blast-radius systems
- This maps directly to how you scoped RBAC and designed the propose/confirm pattern

**"If you had one more week, what would you add?"**
Have 2-3 ready, ideally one per project (shows breadth):
- RAG: cite which document/page an answer came from
- Infra Copilot: Redis-backed pending actions, or a Slack-based confirmation UI
- General: add automated tests (you tested manually and thoroughly, but automated tests would be the natural next maturity step)
