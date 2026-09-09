# Foundry IQ v2 — Private Multi-Agent Orchestration

Three hosted agents (Microsoft Agent Framework, Python) running on a fully
VNet-integrated Microsoft Foundry environment: `publicNetworkAccess:
Disabled` on the Foundry account, and every dependent data service behind a
private endpoint.

See `.claude/plans/dazzling-watching-garden.md` (on the machine this was
built on) for the full phased plan and decisions.

## Architecture

```
                          Internet (blocked)
                                 x
┌──────────────────────────── VNet: foundryiqv2-vnet ─────────────────────────────┐
│                                                                                   │
│  ┌─────────────────┐   ┌──────────────────────┐   ┌───────────────────────────┐  │
│  │  agent-subnet     │   │  pe-subnet             │   │  jumpbox-subnet             │  │
│  │  (delegated to    │   │  private endpoints:     │   │  ci-foundryiq-jump (ACI)   │  │
│  │  Microsoft.App/   │   │  Foundry account,       │   │  -- shell access via        │  │
│  │  environments)    │   │  Search, Storage,        │   │  `az container exec`,       │  │
│  │                   │   │  ACR, Cosmos DB          │   │  no VM/Bastion needed        │  │
│  │  Foundry account   │   └──────────────────────┘   └───────────────────────────┘  │
│  │  (network-injected,                                                              │
│  │   publicNetworkAccess: Disabled) ──── orchestrator-agent ──┬── kb_agent (in-proc) │
│  │   kb-agent, courier-agent,                                 ├── courier_agent (in-proc)│
│  │   orchestrator-agent (hosted)                              └── FoundryToolbox ────┼──▶ Fabric
│  └─────────────────┘                                                                │  (private
│                                                                                       │   link)
│  mcp-subnet (reserved, unused -- no self-hosted MCP/OpenAPI/Function/A2A servers)     │
└───────────────────────────────────────────────────────────────────────────────────┘
```

`kb-agent` and `courier-agent` are also independently registered as their own hosted
agent versions in the same project — reachable directly (from inside the VNet), no
orchestrator required.

## Three hard-won findings that shaped this design

1. **Foundry-to-Foundry A2A is broken.** `tasks/get` always returns `TaskNotFound` after
   a successful `message/send` — confirmed via both raw JSON-RPC and the official
   `A2APreviewTool` SDK path. `kb_agent`/`courier_agent` are wired into the orchestrator
   in-process via `agent_framework.Agent.as_tool()` instead.
2. **The "BYO VNet" template you get pointed at by default doesn't support MCP tools
   behind the VNet.** `anihitk07/foundry-hosted-agents-e2e-samples`' `04-byo-vnet-private`
   mirrors Microsoft's template **15**, whose README says outright it doesn't support
   agent tools (MCP, OpenAPI, Functions, A2A) behind the VNet. Template **19**
   (`azure-ai-foundry/foundry-samples`, `19-private-network-agent-tools`) does — its
   supported-tools list explicitly names Fabric Data Agent and MCP, and it registers a
   `privatelink.fabric.microsoft.com` DNS zone (real private-link support for Fabric,
   not a guess). This repo's `infra/` is adapted from template 19, not 15.
3. **Native web search *does* work behind this VNet posture.** This was flagged as an
   open risk in the plan before building (web search isn't in template 19's
   documented supported-tools list) — testing confirmed it works fine with no
   workaround needed. `courier-agent` needs nothing special.

Also confirmed the hard way: a Foundry account's `networkInjections` is set at
creation time and can't be retrofitted onto an existing account — there's no
"convert to private" path, only "create a new private one." The original public
account (`foundryiqv2pau4`) was decommissioned once this environment was verified
working end-to-end.

## Resources (`rg-foundryiq-v2`, UK South)

- `foundryiqv2p3ygk` — Foundry account (network-injected, `publicNetworkAccess: Disabled`) + project `iqv2project`
- `foundryiqv2-vnet` — VNet, 4 subnets (agent, pe, mcp, jumpbox), 12 private DNS zones
- `foundryiqv2pau4search` — AI Search (semantic search, index `aw-docs-index`) — **stays public**, only gained a private endpoint alongside (needed so `scripts/build_search_index.py` can still run from a normal dev machine)
- `foundryiqv2pau4stor` — Storage (`aw-docs` container) — public-network access was already governance-locked to `Disabled` in this tenant before this project started; gained a private endpoint too
- `acrfoundryiqv2pau4` — ACR (Premium) — **stays public** (needed for `az acr build`, since the jumpbox has no Docker), gained a private endpoint alongside
- `foundryiqv25hdbcosmos` — Cosmos DB for NoSQL (new; required by Foundry's standard agent setup, didn't exist before) — private endpoint only
- `law-foundryiqv2-*` — Log Analytics workspace (for Application Insights / agent tracing)
- `ci-foundryiq-jump` — jumpbox (Azure Container Instance in `jumpbox-subnet`) — see below
- Fabric capacity `fabric3iq` (`rg-3iqdemo`) — external to this resource group; must be **Active** (not Paused) for the Fabric tool to work

## Why the jumpbox is a container, not a VM

