# Copyright (c) Microsoft. All rights reserved.
"""Load data/aw-sales/*.csv into the Fabric Lakehouse as Delta tables, then
configure and publish the Data Agent to query them.

Run from inside the jumpbox (infra/04-jumpbox.bicep), after `az login` there
-- both the OneLake DFS API and the Fabric REST API are reachable from
outside the VNet (Fabric isn't behind a private endpoint the way Search/
Storage/ACR are), but this still needs the jumpbox's signed-in identity and
its az CLI for token acquisition.

Usage:
    python3 scripts/provision_fabric_sales_agent.py

Env vars (all optional, defaults match this project):
    FABRIC_WORKSPACE_NAME   default "foundryiq-workspace"
    FABRIC_LAKEHOUSE_NAME   default "aw_docs_lakehouse"
    FABRIC_DATA_AGENT_NAME  default "aw_sales_data_agent"
    AW_SALES_CSV_DIR        default data/aw-sales/ (repo-relative)

What it does, in order:
1. Resolves the workspace/lakehouse/data-agent IDs by display name (Fabric
   items have no stable ARM-style names to hardcode -- see
   scripts/provision_fabric_workspace.sh, which created them).
2. Uploads each CSV in AW_SALES_CSV_DIR to the Lakehouse's Files section via
   the OneLake DFS (ADLS Gen2-compatible) API: create, append, flush.
3. Loads each uploaded CSV into a same-named Delta table via the Lakehouse
   "Load Table" API (a Fabric long-running operation -- polled to
   completion).
4. Builds a Data Agent definition (see "Data Agent item definition" at
   https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition)
   wiring the Lakehouse's tables in as a `lakehouse_tables` data source, with
   AI instructions and a couple of few-shot examples, and pushes it via
   updateDefinition (also an LRO).
5. Publishes the Data Agent (promotes the draft config to the live one that
   Fabric MCP -- and so scripts/create_fabric_toolbox.sh -- actually serves).

Idempotent: re-running overwrites the same-named tables (load mode
"Overwrite") and replaces the whole Data Agent definition/publish state.
"""

import base64
import csv
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE_NAME = "foundryiq-workspace"
LAKEHOUSE_NAME = "aw_docs_lakehouse"
DATA_AGENT_NAME = "aw_sales_data_agent"

import os

WORKSPACE_NAME = os.environ.get("FABRIC_WORKSPACE_NAME", WORKSPACE_NAME)
LAKEHOUSE_NAME = os.environ.get("FABRIC_LAKEHOUSE_NAME", LAKEHOUSE_NAME)
DATA_AGENT_NAME = os.environ.get("FABRIC_DATA_AGENT_NAME", DATA_AGENT_NAME)
CSV_DIR = Path(os.environ.get("AW_SALES_CSV_DIR", Path(__file__).resolve().parent.parent / "data" / "aw-sales"))

FABRIC_API = "https://api.fabric.microsoft.com/v1"


