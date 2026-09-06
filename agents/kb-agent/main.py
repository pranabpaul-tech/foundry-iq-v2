# Copyright (c) Microsoft. All rights reserved.
"""Foundry IQ Knowledge Base agent.

Grounded in the Adventure Works retail-support Knowledge Base (Azure AI
Search) via AzureAISearchContextProvider in agentic mode. Reuses the proven
pattern from the Foundry IQ POC.
"""

import asyncio
import os

from agent_framework import Agent
from agent_framework.azure import AzureAISearchContextProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

AGENT_INSTRUCTIONS = """\
You are a customer support assistant for Adventure Works Inc.

Answer questions about payment, purchasing, shipping, refunds, and terms &
conditions using only the connected knowledge base. Always cite which
document an answer came from. If a question is not answered by these
documents, say so plainly rather than guessing or using outside knowledge.
"""


async def main() -> None:
    credential = DefaultAzureCredential()

    search_provider = AzureAISearchContextProvider(
        source_id="adventure_works_knowledge_base",
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        credential=credential,
        mode="agentic",
        knowledge_base_name=os.environ["AZURE_SEARCH_KNOWLEDGE_BASE_NAME"],
        knowledge_base_output_mode="extractive_data",
        retrieval_reasoning_effort="minimal",
    )

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential,
    )

    async with search_provider:
        agent = Agent(
            client=client,
            instructions=AGENT_INSTRUCTIONS,
            context_providers=[search_provider],
            default_options={"store": False},
        )
        server = ResponsesHostServer(agent)
        await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