This subscription has zero available VM SKUs in UK South (`az vm list-skus` returns no
unrestricted sizes at all) — the same compute restriction hit earlier in this project,
where Azure Container Instances was the one working option. So the jumpbox is an ACI
container instead of the reference architecture's VM + Bastion: `az container exec`
gives shell access directly, no Bastion needed.

Real consequence: **the jumpbox has no Docker** (no Docker-in-Docker in standard ACI),
so the usual `azd deploy` build+push+register flow doesn't work from inside the VNet.
See "Deploying agent updates" below for the actual two-step process this project uses
instead.

## Agents (`agents/`)

- `kb-agent/` — grounded in the Foundry IQ Knowledge Base (Adventure Works PDFs)
- `courier-agent/` — FedEx/UPS/DHL assistant via the native `WebSearchTool`
- `orchestrator-agent/` — routes to `kb_agent`/`courier_agent` in-process, and to Fabric
  via the `fabric-iq-toolbox` toolbox (OBO-enabled — see the toolbox's own
  `UserEntraToken` connection, `fabric-dataagent-obo`)

None of these agents' Python code changed for the migration to private — only how
they're built and registered changed (see below).

A SharePoint-grounded agent was explored earlier (Work IQ, direct Graph API, the native
Foundry SharePoint tool, a Copilot Studio agent via the Direct-to-Engine API) but
dropped — every path hit either a tenant admin-consent wall or an undocumented/broken
backend connection lookup. Not part of this build.

## Infra (`infra/`)

Numbered by deployment order — `01` and `02` have no dependency on each other's
completion beyond both needing the VNet, `03` needs both, `04` (jumpbox) only needs `01`:

- `01-network.bicep` — VNet + 3 subnets + 12 private DNS zones (`modules/vnet.bicep`, `modules/dns-zones.bicep`)
- `02-data-services.bicep` — private endpoints for the *existing* Search/Storage/ACR (no recreation) + new Cosmos DB + Log Analytics
- `03-foundry-account.bicep` — the network-injected Foundry account + project + model deployments + capability host + RBAC + connections (Cosmos/Storage/Search/ACR)
- `04-jumpbox.bicep` — the ACI jumpbox + its subnet

## Setup sequence (fresh environment)

1. `az deployment group create -g rg-foundryiq-v2 -f infra/01-network.bicep`
2. `az deployment group create -g rg-foundryiq-v2 -f infra/02-data-services.bicep --parameters searchName=<name> storageName=<name> acrName=<name> vnetName=foundryiqv2-vnet peSubnetName=pe-subnet`
3. `az deployment group create -g rg-foundryiq-v2 -f infra/03-foundry-account.bicep --parameters agentSubnetId=<id> peSubnetId=<id> searchName=<name> storageName=<name> cosmosName=<name>`
4. `az deployment group create -g rg-foundryiq-v2 -f infra/04-jumpbox.bicep --parameters vnetName=foundryiqv2-vnet`
5. Upload PDFs and build the search index: `python scripts/build_search_index.py` (runs from a normal dev machine — Search stayed public)
6. From inside the jumpbox (`az container exec -g rg-foundryiq-v2 -n ci-foundryiq-jump --container-name jumpbox --exec-command "az login --use-device-code"`, complete the device-code prompt): `scripts/create_fabric_toolbox.sh <fabric-workspace-id> <fabric-data-agent-id>`
7. Build + register each agent (see below)

## Deploying agent updates

`azd deploy` doesn't work here — it can't reach the private project endpoint from
outside the VNet, and can't build images from inside the jumpbox (no Docker there).
Two steps instead:

1. **Build + push the image** (from a normal dev machine — ACR stayed public):
   `scripts/build_and_push_agent.sh kb-agent`
2. **Register the agent version** (from inside the jumpbox — data-plane call to the
   private project): `scripts/register_hosted_agent.sh kb-agent '{"AZURE_AI_MODEL_DEPLOYMENT_NAME":"gpt-4.1","AZURE_SEARCH_ENDPOINT":"https://foundryiqv2pau4search.search.windows.net","AZURE_SEARCH_KNOWLEDGE_BASE_NAME":"aw-knowledge-base"}'`
   (Don't set `FOUNDRY_PROJECT_ENDPOINT` or any other `FOUNDRY_*`/`AGENT_*` var yourself
   — reserved, auto-injected by the platform.)
3. Grant the new agent's `AgentIdentity` (`instance_identity.principal_id` in the
   registration response) whatever RBAC it needs — Search roles for `kb-agent`/
   `orchestrator-agent`, `Foundry User` on the project for `orchestrator-agent`
   (needed to read the Fabric toolbox connection). None for `courier-agent`.
4. Test: `scripts/invoke_hosted_agent.sh kb-agent "What is Adventure Works' refund policy?"` (from inside the jumpbox)

## Verification

- `nslookup`/`getent hosts` each private-linked FQDN from the jumpbox resolves to a
  `192.168.1.x` address, not a public one.
- Public internet access to the Foundry account's endpoint fails with 403
  (`Public access is disabled. Please configure private endpoint.`).
- Each of the three agents answers correctly when invoked from the jumpbox — confirmed
  end-to-end: kb-agent (KB citation), courier-agent (live web search with citations),
  orchestrator-agent (routed to the Fabric toolbox, returned real data).
