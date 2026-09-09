# Copyright (c) Microsoft. All rights reserved.
"""Chunk, embed, and index the PDFs in data/aw-docs/ into Azure AI Search,
then wrap that index in a Foundry Knowledge Source + Knowledge Base.

Usage:
    pip install -r scripts/requirements.txt
    az login
    python scripts/build_search_index.py

Requires AZURE_SEARCH_ENDPOINT and AZURE_OPENAI_ENDPOINT to be set (see
.env.example at the repo root) and reads PDFs from data/aw-docs/ -- add or
replace PDFs there to reindex different documents, no code changes needed.

What it does, in order:
1. Extract text from every PDF in data/aw-docs/ (pypdf).
2. Chunk each document's text (CHUNK_SIZE_CHARS with CHUNK_OVERLAP_CHARS
   overlap, both below -- tune these constants directly if your documents
   need different chunking).
3. Embed each chunk via the Foundry account's embedding deployment
   (AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME) and upload into a real Azure AI
   Search index (create_index/embed_and_upload).
4. Wrap that index in a SearchIndexKnowledgeSource + KnowledgeBase, which is
   what kb-agent and orchestrator-agent actually query at runtime.

Why a self-built searchIndex-kind pipeline, not Foundry's built-in `file`-
or `azureBlob`-kind knowledge sources: `file`-kind is currently rejected by
the agentic-retrieval API in this environment ("not supported in this API
version"), and `azureBlob`-kind can't be used because this tenant's
governance policy hard-blocks Storage's public network access. `searchIndex`
kind is the fully-supported option that depends on neither.
"""

import hashlib
import os
from pathlib import Path

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    HnswParameters,
    KnowledgeBase,
    KnowledgeBaseAzureOpenAIModel,
    KnowledgeSourceReference,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.knowledgebases.models import KnowledgeRetrievalMinimalReasoningEffort
from dotenv import load_dotenv
from openai import AzureOpenAI
from pypdf import PdfReader

load_dotenv()

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
FOUNDRY_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
CHAT_MODEL_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1")
EMBEDDING_MODEL_DEPLOYMENT = os.environ.get("AZURE_EMBEDDING_MODEL_DEPLOYMENT_NAME", "text-embedding-3-large")
EMBEDDING_DIMENSIONS = 3072  # text-embedding-3-large default

INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX_NAME", "aw-docs-index")
KNOWLEDGE_SOURCE_NAME = os.environ.get("AZURE_SEARCH_KNOWLEDGE_SOURCE_NAME", "aw-docs-source")
KNOWLEDGE_BASE_NAME = os.environ.get("AZURE_SEARCH_KNOWLEDGE_BASE_NAME", "aw-knowledge-base")

# Ships with the repo at data/aw-docs/ -- override AW_PDF_DIR only if your
# source PDFs live somewhere else.
DEFAULT_PDF_DIR = Path(__file__).resolve().parent.parent / "data" / "aw-docs"
PDF_DIR = Path(os.environ.get("AW_PDF_DIR", DEFAULT_PDF_DIR))
PDF_FILES = sorted(p.name for p in PDF_DIR.glob("*.pdf"))

CHUNK_SIZE_CHARS = 2000
CHUNK_OVERLAP_CHARS = 200


def chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE_CHARS
        chunks.append(text[start:end])
        start = end - CHUNK_OVERLAP_CHARS
    return [c for c in chunks if c.strip()]


def extract_chunks() -> list[dict]:
    if not PDF_FILES:
        raise SystemExit(f"No PDFs found in {PDF_DIR}. Add PDFs there or set AW_PDF_DIR.")
    all_chunks = []
    for filename in PDF_FILES:
        path = PDF_DIR / filename
        reader = PdfReader(str(path))
        full_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        for i, chunk in enumerate(chunk_text(full_text)):
            # Deterministic (not random) so re-running this script overwrites
            # each chunk's existing document instead of adding a duplicate
            # alongside it -- upload_documents upserts by this key.
            chunk_id = hashlib.sha1(f"{filename}:{i}".encode("utf-8")).hexdigest()
            all_chunks.append({
                "id": chunk_id,
                "content": chunk,
                "source_file": filename,
                "chunk_index": i,
            })
        print(f"Extracted {len(reader.pages)} pages / chunked into pieces from '{filename}'")
    return all_chunks


def create_index(index_client: SearchIndexClient) -> None:
    index = SearchIndex(
        name=INDEX_NAME,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SearchField(name="content", type=SearchFieldDataType.String, searchable=True),
            SimpleField(name="source_file", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=EMBEDDING_DIMENSIONS,
                vector_search_profile_name="default-profile",
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="default-hnsw", parameters=HnswParameters())],
            profiles=[VectorSearchProfile(name="default-profile", algorithm_configuration_name="default-hnsw")],
        ),
        semantic_search=SemanticSearch(
            default_configuration_name="default-semantic",
            configurations=[
                SemanticConfiguration(
                    name="default-semantic",
                    prioritized_fields=SemanticPrioritizedFields(
                        content_fields=[SemanticField(field_name="content")],
                    ),
                )
            ],
        ),
    )
    index_client.create_or_update_index(index)
    print(f"Index '{INDEX_NAME}' created/updated.")


def embed_and_upload(chunks: list[dict], credential) -> None:
    token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    openai_client = AzureOpenAI(
        azure_endpoint=FOUNDRY_OPENAI_ENDPOINT,
        azure_ad_token_provider=token_provider,
        api_version="2024-06-01",
    )

    search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=INDEX_NAME, credential=credential)

    batch_size = 16
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c["content"] for c in batch]
        response = openai_client.embeddings.create(model=EMBEDDING_MODEL_DEPLOYMENT, input=texts)
        for chunk, embedding in zip(batch, response.data):
            chunk["content_vector"] = embedding.embedding
        search_client.upload_documents(documents=batch)
        print(f"Uploaded batch {i // batch_size + 1} ({len(batch)} chunks)")


def create_knowledge_source_and_base(index_client: SearchIndexClient) -> None:
    knowledge_source = SearchIndexKnowledgeSource(
        name=KNOWLEDGE_SOURCE_NAME,
        description="Adventure Works retail customer-support PDFs, chunked and embedded into a search index.",
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=INDEX_NAME,
        ),
    )
    index_client.create_or_update_knowledge_source(knowledge_source)
    print(f"Knowledge source '{KNOWLEDGE_SOURCE_NAME}' created/updated.")

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
    index_client.create_or_update_knowledge_base(knowledge_base)
    print(f"Knowledge base '{KNOWLEDGE_BASE_NAME}' created/updated.")


def main() -> None:
    credential = DefaultAzureCredential()
    index_client = SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=credential)

    create_index(index_client)
    chunks = extract_chunks()
    embed_and_upload(chunks, credential)
    create_knowledge_source_and_base(index_client)


if __name__ == "__main__":
    main()
