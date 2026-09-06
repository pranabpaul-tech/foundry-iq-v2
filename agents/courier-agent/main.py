# Copyright (c) Microsoft. All rights reserved.
"""Courier-services agent — live web-search grounded.

Uses FoundryChatClient's built-in `get_web_search_tool()`, which returns the
native SDK `WebSearchTool` (type "web_search"). Unlike the Bing Grounding or
Bing Custom Search tools, this needs no project connection or toolbox setup
at all — verified directly against the installed
`azure-ai-projects`/`agent-framework` packages (see
agent_framework.foundry.FoundryChatClient.get_web_search_tool).

Default scope below is a general courier/parcel-delivery assistant. Refine
AGENT_INSTRUCTIONS once specific courier companies/tone are provided.
"""

import asyncio
import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

AGENT_INSTRUCTIONS = """\
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


async def main() -> None:
    credential = DefaultAzureCredential()

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential,
    )

    web_search_tool = client.get_web_search_tool(search_context_size="medium")

    agent = Agent(
        client=client,
        instructions=AGENT_INSTRUCTIONS,
        tools=[web_search_tool],
        default_options={"store": False},
    )
    server = ResponsesHostServer(agent)
    await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
