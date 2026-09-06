# Copyright (c) Microsoft. All rights reserved.
"""One-off script: create the Foundry IQ Knowledge Base for the KB agent.

Tries the storage-backed (`azureBlob`-kind) knowledge source first, since
Storage starts out public in this environment (Phase 1). If Storage's public
access turns out to be governance-blocked in this tenant (as it was in the
prior POC, no matter how it was requested), fall back to the `File`-kind
knowledge source, which uploads the PDFs directly through Search's own data
plane and sidesteps the Storage account entirely.

Run with an identity that has Search Service Contributor + Search Index Data
Contributor on the search service (the bicep template already grants these to
the Foundry project's identity; grant them to your own user too if running
this script interactively).
"""

import os
import sys
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureBlobKnowledgeSource,
    AzureBlobKnowledgeSourceParameters,
    AzureOpenAIVectorizerParameters,
    FileKnowledgeSource,
    FileKnowledgeSourceParameters,
    KnowledgeBase,
    KnowledgeBaseAzureOpenAIModel,
    KnowledgeSourceReference,
)
from azure.search.documents.knowledgebases.models import (
    KnowledgeRetrievalMinimalReasoningEffort,
    KnowledgeSourceAzureOpenAIVectorizer,
    KnowledgeSourceIngestionParameters,
)
from dotenv import load_dotenv

load_dotenv()

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
FOUNDRY_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
CHAT_MODEL_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1")
EMBEDDING_MODEL_DEPLOYMENT = os.environ.get("AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME", "text-embedding-3-large")
KNOWLEDGE_SOURCE_NAME = os.environ.get("AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME", "aw-docs-source")
KNOWLEDGE_BASE_NAME = os.environ.get("AZURE_SEARCH_KNOWLEDGE_BASE_NAME", "aw-knowledge-base")

STORAGE_ACCOUNT_RESOURCE_ID = os.environ.get("AZURE_STORAGE_ACCOUNT_RESOURCE_ID", "")
STORAGE_CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "aw-docs")

PDF_DIR = Path(os.environ["AW_PDF_DIR"])
PDF_FILES = [
    "Adventure Works Inc. – Retail Customer Payment, Purchase, Shipping & Refund Information.pdf",
    "Adventure Works Inc. – Retail Customer Terms and Conditions.pdf",
    "Adventure Works Inc Retail Customer Support Representative Guide.pdf",
]


def _embedding_ingestion_params() -> KnowledgeSourceIngestionParameters:
    return KnowledgeSourceIngestionParameters(
        embedding_model=KnowledgeSourceAzureOpenAIVectorizer(
            azure_open_ai_parameters=AzureOpenAIVectorizerParameters(
                resource_url=FOUNDRY_OPENAI_ENDPOINT,
                deployment_name=EMBEDDING_MODEL_DEPLOYMENT,
                model_name=EMBEDDING_MODEL_DEPLOYMENT,
            )
        ),
    )


def create_blob_backed_source(client: SearchIndexClient) -> None:
    knowledge_source = AzureBlobKnowledgeSource(
        name=KNOWLEDGE_SOURCE_NAME,
        description="Adventure Works retail customer-support PDFs (payment/purchase/shipping/refund, terms, support rep guide).",
        azure_blob_parameters=AzureBlobKnowledgeSourceParameters(
            connection_string=f"ResourceId={STORAGE_ACCOUNT_RESOURCE_ID};",
            container_name=STORAGE_CONTAINER_NAME,
            ingestion_parameters=_embedding_ingestion_params(),
        ),
    )
    client.create_or_update_knowledge_source(knowledge_source)
    print(f"[blob-backed] Knowledge source '{KNOWLEDGE_SOURCE_NAME}' created/updated.")


def create_file_backed_source(client: SearchIndexClient) -> None:
    knowledge_source = FileKnowledgeSource(
        name=KNOWLEDGE_SOURCE_NAME,
        description="Adventure Works retail customer-support PDFs (payment/purchase/shipping/refund, terms, support rep guide).",
        file_parameters=FileKnowledgeSourceParameters(
            ingestion_parameters=_embedding_ingestion_params(),
        ),
    )
    client.create_or_update_knowledge_source(knowledge_source)
    print(f"[file-backed] Knowledge source '{KNOWLEDGE_SOURCE_NAME}' created/updated.")

    for filename in PDF_FILES:
        path = PDF_DIR / filename
        safe_filename = filename.replace("–", "-")
        with open(path, "rb") as f:
            result = client.upload_knowledge_source_file(KNOWLEDGE_SOURCE_NAME, f, filename=safe_filename)
        print(f"Uploaded '{safe_filename}' -> file_id={result.file_id}")


def main() -> None:
    credential = DefaultAzureCredential()
    client = SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=credential)

    mode = sys.argv[1] if len(sys.argv) > 1 else "blob"
    if mode == "blob":
        if not STORAGE_ACCOUNT_RESOURCE_ID:
            raise SystemExit("AZURE_STORAGE_ACCOUNT_RESOURCE_ID is required for blob mode. Set it or pass 'file' as the mode.")
        create_blob_backed_source(client)
    elif mode == "file":
        create_file_backed_source(client)
    else:
        raise SystemExit(f"Unknown mode '{mode}'. Use 'blob' or 'file'.")

    knowledge_base = KnowledgeBase(
        name=KNOWLEDGE_BASE_NAME,
        description="Adventure Works retail customer-support knowledge base.",
        knowledge_sources=[KnowledgeSourceReference(name=KNOWLEDGE_SOURCE_NAME)],
        models=[
            KnowledgeBaseAzureOpenAIModel(
                azure_open_ai_parameters=AzureOpenAIVectorizerParameters(
                    resource_url=FOUNDRY_OPENAI_ENDPOINT,
                    deployment_name=CHAT_MODEL_DEPLOYMENT,
                    model_name=CHAT_MODEL_DEPLOYMENT,
                )
            )
        ],
        output_mode="extractiveData",
        retrieval_reasoning_effort=KnowledgeRetrievalMinimalReasoningEffort(),
    )
    client.create_or_update_knowledge_base(knowledge_base)
    print(f"Knowledge base '{KNOWLEDGE_BASE_NAME}' created/updated.")


if __name__ == "__main__":
    main()
