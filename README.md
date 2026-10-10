<div align="center">

# 🛰️ OpsPilot

### Voice + MCP-Native AI Operations Agent

**Talk or type to an AI that answers IT policy questions, manages tickets and projects, and performs privileged actions — only after policy checks and human approval.**

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Azure%20Container%20Apps-0078D4?logo=microsoftazure&logoColor=white)](https://opspilot-app.delightfulwater-bc736702.koreacentral.azurecontainerapps.io)
[![CI/CD](https://img.shields.io/badge/CI%2FCD-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/ci-cd.yml)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-gateway-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-agent-1C3C3C)
![MCP](https://img.shields.io/badge/MCP-3%20tool%20servers-8A2BE2)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Eval](https://img.shields.io/badge/Overall%20eval%20score-0.9685-brightgreen)

**[🚀 Live demo](https://opspilot-app.delightfulwater-bc736702.koreacentral.azurecontainerapps.io)** · **[📊 Evaluation results](#-evaluation)** · **[🏗️ Architecture](#-architecture)** · **[⚡ Quick start](#-quick-start-docker-compose)**

</div>

---

## 📌 TL;DR (for recruiters and hiring managers)

OpsPilot is a **production-style, deployed AI agent** — not a notebook demo. It covers the whole stack an applied-AI / ML platform team cares about:

| Area | What I built |
|---|---|
| **Agent orchestration** | LangGraph state machine: input guardrails → planner ⇄ executor → output guardrails, with checkpointed memory and **human-in-the-loop approval** |
| **Tool layer** | **3 independent MCP servers** (policy search, SQL/HR data, ops actions) exposing **14 tools**, consumed through a client with **circuit breakers** and cached tool discovery |
| **LLM reliability** | **`qwen/qwen3.8-27b` on Groq** as primary, **`gemini/gemini-3.6-flash`** as automatic fallback via a LiteLLM Router (instant failover on rate limits, retries on malformed tool calls) |
| **RAG** | LlamaParse → header-aware chunking → **hybrid search (dense + BM25, RRF fusion)** in Qdrant → **cross-encoder reranking** |
| **Voice** | Real-time voice assistant with Pipecat: VAD, Groq Whisper STT, Kokoro TTS, **barge-in**, and *spoken* approvals ("yes, go ahead") |
| **Security** | JWT auth, bcrypt, escalating lockouts, YAML policy engine (risk tiers + scopes), PII / prompt-injection / output guardrails, full audit log |
| **Evaluation** | 34-case golden dataset, DeepEval LLM-judge metrics + deterministic checks, tracked in **MLflow** — **overall score 0.9685** |
| **DevOps** | 4 Docker images, `docker-compose` with health-gated startup, GitHub Actions → Azure Container Registry → **Azure Container Apps** (OIDC, no stored cloud passwords) |

---

## 📑 Table of contents

1. [What it does](#-what-it-does)
2. [Architecture](#-architecture)
3. [How a request flows](#-how-a-request-flows)
4. [Key engineering decisions](#-key-engineering-decisions)
5. [Evaluation](#-evaluation)
6. [Tech stack](#-tech-stack)
7. [Quick start (Docker Compose)](#-quick-start-docker-compose)
8. [Configuration](#-configuration)
9. [Running without Docker](#-running-without-docker)
10. [Deployment (Azure Container Apps + CI/CD)](#-deployment-azure-container-apps--cicd)
11. [API reference](#-api-reference)
12. [Project structure](#-project-structure)
13. [Known limitations and roadmap](#-known-limitations-and-roadmap)

---

## ✨ What it does

OpsPilot acts as an **IT helpdesk and operations copilot** for a company. An employee or admin signs in and then talks (voice) or types (chat):

- 📚 **Answers policy questions from real documents** — *"Can I use my personal phone to access the HR system?"*, *"What's the SEV-1 response time?"* — grounded in six indexed policy documents (access control, incident response, passwords, monitoring, software requests, VPN).
- 🎫 **Creates and looks up tickets**, searches employees by name or department.
- 🔐 **Performs privileged actions safely** — password resets and access revocation are *DESTRUCTIVE*: the agent **pauses and asks a human to approve** before anything runs. Employees cannot revoke access at all; non-admins cannot touch other people's accounts.
- 📁 **Manages projects** — admins create projects, assign people and move stages; employees report progress or blockers, and the agent draws a text flowchart of project status.
- 🖥️ **Self-service portal + admin console** — tickets, projects, analytics, audit logs, login events, account lock/unlock.
- 🎙️ **Hands-free voice mode** — speak naturally, interrupt the assistant mid-sentence, and approve actions by voice.

> **Live demo note:** the Azure app is configured to **scale to zero when idle** to save cost, so the first request after a quiet period triggers a cold start (the docs server loads its embedding and reranker models). Allow a few minutes, then refresh. Demo credentials are available on request.

---

## 🏗️ Architecture

```mermaid
flowchart TB
    subgraph Client["Browser (static portal)"]
        UI["Chat · Voice · Employee portal · Admin console"]
    end

    subgraph Gateway["Gateway container · FastAPI :8000"]
        API["REST + WebSocket API<br/>JWT auth · portal routes"]
        VOICE["Voice pipeline (Pipecat)<br/>VAD → Whisper STT → Agent → Kokoro TTS"]
        subgraph Agent["LangGraph agent"]
            IG["input_guard<br/>PII + injection"] --> PL["planner<br/>LLM + tool selection"]
            PL -->|tool calls| EX["executor<br/>policy engine + approval"]
            EX --> PL
            PL -->|final answer| OG["output_guard<br/>PII leak + grounding"]
        end
        MCPC["MCP client manager<br/>cached discovery · circuit breakers"]
    end

    subgraph LLM["LiteLLM Router"]
        G1["PRIMARY<br/>Groq · qwen/qwen3.8-27b"]
        G2["FALLBACK<br/>Gemini · gemini-3.6-flash"]
        G1 -. "429 / error → instant failover" .-> G2
    end

    subgraph MCP["MCP tool servers (streamable HTTP)"]
        DOCS["docs-server :8001<br/>search_policy_docs"]
        SQL["sql-server :8002<br/>employees · tickets (read)"]
        OPS["ops-server :8003<br/>tickets · passwords · access · projects"]
    end

    QD[("Qdrant Cloud<br/>dense + BM25 hybrid")]
    DB[("SQLite<br/>shared volume")]

    UI <-->|HTTPS / WSS| API
    API --> Agent
    VOICE --> Agent
    PL <--> LLM
    EX --> MCPC
    DOCS & SQL & OPS
    DOCS --> QD
    SQL --> DB
    OPS --> DB
    API --> DB