def az_token(resource: str) -> str:
    out = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def http(method: str, url: str, token: str, body: bytes | None = None, json_body: dict | None = None,
         content_type: str = "application/json") -> tuple[int, bytes, dict]:
    data = body
    if json_body is not None:
        data = json.dumps(json_body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def fabric_get(path: str, token: str):
    status, body, _ = http("GET", f"{FABRIC_API}{path}", token)
    if status >= 300:
        raise RuntimeError(f"GET {path} -> {status}: {body!r}")
    return json.loads(body) if body else None


def fabric_call_lro(method: str, path: str, token: str, json_body: dict | None = None):
    """POST/PATCH that may return 200/202 (LRO) or 200 with a direct body."""
    status, body, headers = http(method, f"{FABRIC_API}{path}", token, json_body=json_body)
    if status == 202:
        op_url = headers.get("Location")
        while True:
            time.sleep(int(headers.get("Retry-After", "5")))
            status, body, headers = http("GET", op_url, token)
            state = json.loads(body)
            if state.get("status") in ("Succeeded", "Failed"):
                if state["status"] == "Failed":
                    raise RuntimeError(f"LRO {path} failed: {state}")
                # fetch result if present
                status, body, _ = http("GET", f"{op_url}/result", token)
                return json.loads(body) if body else None
    elif status >= 300:
        raise RuntimeError(f"{method} {path} -> {status}: {body!r}")
    return json.loads(body) if body else None


def find_item(workspace_id: str, item_type: str, display_name: str, token: str) -> str:
    items = fabric_get(f"/workspaces/{workspace_id}/items?type={item_type}", token)
    for item in items.get("value", []):
        if item["displayName"] == display_name:
            return item["id"]
    raise RuntimeError(f"{item_type} '{display_name}' not found in workspace {workspace_id}")


def part(path: str, obj: dict) -> dict:
    payload = base64.b64encode(json.dumps(obj, indent=2).encode()).decode()
    return {"path": path, "payload": payload, "payloadType": "InlineBase64"}


def main():
    fabric_token = az_token("https://api.fabric.microsoft.com")
    storage_token = az_token("https://storage.azure.com/")

    workspaces = fabric_get("/workspaces", fabric_token)
    workspace_id = next(w["id"] for w in workspaces["value"] if w["displayName"] == WORKSPACE_NAME)
    print(f"Workspace {WORKSPACE_NAME}: {workspace_id}")

    lakehouse_id = find_item(workspace_id, "Lakehouse", LAKEHOUSE_NAME, fabric_token)
    print(f"Lakehouse {LAKEHOUSE_NAME}: {lakehouse_id}")
    data_agent_id = find_item(workspace_id, "DataAgent", DATA_AGENT_NAME, fabric_token)
    print(f"Data Agent {DATA_AGENT_NAME}: {data_agent_id}")

    csv_files = sorted(CSV_DIR.glob("*.csv"))
    if not csv_files:
        raise SystemExit(f"No CSVs found in {CSV_DIR}")

    tables = []  # (table_name, [column names])
    for csv_path in csv_files:
        table_name = csv_path.stem
        csv_bytes = csv_path.read_bytes()
        with open(csv_path, newline="") as f:
            columns = next(csv.reader(f))

        print(f"==> Uploading {csv_path.name} ({len(csv_bytes)} bytes) to Lakehouse Files")
        onelake_base = f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/Files/{csv_path.name}"
        status, body, _ = http("PUT", onelake_base + "?resource=file", storage_token)
        if status >= 300:
            raise RuntimeError(f"OneLake create failed: {status} {body!r}")
        status, body, _ = http("PATCH", onelake_base + "?action=append&position=0", storage_token, body=csv_bytes,
                                content_type="application/octet-stream")
        if status >= 300:
            raise RuntimeError(f"OneLake append failed: {status} {body!r}")
        status, body, _ = http("PATCH", onelake_base + f"?action=flush&position={len(csv_bytes)}", storage_token)
        if status >= 300:
            raise RuntimeError(f"OneLake flush failed: {status} {body!r}")

        print(f"==> Loading table '{table_name}'")
        fabric_call_lro(
            "POST", f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table_name}/load", fabric_token,
            json_body={
                "relativePath": f"Files/{csv_path.name}",
                "pathType": "File",
                "mode": "Overwrite",
                "formatOptions": {"format": "Csv", "header": True, "delimiter": ","},
            },
        )
        tables.append((table_name, columns))

    print("==> Configuring Data Agent definition")
    ds_key = f"lakehouse-{LAKEHOUSE_NAME}"
    elements = [
        {
            "id": f"00000000-0000-0000-0000-{i:012d}",
            "is_selected": True,
            "display_name": table_name,
            "type": "lakehouse_tables.table",
            "children": [
                {
                    "id": f"00000000-0000-0000-0000-{i:06d}{j:06d}",
                    "is_selected": True,
                    "display_name": col,
                    "type": "lakehouse_tables.column",
                }
                for j, col in enumerate(columns)
            ],
        }
        for i, (table_name, columns) in enumerate(tables, start=1)
    ]

    ai_instructions = (
        "You are Adventure Works' sales data assistant. Answer questions about orders, "
        "products, customers, revenue, and order status using the sales_orders table. "
        "Always aggregate/filter with SQL against the table rather than guessing, and "
        "format currency amounts with a dollar sign and two decimal places."
    )
    few_shots = {
        "$schema": "1.0.0",
        "fewShots": [
            {
                "id": "10000000-0000-0000-0000-000000000001",
                "question": "What were the total sales for the Bikes category?",
                "query": "SELECT SUM(TotalAmount) FROM sales_orders WHERE Category = 'Bikes'",
            },
            {
                "id": "10000000-0000-0000-0000-000000000002",
                "question": "Show me the top 5 orders by total amount",
                "query": "SELECT TOP 5 OrderID, CustomerName, Product, TotalAmount FROM sales_orders ORDER BY TotalAmount DESC",
            },
        ],
    }

    definition_parts = [
        part("Files/Config/data_agent.json", {"$schema": "2.1.0"}),
        part(f"Files/Config/draft/{ds_key}/datasource.json", {
            "$schema": "1.0.0",
            "artifactId": lakehouse_id,
            "workspaceId": workspace_id,
            "displayName": LAKEHOUSE_NAME,
            "type": "lakehouse_tables",
            "userDescription": "Adventure Works sales order data",
            "dataSourceInstructions": "Use the sales_orders table for all sales/order/revenue questions.",
            "elements": elements,
        }),
        part(f"Files/Config/draft/{ds_key}/fewshots.json", few_shots),
        part("Files/Config/draft/stage_config.json", {"$schema": "1.0.0", "aiInstructions": ai_instructions}),
    ]

    fabric_call_lro(
        "POST", f"/workspaces/{workspace_id}/dataAgents/{data_agent_id}/updateDefinition?updateMetadata=True",
        fabric_token, json_body={"definition": {"parts": definition_parts}},
    )

    print("==> Publishing Data Agent")
    status, body, _ = http(
        "POST", f"{FABRIC_API}/workspaces/{workspace_id}/dataAgents/{data_agent_id}/staging/publish", fabric_token,
        json_body={"publishedDescription": "Adventure Works sales data agent"},
    )
    if status >= 300:
        raise RuntimeError(f"Publish failed: {status} {body!r}")

    print("")
    print(f"Done. Workspace ID: {workspace_id}")
    print(f"Data Agent ID: {data_agent_id}")
    print("Next: scripts/create_fabric_toolbox.sh "
          f"{workspace_id} {data_agent_id}")


if __name__ == "__main__":
    main()
