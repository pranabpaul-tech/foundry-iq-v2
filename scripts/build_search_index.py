# Copyright (c) Microsoft. All rights reserved.
"""Build a conventional (searchIndex-kind) Foundry IQ Knowledge Base.

Fallback path: the `file`-kind knowledge source (used successfully in the
prior POC) is currently rejected by the agentic-retrieval API ("not
supported in this API version" -- a platform-side regression/rollout change,
not something in our control), and `azureBlob`-kind is unusable because this
tenant's governance policy hard-blocks Storage's public network access no
matter how it's requested.

`searchIndex` kind is the one fully-supported, stable option left that
doesn't depend on either of those. This script does the ingestion work
itself: extract text from the PDFs, chunk it, embed each chunk via the
Foundry account's embedding deployment, push into a real Search index, then
wrap that index in a SearchIndexKnowledgeSource + KnowledgeBase.
"""

import os
import uuid
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

PDF_DIR = Path(os.environ["AW_PDF_DIR"])
PDF_FILES = [
    "Adventure Works Inc. – Retail Customer Payment, Purchase, Shipping & Refund Information.pdf",
    "Adventure Works Inc. – Retail Customer Terms and Conditions.pdf",
    "Adventure Works Inc Retail Customer Support Representative Guide.pdf",
]

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
    all_chunks = []
    for filename in PDF_FILES:
        path = PDF_DIR / filename
        reader = PdfReader(str(path))
        full_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        for i, chunk in enumerate(chunk_text(full_text)):
            all_chunks.append({
                "id": str(uuid.uuid4()),
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
