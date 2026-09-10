# Copyright (c) Microsoft. All rights reserved.
"""Load the pre-built Delta tables in data/aw-sales/adventureworks_sample.zip
into the Fabric Lakehouse, then configure and publish the Data Agent to
query them.

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
    AW_SALES_ZIP            default data/aw-sales/adventureworks_sample.zip (repo-relative)

What it does, in order:
1. Resolves the workspace/lakehouse/data-agent IDs by display name (Fabric
   items have no stable ARM-style names to hardcode -- see
   scripts/provision_fabric_workspace.sh, which created them).
2. Extracts AW_SALES_ZIP. Each top-level directory in it (adventureworks_
   customers, _orders, _products, etc.) is already a complete, valid Delta
   table -- a Parquet data file plus a _delta_log/*.json transaction log --
   not raw CSVs needing conversion. Uploads every file in each, unmodified,
   to the Lakehouse's Tables/<dirname>/ path via the OneLake DFS (ADLS
   Gen2-compatible) API: create, append, flush. Fabric auto-discovers any
   valid Delta table folder placed directly under Tables/ -- no separate
   "load table" API call needed (contrast with a raw CSV, which does need
   one; see this file's git history for that version, from before this
   richer prebuilt dataset replaced the earlier synthetic single-table CSV).
3. Removes any older Tables/sales_orders and Files/sales_orders.csv left
   over from that earlier CSV-based version, if present.
4. Builds a Data Agent definition (see "Data Agent item definition" at
   https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/data-agent-definition)
   wiring all the Lakehouse's tables in as a `lakehouse_tables` data source
   -- elements nested table/column under a "dbo" schema element; a flat
   table-level list with no schema wrapper validates and publishes fine but
   makes the Data Agent fail at query time, confirmed by calling the MCP
   tool directly -- with AI instructions describing the tables' foreign-key
   relationships and a few join-based few-shot examples, and pushes it via
   updateDefinition (an LRO).
5. Publishes the Data Agent (promotes the draft config to the live one that
   Fabric MCP -- and so scripts/create_fabric_toolbox.sh -- actually serves).

Idempotent: re-running overwrites the same-named tables and replaces the
whole Data Agent definition/publish state.
"""

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

WORKSPACE_NAME = os.environ.get("FABRIC_WORKSPACE_NAME", "foundryiq-workspace")
LAKEHOUSE_NAME = os.environ.get("FABRIC_LAKEHOUSE_NAME", "aw_docs_lakehouse")
DATA_AGENT_NAME = os.environ.get("FABRIC_DATA_AGENT_NAME", "aw_sales_data_agent")
ZIP_PATH = Path(os.environ.get(
    "AW_SALES_ZIP",
    Path(__file__).resolve().parent.parent / "data" / "aw-sales" / "adventureworks_sample.zip",
))

FABRIC_API = "https://api.fabric.microsoft.com/v1"

# Real AdventureWorks schema for each pre-built Delta table, used only to
# describe them in the Data Agent's datasource elements (a parquet-reading
# library isn't available on the jumpbox, and isn't needed -- the table
# files themselves are uploaded and used as-is).
TABLE_COLUMNS = {
    "adventureworks_customers": ["CustomerID_K", "FirstName", "LastName", "FullName"],
    "adventureworks_employees": ["EmployeeID_K", "ManagerID", "EmployeeFullName", "JobTitle",
                                  "OrganizationLevel", "MaritalStatus", "Gender", "Territory",
                                  "Country", "Group"],
    "adventureworks_orders": ["SalesOrderDetailID_K", "OrderDate", "DueDate", "ShipDate",
                               "EmployeeID_FK", "CustomerID_FK", "SubTotal", "TaxAmt", "Freight",
                               "TotalDue", "ProductID_FK", "OrderQty", "UnitPrice",
                               "UnitPriceDiscount", "LineTotal", "SalesOrderID"],
    "adventureworks_productcategories": ["CategoryID_K", "CategoryName"],
    "adventureworks_products": ["ProductID_K", "ProductNumber", "ProductName", "ModelName",
                                 "MakeFlag", "StandardCost", "ListPrice", "SubCategoryID_FK"],
    "adventureworks_productsubcategories": ["SubCategoryID_K", "CategoryID_FK", "SubCategoryName"],
    "adventureworks_vendorproduct": ["ProductID_FK", "VendorID_FK"],
    "adventureworks_vendors": ["VendorID_K", "VendorName", "AccountNumber", "CreditRating",
                                "ActiveFlag"],
}


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


def onelake_upload(workspace_id: str, lakehouse_id: str, relative_path: str, data: bytes, storage_token: str):
    base = f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/{relative_path}"
    status, body, _ = http("PUT", base + "?resource=file", storage_token)
    if status >= 300:
        raise RuntimeError(f"OneLake create failed for {relative_path}: {status} {body!r}")
    status, body, _ = http("PATCH", base + "?action=append&position=0", storage_token, body=data,
                            content_type="application/octet-stream")
    if status >= 300:
        raise RuntimeError(f"OneLake append failed for {relative_path}: {status} {body!r}")
    status, body, _ = http("PATCH", base + f"?action=flush&position={len(data)}", storage_token)
    if status >= 300:
        raise RuntimeError(f"OneLake flush failed for {relative_path}: {status} {body!r}")


