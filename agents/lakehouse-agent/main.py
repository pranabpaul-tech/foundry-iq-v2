# Copyright (c) Microsoft. All rights reserved.
"""Direct-Lakehouse agent — bypasses the Fabric Data Agent entirely.

Where orchestrator-agent's `fabric_toolbox` tool delegates natural language
to the Fabric Data Agent (which does its own NL2SQL against the lakehouse
tables), this agent writes and runs T-SQL itself, straight against the
Fabric Lakehouse's SQL analytics endpoint -- no Fabric Data Agent, no
Foundry toolbox/connection, no OBO. It authenticates as this container's
own AgentIdentity (DefaultAzureCredential) against both the Fabric REST API
(to resolve the SQL analytics endpoint's server hostname by workspace/
lakehouse display name, the same lookup-by-name pattern
scripts/provision_fabric_sales_agent.py uses) and the SQL endpoint itself
(AAD access-token auth via pyodbc's SQL_COPT_SS_ACCESS_TOKEN attribute --
the standard way to authenticate to a Fabric/Synapse SQL endpoint with an
Entra token). Requires the AgentIdentity to hold at least the Viewer role
on the Fabric workspace (granted in scripts/jumpbox_setup.sh).

Registered as its own hosted agent (so it's independently invokable) and
also wrapped as an in-process tool under orchestrator-agent, so a user can
explicitly ask for the lakehouse to be queried directly instead of going
through the Fabric data agent.
"""

import asyncio
import json
import os
import struct
import urllib.request
from typing import Annotated

import pyodbc
from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

FABRIC_API = "https://api.fabric.microsoft.com/v1"
WORKSPACE_NAME = os.environ.get("FABRIC_WORKSPACE_NAME", "foundryiq-workspace")
LAKEHOUSE_NAME = os.environ.get("FABRIC_LAKEHOUSE_NAME", "aw_docs_lakehouse")

# Standard pyodbc attribute id for passing an AAD access token in place of a
# username/password -- see Microsoft's docs on connecting to Synapse/Fabric
# SQL endpoints with an Entra token via pyodbc.
SQL_COPT_SS_ACCESS_TOKEN = 1256

AGENT_INSTRUCTIONS = """\
You are Adventure Works' direct-lakehouse data assistant. You answer
structured sales, customer, product, and vendor questions by writing and
running your own T-SQL SELECT statement directly against the Adventure
Works Fabric Lakehouse -- there is no natural-language delegation to a
separate data agent here, you write the SQL yourself, one SELECT per call.

Schema (all tables under the `dbo` schema):
- dbo.adventureworks_orders (one row per order line item): SalesOrderDetailID_K,
  OrderDate, DueDate, ShipDate, EmployeeID_FK, CustomerID_FK, SubTotal, TaxAmt,
  Freight, TotalDue, ProductID_FK, OrderQty, UnitPrice, UnitPriceDiscount,
  LineTotal, SalesOrderID
- dbo.adventureworks_customers: CustomerID_K, FirstName, LastName, FullName
- dbo.adventureworks_employees: EmployeeID_K, ManagerID, EmployeeFullName,
  JobTitle, OrganizationLevel, MaritalStatus, Gender, Territory, Country, Group
- dbo.adventureworks_products: ProductID_K, ProductNumber, ProductName,
  ModelName, MakeFlag, StandardCost, ListPrice, SubCategoryID_FK
- dbo.adventureworks_productsubcategories: SubCategoryID_K, CategoryID_FK,
  SubCategoryName
- dbo.adventureworks_productcategories: CategoryID_K, CategoryName
- dbo.adventureworks_vendors: VendorID_K, VendorName, AccountNumber,
  CreditRating, ActiveFlag
- dbo.adventureworks_vendorproduct (many-to-many link): ProductID_FK, VendorID_FK

Joins: orders.CustomerID_FK = customers.CustomerID_K,
orders.EmployeeID_FK = employees.EmployeeID_K,
orders.ProductID_FK = products.ProductID_K,
products.SubCategoryID_FK = productsubcategories.SubCategoryID_K,
productsubcategories.CategoryID_FK = productcategories.CategoryID_K,
vendorproduct links products.ProductID_K to vendors.VendorID_K.

Use LineTotal/TotalDue for revenue questions, join in the relevant
dimension tables rather than guessing values, and format currency amounts
with a dollar sign and two decimal places. Only ever issue a single
read-only SELECT statement per call -- never attempt to modify data.
"""

credential = DefaultAzureCredential()
_sql_endpoint: tuple[str, str] | None = None


def _fabric_get(path: str, token: str) -> dict:
    req = urllib.request.Request(f"{FABRIC_API}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _resolve_sql_endpoint() -> tuple[str, str]:
    """Look up the Lakehouse's SQL analytics endpoint server by display name.

    Cached for the container's lifetime -- resolved lazily (not at import
    time) so a missing/not-yet-provisioned lakehouse only breaks the tool
    call, not agent startup.
    """
    global _sql_endpoint
    if _sql_endpoint is not None:
        return _sql_endpoint

    token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    workspaces = _fabric_get("/workspaces", token)
    workspace_id = next(w["id"] for w in workspaces["value"] if w["displayName"] == WORKSPACE_NAME)

    items = _fabric_get(f"/workspaces/{workspace_id}/items?type=Lakehouse", token)
    lakehouse_id = next(i["id"] for i in items["value"] if i["displayName"] == LAKEHOUSE_NAME)

    lakehouse = _fabric_get(f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}", token)
    server = lakehouse["properties"]["sqlEndpointProperties"]["connectionString"]

    _sql_endpoint = (server, LAKEHOUSE_NAME)
    return _sql_endpoint


def _run_query(sql: str) -> str:
    server, database = _resolve_sql_endpoint()

    sql_token = credential.get_token("https://database.windows.net/.default").token
    token_bytes = sql_token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={server},1433;Database={database};Encrypt=yes;TrustServerCertificate=no;"
    )
    with pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct}, timeout=30) as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchmany(200)]
    return json.dumps(rows, default=str)


@tool
async def query_lakehouse(
    sql: Annotated[
        str,
        "A single read-only T-SQL SELECT statement (tables under schema `dbo`) to run "
        "directly against the Adventure Works lakehouse.",
    ],
) -> str:
    """Run a T-SQL SELECT query directly against the Adventure Works Fabric Lakehouse's
    SQL analytics endpoint (bypassing the Fabric Data Agent's NL2SQL layer) and return up
    to 200 result rows as JSON. Use this only when explicitly asked to query the lakehouse
    directly instead of using the Fabric data agent."""
    stripped = sql.strip()
    if stripped[:6].upper() != "SELECT":
        return "Error: only a single SELECT statement is allowed."
    return await asyncio.to_thread(_run_query, stripped)


async def main() -> None:
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=credential,
    )

    agent = Agent(
        client=client,
        instructions=AGENT_INSTRUCTIONS,
        tools=[query_lakehouse],
        default_options={"store": False},
    )
    server = ResponsesHostServer(agent)
    await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
