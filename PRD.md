This Product Requirements Document (PRD) outlines the blueprint for building an enterprise-grade AI agent platform, heavily inspired by the architecture of the `nanobot` codebase you provided.

---

# Product Requirements Document (PRD): Enterprise AI Orchestrator

**Project Code Name:** _Nexus_ (Proposed)

**Version:** 1.0

**Status:** Draft

## 1. Executive Summary

**Vision:** To create a lightweight, highly extensible AI agent platform that unifies corporate communication channels (WhatsApp, Slack, Telegram) with autonomous execution capabilities. Unlike rigid chatbots, _Nexus_ serves as an active employee capable of using tools, managing files, browsing the web, and spawning sub-agents to complete complex workflows.

**Core Value Proposition:**

- **Vendor Agnostic:** Switch between OpenAI, Anthropic, or Local LLMs instantly.
    
- **Universal Interface:** One bot accessible via CLI, WhatsApp, or API.
    
- **Action-Oriented:** Not just chat; it executes code, manages schedules, and interacts with external systems.
    

---

## 2. User Personas

1. **The Operator (End User):** Uses the bot via WhatsApp/Slack to request reports, schedule tasks, or query internal databases.
    
2. **The Developer (Extender):** Writes Python classes to add new "Tools" (integrations) to the bot.
    
3. **The Admin:** Manages API keys, monitors token usage, and configures the LLM provider.
    

---

## 3. Functional Requirements

### 3.1. Core Agent Loop ("The Brain")

- **Context Management:** The system must dynamically assemble a "System Prompt" for every turn, injecting:
    
    - **User Profile:** Who is talking? (Derived from `USER.md`).
        
    - **Short-term Memory:** Recent chat history.
        
    - **Long-term Memory:** Relevant excerpts from `MEMORY.md`.
        
    - **Available Tools:** JSON definitions of what the bot can do.
        
- **LLM Abstraction:** Must use a wrapper (like `litellm`) to support:
    
    - GPT-5.2 (Complex reasoning)
        
    - Claude 4.5 Sonnet (Coding tasks)
        
    - Gemini-3-flash (Cost efficiency)
        
- **Decision Engine:** The agent must automatically decide whether to reply with text OR execute a tool based on user intent.
    

### 3.2. Channel Connectivity ("The Nervous System")

The system must decouple "Listening" from "Thinking" using a Message Bus pattern.

- **Message Bus:** A pub/sub system where incoming messages are normalized into a standard `InboundMessage` object.
    
- **WhatsApp Bridge (High Priority):**
    
    - **Architecture:** A standalone Node.js microservice using `@whiskeysockets/baileys`.
        
    - **Communication:** WebSocket connection between the Python Core and Node.js Bridge.
        
    - **Features:** QR Code login, text send/receive, media handling (images/docs).
        
- **Other Channels:** Support for Telegram (polling), Slack (Events API), and CLI (STDIN/STDOUT).
    

### 3.3. Tool Ecosystem ("The Hands")

The bot must support a plugin architecture for tools.

- **FileSystem Tool:**
    
    - Capabilities: `read_file`, `write_file`, `list_dir`, `grep_search`.
        
    - _Security Constraint:_ Operations must be sandboxed to a specific `./workspace` directory to prevent system damage.
        
- **Web Surfer Tool:**
    
    - Capabilities: `search_web` (via Brave/Google API), `fetch_url` (scrape and convert HTML to Markdown).
        
- **Scheduler (Cron) Tool:**
    
    - Capabilities: Allow users to say "Remind me every Monday at 9 AM" and convert that into a system cron job.
        
- **Sub-Agent Tool:**
    
    - Capabilities: Ability to spawn a child process to handle a long-running task (e.g., "Research this topic and write a report") without blocking the main chat.
        

### 3.4. Memory Systems

- **Session Memory:** In-memory list of the last $N$ turns.
    
- **Persistent Memory:**
    
    - **File-Based:** The bot should read/write to Markdown files (`MEMORY.md`) for human-readable, transparent memory storage.
        
    - **Journaling:** Auto-generate daily logs (e.g., `memories/2026-02-09.md`) to track what happened each day.
        

---

## 4. Technical Architecture

### 4.1. High-Level Diagram

### 4.2. Technology Stack

- **Core Backend:** Python 3.11+
    
- **LLM Interface:** `litellm` (Standardizes API calls to OpenAI, Azure, etc.)
    
- **WhatsApp Service:** TypeScript / Node.js 20+ (using `Baileys` library)
    
- **Inter-Process Communication:** WebSockets (for Python <-> Node.js)
    
- **Data Validation:** `Pydantic`
    
- **Configuration:** `TOML` or `.env` files.
    

### 4.3. Directory Structure (Proposed)

Plaintext

```
nexus-core/
├── bridge/                 # Node.js Service for WhatsApp
│   ├── src/whatsapp.ts     # Baileys logic
│   └── src/server.ts       # WebSocket Server
├── nexus/
│   ├── core/               # Main Logic
│   │   ├── loop.py         # Agent Thought Loop
│   │   └── bus.py          # Message Queue System
│   ├── tools/              # Plugin Directory
│   │   ├── web.py
│   │   └── files.py
│   └── memory/             # Storage logic
└── workspace/              # Sandboxed area for bot file operations
```

---

## 5. Security & Compliance

1. **Sandboxing:** The `ShellTool` (executing code) is dangerous. For the flagship product, this must run inside a **Docker Container** or a restricted environment (e.g., `e2b` sandbox) so the bot cannot delete system files.
    
2. **PII Filtering:** Ensure the logger creates a `redacted.log` that strips phone numbers and API keys before saving logs.
    
3. **Human-in-the-Loop:** Critical actions (like `delete_file` or `email_broadcast`) should require a confirmation step ("I am about to delete X. Proceed? Y/N").
    

---

## 6. Roadmap

### Phase 1: The MVP (Weeks 1-3)

- **Goal:** A CLI-based bot that can chat via GPT-5.2 and read local files.
    
- **Deliverables:**
    
    - Python project skeleton.
        
    - `litellm` integration.
        
    - Basic `MessageBus`.
        
    - `FileSystem` tools.
        

### Phase 2: The Connectivity Layer (Weeks 4-6)

- **Goal:** Get it off the terminal and onto WhatsApp.
    
- **Deliverables:**
    
    - Build the Node.js Bridge (`bridge/`).
        
    - Implement WebSocket client in Python.
        
    - Handle QR code generation and session persistence.
        

### Phase 3: Intelligence & Web (Weeks 7-9)

- **Goal:** Make it useful for research and scheduling.
    
- **Deliverables:**
    
    - Integrate Brave Search API.
        
    - Implement URL scraper (Readability).
        
    - Add `CronTool` for recurring tasks.
        

### Phase 4: Enterprise Hardening (Weeks 10+)

- **Goal:** Prepare for company-wide deployment.
    
- **Deliverables:**
    
    - Dockerize the entire application.
        
    - Add Multi-user support (mapping WhatsApp numbers to specific `USER_{ID}.md` profiles).
        
    - Implement rate limiting to control LLM costs.