def onelake_delete(workspace_id: str, lakehouse_id: str, relative_path: str, storage_token: str):
    base = f"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/{relative_path}"
    http("DELETE", base + "?recursive=true", storage_token)  # best-effort, ignore result


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

    print("==> Removing any leftover sales_orders table/file from an earlier CSV-based run")
    onelake_delete(workspace_id, lakehouse_id, "Tables/sales_orders", storage_token)
    onelake_delete(workspace_id, lakehouse_id, "Files/sales_orders.csv", storage_token)

    print(f"==> Extracting {ZIP_PATH.name}")
    extract_dir = Path("/tmp/aw_sales_extract") if os.name != "nt" else Path(os.environ.get("TEMP", ".")) / "aw_sales_extract"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH) as zf:
        zf.extractall(extract_dir)

    table_dirs = sorted(p for p in extract_dir.iterdir() if p.is_dir())
    if not table_dirs:
        raise SystemExit(f"No table directories found in {ZIP_PATH}")

    table_names = []
    for table_dir in table_dirs:
        table_name = table_dir.name
        table_names.append(table_name)
        files = [p for p in table_dir.rglob("*") if p.is_file()]
        print(f"==> Uploading {table_name} ({len(files)} files) to Tables/{table_name}/")
        for f in files:
            rel = f.relative_to(table_dir).as_posix()
            onelake_upload(workspace_id, lakehouse_id, f"Tables/{table_name}/{rel}", f.read_bytes(), storage_token)

    print("==> Configuring Data Agent definition")
    ds_key = f"lakehouse-{LAKEHOUSE_NAME}"

    def table_element(i: int, table_name: str) -> dict:
        columns = TABLE_COLUMNS.get(table_name, [])
        return {
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

    elements = [
        {
            "id": "00000000-0000-0000-0000-000000000000",
            "is_selected": True,
            "display_name": "dbo",
            "type": "lakehouse_tables.schema",
            "children": [table_element(i, name) for i, name in enumerate(table_names, start=1)],
        }
    ]

    ai_instructions = (
        "You are Adventure Works' sales data assistant, working with a relational schema: "
        "adventureworks_orders (one row per order line item) references "
        "adventureworks_customers via CustomerID_FK = CustomerID_K, "
        "adventureworks_employees via EmployeeID_FK = EmployeeID_K, and "
        "adventureworks_products via ProductID_FK = ProductID_K. "
        "adventureworks_products references adventureworks_productsubcategories via "
        "SubCategoryID_FK = SubCategoryID_K, which references adventureworks_productcategories "
        "via CategoryID_FK = CategoryID_K. adventureworks_vendorproduct is a many-to-many link "
        "between adventureworks_products (ProductID_FK) and adventureworks_vendors "
        "(VendorID_FK = VendorID_K). Use LineTotal/TotalDue for revenue questions, join in the "
        "relevant dimension tables rather than guessing values, and format currency amounts with "
        "a dollar sign and two decimal places."
    )
    few_shots = {
        "$schema": "1.0.0",
        "fewShots": [
            {
                "id": "10000000-0000-0000-0000-000000000001",
                "question": "What were the total sales by product category?",
                "query": (
                    "SELECT pc.CategoryName, SUM(o.LineTotal) AS TotalSales "
                    "FROM adventureworks_orders o "
                    "JOIN adventureworks_products p ON o.ProductID_FK = p.ProductID_K "
                    "JOIN adventureworks_productsubcategories ps ON p.SubCategoryID_FK = ps.SubCategoryID_K "
                    "JOIN adventureworks_productcategories pc ON ps.CategoryID_FK = pc.CategoryID_K "
                    "GROUP BY pc.CategoryName"
                ),
            },
            {
                "id": "10000000-0000-0000-0000-000000000002",
                "question": "Who are the top 5 customers by total spend?",
                "query": (
                    "SELECT c.FullName, SUM(o.LineTotal) AS TotalSpend "
                    "FROM adventureworks_orders o "
                    "JOIN adventureworks_customers c ON o.CustomerID_FK = c.CustomerID_K "
                    "GROUP BY c.FullName ORDER BY TotalSpend DESC LIMIT 5"
                ),
            },
            {
                "id": "10000000-0000-0000-0000-000000000003",
                "question": "Which vendors supply the most products?",
                "query": (
                    "SELECT v.VendorName, COUNT(*) AS ProductCount "
                    "FROM adventureworks_vendorproduct vp "
                    "JOIN adventureworks_vendors v ON vp.VendorID_FK = v.VendorID_K "
                    "GROUP BY v.VendorName ORDER BY ProductCount DESC"
                ),
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
            "userDescription": "Adventure Works sales data (orders, customers, employees, products, categories, vendors)",
            "dataSourceInstructions": "Use these tables for all sales, order, customer, product, and vendor questions.",
            "elements": elements,
        }),
        part(f"Files/Config/draft/{ds_key}/fewshots.json", few_shots),
        part("Files/Config/draft/stage_config.json", {"$schema": "1.0.0", "aiInstructions": ai_instructions}),
    ]

    fabric_call_lro(
        "POST", f"/workspaces/{workspace_id}/dataAgents/{data_agent_id}/updateDefinition",
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
    print(f"Tables loaded: {', '.join(table_names)}")
    print("Next: scripts/create_fabric_toolbox.sh "
          f"{workspace_id} {data_agent_id}")


if __name__ == "__main__":
    main()
