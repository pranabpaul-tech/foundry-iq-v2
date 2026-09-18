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
import json
import os
import struct
import urllib.request
from typing import Annotated

import pyodbc
from agent_framework import Agent, tool
from agent_framework.azure import AzureAISearchContextProvider
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import FoundryToolbox, ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()

# ---------- lakehouse_agent's SQL tool (duplicated from agents/lakehouse-agent/main.py --
# each hosted agent's container is self-contained, same pattern KB_AGENT_INSTRUCTIONS/
# COURIER_AGENT_INSTRUCTIONS below already follow for kb_agent/courier_agent) ----------

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_WORKSPACE_NAME = os.environ.get("FABRIC_WORKSPACE_NAME", "foundryiq-workspace")
FABRIC_LAKEHOUSE_NAME = os.environ.get("FABRIC_LAKEHOUSE_NAME", "aw_docs_lakehouse")
SQL_COPT_SS_ACCESS_TOKEN = 1256

_sql_endpoint: tuple[str, str] | None = None


def _fabric_get(path: str, token: str) -> dict:
    req = urllib.request.Request(f"{FABRIC_API}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _resolve_sql_endpoint(credential: DefaultAzureCredential) -> tuple[str, str]:
    global _sql_endpoint
    if _sql_endpoint is not None:
        return _sql_endpoint

    token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    workspaces = _fabric_get("/workspaces", token)
    workspace_id = next(w["id"] for w in workspaces["value"] if w["displayName"] == FABRIC_WORKSPACE_NAME)

    items = _fabric_get(f"/workspaces/{workspace_id}/items?type=Lakehouse", token)
    lakehouse_id = next(i["id"] for i in items["value"] if i["displayName"] == FABRIC_LAKEHOUSE_NAME)

    lakehouse = _fabric_get(f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}", token)
    server = lakehouse["properties"]["sqlEndpointProperties"]["connectionString"]

    _sql_endpoint = (server, FABRIC_LAKEHOUSE_NAME)
    return _sql_endpoint


def _run_lakehouse_query(sql: str, credential: DefaultAzureCredential) -> str:
    server, database = _resolve_sql_endpoint(credential)

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

LAKEHOUSE_AGENT_INSTRUCTIONS = """\
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

ORCHESTRATOR_INSTRUCTIONS = """\
You are the top-level assistant for Adventure Works. You have specialist
tools available and must route each question to the right one(s):

- `kb_agent`: Adventure Works policies -- payment, purchasing, shipping,
  refunds, and terms & conditions.
- `courier_agent`: FedEx/UPS/DHL carrier questions -- tracking, transit
  times, rates, claims for lost/damaged packages.
- Fabric data agent tool (from the connected toolbox): structured
  enterprise data questions (e.g. sales, customers, orders) -- the default
  choice for this kind of question.
- `lakehouse_agent`: the SAME underlying sales/customer/product/vendor data,
  but queried by writing and running T-SQL directly against the Fabric
  Lakehouse's SQL analytics endpoint, bypassing the Fabric data agent
  entirely. Only use this tool when the user explicitly asks to query the
  lakehouse directly, run raw SQL, or bypass/skip the Fabric data agent --
  never as your default for a structured data question.

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
        to 200 result rows as JSON."""
        stripped = sql.strip()
        if stripped[:6].upper() != "SELECT":
            return "Error: only a single SELECT statement is allowed."
        return await asyncio.to_thread(_run_lakehouse_query, stripped, credential)

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

        lakehouse_agent = Agent(
            client=client,
            name="lakehouse_agent",
            description="Direct T-SQL access to the Adventure Works Fabric Lakehouse, bypassing the Fabric data agent.",
            instructions=LAKEHOUSE_AGENT_INSTRUCTIONS,
            tools=[query_lakehouse],
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
                lakehouse_agent.as_tool(
                    name="lakehouse_agent",
                    description="Query Adventure Works sales/customer/product/vendor data by running SQL directly against the Fabric Lakehouse, bypassing the Fabric data agent. Only use when explicitly asked to query the lakehouse directly or bypass the Fabric data agent.",
                    arg_description="The structured data question to answer by querying the lakehouse directly.",
                ),
            ],
            default_options={"store": False},
        )
        server = ResponsesHostServer(orchestrator)
        await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
