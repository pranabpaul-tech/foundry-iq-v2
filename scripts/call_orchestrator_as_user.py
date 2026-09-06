# Copyright (c) Microsoft. All rights reserved.
"""Call orchestrator-agent's Responses endpoint as a real signed-in user.

This must be called with a real user's Entra token (not `azd ai agent
invoke`, which authenticates as the agent's own service identity) for the
Fabric tool to work: Fabric access goes through a toolbox whose connection
uses UserEntraToken auth, which requires On-Behalf-Of a real user.
"""

import os
import sys

import requests
from azure.identity import AzureCliCredential
from dotenv import load_dotenv

load_dotenv()

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
AGENT_NAME = "orchestrator-agent"


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "Tell me about sales records."

    credential = AzureCliCredential()
    token = credential.get_token("https://ai.azure.com/.default").token

    response = requests.post(
        f"{PROJECT_ENDPOINT}/agents/{AGENT_NAME}/endpoint/protocols/openai/responses?api-version=v1",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"input": question},
        timeout=90,
    )
    if not response.ok:
        print("HTTP", response.status_code, ":", response.text)
        return
    data = response.json()

    print("STATUS:", data.get("status"))
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = content["text"].encode("ascii", "replace").decode("ascii")
                    print("\nANSWER:\n", text)


if __name__ == "__main__":
    main()
