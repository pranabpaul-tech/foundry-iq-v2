# Copyright (c) Microsoft. All rights reserved.
"""Top-level orchestrator agent (hosted, MAF container).

kb_agent/courier_agent are wrapped as in-process Agent objects and exposed
via Agent.as_tool() (no A2A -- see agents/kb-agent, agents/courier-agent;
Foundry-to-Foundry A2A is a confirmed platform bug, tasks/get always
returns TaskNotFound).

Fabric access goes through a Foundry Toolbox (FoundryToolbox from
agent_framework_foundry_hosting) pointed at the "fabric-iq-toolbox", which
wraps our Fabric data agent's own MCP endpoint via a project connection
using UserEntraToken auth. Unlike inline native tools (e.g.
MicrosoftFabricPreviewTool passed directly to FoundryChatClient, which
always authenticates as this container's own fixed AgentIdentity and can
never satisfy Fabric's OBO requirement), FoundryToolbox forwards the
platform's per-request call-id to Foundry's MCP proxy, which resolves the
real calling identity server-side -- so this DOES support OBO, provided
the caller of this agent's own endpoint authenticates with a real user
token (not a service-principal one, e.g. not `azd ai agent invoke`).
"""

import asyncio
import os

from agent_framework import Agent
from agent_framework.azure import AzureAISearchContextProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import FoundryToolbox, ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

KB_AGENT_INSTRUCTIONS = """\
You are a customer support assistant for Adventure Works Inc.

Answer questions about payment, purchasing, shipping, refunds, and terms &
conditions using only the connected knowledge base. Always cite which
document an answer came from. If a question is not answered by these
documents, say so plainly rather than guessing or using outside knowledge.
"""

COURIER_AGENT_INSTRUCTIONS = """\
You are a courier and parcel-delivery assistant covering exactly three
carriers: FedEx, UPS, and DHL. Use live web search to answer questions
about these carriers — things like service options, estimated transit
times, tracking a shipment, delivery areas, rates/quotes, and how to file
a claim for a lost or damaged package.

Always search the web for current information rather than relying on
memorized facts, since carrier services, rates, and policies change
frequently. Cite the source (carrier name/site) for any specific claim
about rates, timelines, or policies.

If a question is about a courier other than FedEx, UPS, or DHL, or falls
outside courier/shipping services entirely, say so plainly and decline
rather than guessing.

Keep answers concise and practical, in a helpful, professional tone.
"""

ORCHESTRATOR_INSTRUCTIONS = """\
You are the top-level assistant for Adventure Works. You have specialist
tools available and must route each question to the right one(s):

- `kb_agent`: Adventure Works policies -- payment, purchasing, shipping,
  refunds, and terms & conditions.
- `courier_agent`: FedEx/UPS/DHL carrier questions -- tracking, transit
  times, rates, claims for lost/damaged packages.
- Fabric data agent tool (from the connected toolbox): structured
  enterprise data questions (e.g. sales, customers, orders).

Decide which tool(s) apply based on the question. If a question spans
multiple domains, call all relevant tools and combine their answers into
one coherent response. If no tool is relevant, say so plainly rather than
guessing.
"""


async def main() -> None:
    credential = DefaultAzureCredential()

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential,
    )

    search_provider = AzureAISearchContextProvider(
        source_id="adventure_works_knowledge_base",
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        credential=credential,
        mode="agentic",
        knowledge_base_name=os.environ["AZURE_SEARCH_KNOWLEDGE_BASE_NAME"],
        knowledge_base_output_mode="extractive_data",
        retrieval_reasoning_effort="minimal",
    )

    toolbox_url = f"{os.environ['FOUNDRY_PROJECT_ENDPOINT'].rstrip('/')}/toolboxes/fabric-iq-toolbox/mcp?api-version=v1"
    fabric_toolbox = FoundryToolbox(credential, url=toolbox_url, name="fabric_dataagent")

    async with search_provider:
        kb_agent = Agent(
            client=client,
            name="kb_agent",
            description="Adventure Works policy knowledge base (payment, purchasing, shipping, refunds, terms & conditions).",
            instructions=KB_AGENT_INSTRUCTIONS,
            context_providers=[search_provider],
            default_options={"store": False},
        )

        courier_agent = Agent(
            client=client,
            name="courier_agent",
            description="FedEx/UPS/DHL courier assistant (tracking, rates, claims) via live web search.",
            instructions=COURIER_AGENT_INSTRUCTIONS,
            tools=[client.get_web_search_tool(search_context_size="medium")],
            default_options={"store": False},
        )

        orchestrator = Agent(
            client=client,
            instructions=ORCHESTRATOR_INSTRUCTIONS,
            tools=[
                kb_agent.as_tool(
                    name="kb_agent",
                    description="Answer questions about Adventure Works policies: payment, purchasing, shipping, refunds, terms & conditions.",
                    arg_description="The customer's question about Adventure Works policy.",
                ),
                courier_agent.as_tool(
                    name="courier_agent",
                    description="Answer questions about FedEx, UPS, or DHL: tracking, rates, transit times, claims.",
                    arg_description="The customer's question about a FedEx/UPS/DHL shipment.",
                ),
                fabric_toolbox,
            ],
            default_options={"store": False},
        )
        server = ResponsesHostServer(orchestrator)
        await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
