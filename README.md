# Foundry IQ v2 — Multi-Agent Orchestration

Multi-agent Microsoft Foundry environment: three Hosted Agents (Microsoft
Agent Framework, Python), with a top-level orchestrator that routes to
in-process specialists for KB/courier and to a Fabric data agent through
an OBO-enabled Foundry Toolbox.

See `.claude/plans/dazzling-watching-garden.md` (on the machine this was
built on) for the full phased plan, decisions, and known risks.

## Architecture

```
                    ┌─────────────────────────────┐
                    │      orchestrator-agent       │  Hosted Agent (MAF container)
                    │   routes each question to     │
                    │   the right tool(s) below      │
                    └───────────────┬─────────────────┘
        ┌───────────────────┬──────┴──────────┐
        ▼                   ▼                 ▼
┌────────────────┐  ┌────────────────┐  ┌──────────────────────────┐
│  kb_agent        │  │ courier_agent   │  │  FoundryToolbox            │
│  (in-process      │  │ (in-process     │  │  → fabric-iq-toolbox       │
│  Agent.as_tool())│  │  Agent.as_tool()│  │  → fabric-dataagent-obo    │
│  → AzureAISearch  │  │  → native       │  │    connection (UserEntra-  │
│    ContextProvider│  │    WebSearchTool│  │    Token, OBO passthrough) │
│  → aw-knowledge-  │  │  (no connection │  │  → Fabric data-agent MCP   │
│    base           │  │  needed)        │  │    endpoint directly       │
└────────────────┘  └────────────────┘  └──────────────────────────┘
```

`kb-agent` and `courier-agent` are also independently deployed as their own standalone
Hosted Agents (see `agents/kb-agent/`, `agents/courier-agent/`), callable directly via
`azd ai agent invoke`.

## Why Fabric needs a toolbox, not an inline tool

Two platform-level findings from this build drove this design:

1. **Foundry-to-Foundry A2A is broken** — `tasks/get` always returns `TaskNotFound`
   after a successful `message/send`, confirmed via both raw JSON-RPC and the
   official `A2APreviewTool` SDK path. `kb_agent`/`courier_agent` are wired in-process
   via `agent_framework.Agent.as_tool()` instead of A2A.
2. **The Microsoft Fabric data agent requires On-Behalf-Of (OBO) user identity** for
   its Foundry-native tool (`MicrosoftFabricPreviewTool`/`fabric_dataagent_preview`) —
   service-principal auth isn't supported there. A hosted container's own
   `DefaultAzureCredential()` identity is fixed and used for every outbound call it
   makes, including inline tool calls — so that native tool never works from a hosted
   agent, regardless of which token was used to reach its public endpoint.

   The fix: **Foundry Toolboxes** are a separate mechanism. `FoundryToolbox` (from
   `agent_framework_foundry_hosting`) forwards a per-request call-id from the hosted
   agent's incoming request to Foundry's MCP proxy, which resolves the *real caller's*
   identity server-side — this **does** support OBO. Our toolbox (`fabric-iq-toolbox`)
   wraps a connection (`fabric-dataagent-obo`, category `RemoteTool`, `authType:
   UserEntraToken`) pointed directly at Fabric's own data-agent MCP endpoint
   (`api.fabric.microsoft.com/v1/mcp/workspaces/{workspaceId}/dataagents/{dataAgentId}/agent`)
   — bypassing the older Foundry-native Fabric tool entirely. This means Fabric access
   through the orchestrator **only works when called with a real signed-in user's
   token** (see `scripts/call_orchestrator_as_user.py`) — not via `azd ai agent invoke`,
   which authenticates as the agent's own service identity.

   Note: Fabric's data-agent MCP endpoint *does* also accept a pure service-principal
   token directly (confirmed by testing) — but the service principal still needs
   Fabric-side data-source-level read permission (not just workspace membership),
   which wasn't pursued further since the OBO path above already works end-to-end.

An earlier design explored a native Foundry **prompt agent** (`orchestrator-prompt`,
server-side/stateless) as a workaround, since prompt agents inherit the calling
request's identity directly. It worked, but was removed once the toolbox approach
proved the hosted container could support OBO after all — one orchestrator, one
form factor.

## Resources (`rg-foundryiq-v2`, UK South)

- Foundry account + project (`iqv2project`) — **stays public** throughout
- Storage account (`aw-docs` container) — public in Phase 1, private endpoint in Phase 8
- Azure AI Search (semantic search enabled, index `aw-docs-index`) — public in Phase 1, private endpoint in Phase 8
- ACR (Premium) — public + IP-allowlisted in Phase 1, private endpoint in Phase 8
- Fabric capacity `fabric3iq` (`rg-3iqdemo`) — external to this resource group; must be **Active** (not Paused) for the Fabric tool to work

## Agents (`agents/`)

- `kb-agent/` — grounded in the Foundry IQ Knowledge Base (Adventure Works PDFs). Hosted, standalone, working.
- `courier-agent/` — FedEx/UPS/DHL assistant via the native `WebSearchTool`. Hosted, standalone, working.
- `orchestrator-agent/` — the top-level orchestrator (hosted). Routes to `kb_agent`/`courier_agent` in-process, and to Fabric via the `fabric-iq-toolbox` toolbox. Must be called with a real user token for Fabric to work (`scripts/call_orchestrator_as_user.py`).

A SharePoint-grounded agent was explored (Work IQ, direct Graph API, the native Foundry SharePoint
tool, and a Copilot Studio agent called via the Direct-to-Engine API) but dropped — every path hit
either a tenant admin-consent wall or an undocumented/broken backend connection lookup. Not part of
this build.

## Setup sequence

1. Deploy `infra/main.bicep` (Phase 1) — core resources, public.
2. Upload the Adventure Works PDFs and build the search index (`scripts/build_search_index.py`).
3. Build/deploy `kb-agent` and `courier-agent` via the standard `azd ai agent init` + `azd deploy` flow.
4. Create the `fabric-dataagent-obo` connection (category `RemoteTool`, `authType: UserEntraToken`,
   target = your Fabric data agent's MCP endpoint) and the `fabric-iq-toolbox` toolbox
   (`scripts/create_fabric_toolbox.py`).
5. Build/deploy `orchestrator-agent` via `azd deploy orchestrator-agent`. Grant its AgentIdentity
   the same RBAC as the other agents (`Search Index Data Reader`, `Search Service Contributor`,
   `Foundry User` on the project).
6. Test: `python scripts/call_orchestrator_as_user.py "<question>"` (uses your own `az login` identity).
7. Lock down Storage/ACR/Search to private endpoints (Foundry stays public).
