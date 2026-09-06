# Copyright (c) Microsoft. All rights reserved.
"""Create a Foundry toolbox containing the Fabric IQ tool, pointed directly
at our Fabric data agent's own MCP endpoint (not the older
fabric_dataagent_preview tool). The toolbox's connection uses UserEntraToken
auth so the toolbox forwards the *calling user's* identity to Fabric,
enabling OBO -- and per Microsoft's docs, this specific "data agent" MCP
endpoint also accepts service-principal tokens directly, unlike the older
tool.
"""

import os

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FabricIQPreviewTool
from azure.identity import AzureCliCredential
from dotenv import load_dotenv

load_dotenv()

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
CONNECTION_NAME = "fabric-dataagent-obo"
SERVER_URL = (
    "https://api.fabric.microsoft.com/v1/mcp/workspaces/"
    f"{os.environ['FABRIC_WORKSPACE_ID']}/dataagents/"
    f"{os.environ['FABRIC_DATA_AGENT_ID']}/agent"
)
TOOLBOX_NAME = "fabric-iq-toolbox"


def main() -> None:
    credential = AzureCliCredential()
    project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)

    connection = project.connections.get(CONNECTION_NAME)

    fabric_iq_tool = FabricIQPreviewTool(
        project_connection_id=connection.id,
        server_label="fabric-dataagent",
        server_url=SERVER_URL,
        require_approval="never",
    )

    toolbox = project.beta.toolboxes.create_version(
        name=TOOLBOX_NAME,
        description="Toolbox with the Fabric data agent (Fabric IQ) tool, OBO-enabled.",
        tools=[fabric_iq_tool],
    )
    print(f"Created toolbox: {toolbox.name}, version: {toolbox.version}")


if __name__ == "__main__":
    main()
