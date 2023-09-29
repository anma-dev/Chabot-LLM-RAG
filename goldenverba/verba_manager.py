import os
import ssl

import weaviate

from typing import Optional
from weaviate import Client
from weaviate.embedded import EmbeddedOptions
from wasabi import msg

from goldenverba.ingestion.reader.manager import ReaderManager
from goldenverba.ingestion.chunking.manager import ChunkerManager
from goldenverba.ingestion.embedding.manager import EmbeddingManager
from goldenverba.ingestion.reader.document import Document
from goldenverba.ingestion.reader.interface import Reader
from goldenverba.ingestion.chunking.interface import Chunker
from goldenverba.ingestion.embedding.interface import Embedder

import goldenverba.ingestion.schema.schema_generation as schema_manager


class VerbaManager:
    """Manages all Verba Components"""

    def __init__(self) -> None:
        self.reader_manager = ReaderManager()
        self.chunker_manager = ChunkerManager()
        self.embedder_manager = EmbeddingManager()
        self.environment_variables = {}
        self.installed_libraries = {}
        self.weaviate_type = ""
        self.client = self.setup_client()

        self.verify_installed_libraries()
        self.verify_variables()

        # Check if all schemas exist for all possible vectorizers
        for vectorizer in schema_manager.VECTORIZERS:
            schema_manager.init_schemas(self.client, vectorizer, False, True)

        for embedding in schema_manager.EMBEDDINGS:
            schema_manager.init_schemas(self.client, embedding, False, True)

    def import_data(
        self,
        bytes: list[str],
        contents: list[str],
        paths: list[str],
        fileNames: list[str],
        document_type: str,
        units: int = 100,
        overlap: int = 50,
    ) -> list[Document]:
        loaded_documents = self.reader_manager.load(
            bytes, contents, paths, fileNames, document_type
        )
        modified_documents = self.chunker_manager.chunk(
            loaded_documents, units, overlap
        )

        if self.embedder_manager.embed(modified_documents, client=self.client):
            msg.good("Embedding successful")
            return modified_documents
        else:
            msg.fail("Embedding failed")
            return None

    def reader_set_reader(self, reader: str) -> bool:
        return self.reader_manager.set_reader(reader)

    def reader_get_readers(self) -> dict[str, Reader]:
        return self.reader_manager.get_readers()

    def chunker_set_chunker(self, chunker: str) -> bool:
        return self.chunker_manager.set_chunker(chunker)

    def chunker_get_chunker(self) -> dict[str, Chunker]:
        return self.chunker_manager.get_chunkers()

    def embedder_set_embedder(self, embedder: str) -> bool:
        return self.embedder_manager.set_embedder(embedder)

    def embedder_get_embedder(self) -> dict[str, Embedder]:
        return self.embedder_manager.get_embedders()

    def setup_client(self) -> Optional[Client]:
        """
        @returns Optional[Client] - The Weaviate Client
        """

        msg.info("Setting up client")

        additional_header = {}
        client = None

        # Check OpenAI ENV KEY
        try:
            import openai

            openai_key = os.environ.get("OPENAI_API_KEY", "")
            if openai_key != "":
                additional_header["X-OpenAI-Api-Key"] = openai_key
                self.environment_variables["OPENAI_API_KEY"] = True
                openai.api_key = openai_key
                msg.info("OpenAI API key detected")
            else:
                self.environment_variables["OPENAI_API_KEY"] = False
        except Exception as e:
            self.environment_variables["OPENAI_API_KEY"] = False

        # Check Verba URL ENV
        weaviate_url = os.environ.get("VERBA_URL", "")
        if weaviate_url != "":
            weaviate_key = os.environ.get("VERBA_API_KEY", "")
            self.environment_variables["VERBA_URL"] = True
            self.weaviate_type = "Weaviate Cluster"
            auth_config = weaviate.AuthApiKey(api_key=weaviate_key)
            client = weaviate.Client(
                url=weaviate_url,
                additional_headers=additional_header,
                auth_client_secret=auth_config,
            )
        # Use Weaviate Embedded
        else:
            try:
                _create_unverified_https_context = ssl._create_unverified_context
            except AttributeError:
                pass
            else:
                ssl._create_default_https_context = _create_unverified_https_context

            msg.info("Using Weaviate Embedded")
            self.weaviate_type = "Weaviate Embedded"
            client = weaviate.Client(
                additional_headers={"X-OpenAI-Api-Key": openai.api_key},
                embedded_options=EmbeddedOptions(
                    persistence_data_path="./.verba/local/share/",
                    binary_path="./.verba/cache/weaviate-embedded",
                ),
            )

        if client != None:
            msg.good("Connected to Weaviate")
        else:
            msg.fail("Connection to Weaviate failed")

        return client

    def verify_installed_libraries(self) -> None:
        """
        Checks which libraries are installed and fills out the self.installed_libraries dictionary for the frontend to access
        """

        # spaCy, used for Chunking
        try:
            import spacy

            self.installed_libraries["spacy"] = True
        except Exception as e:
            self.installed_libraries["spacy"] = False

    def verify_variables(self) -> None:
        """
        Checks which environment variables are installed and fills out the self.environment_variables dictionary for the frontend to access
        """

        # OpenAI API Key
        if os.environ.get("OPENAI_API_KEY", "") != "":
            self.environment_variables["OPENAI_API_KEY"] = True
        else:
            self.environment_variables["OPENAI_API_KEY"] = False

    def get_schemas(self) -> dict:
        """
        @returns dict - A dictionary with the schema names and their object count
        """

        schema_info = self.client.schema.get()
        schemas = {}

        for _class in schema_info["classes"]:
            results = (
                self.client.query.get(_class["class"])
                .with_limit(100000)
                .with_additional(properties=["id"])
                .do()
            )

            schemas[_class["class"]] = len(results["data"]["Get"][_class["class"]])

        return schemas

    def retrieve_all_documents(self) -> list:
        """Return all documents from Weaviate
        @returns list - Document list
        """

        class_name = "Document_" + schema_manager.strip_non_letters(
            self.embedder_manager.selected_embedder.vectorizer
        )

        print(class_name)

        query_results = (
            self.client.query.get(
                class_name=class_name,
                properties=["doc_name", "doc_type", "doc_link"],
            )
            .with_additional(properties=["id"])
            .with_limit(10000)
            .do()
        )
        results = query_results["data"]["Get"][class_name]
        return results

    def retrieve_document(self, doc_id: str) -> dict:
        """Return a document by it's ID (UUID format) from Weaviate
        @parameter doc_id : str - Document ID
        @returns dict - Document dict
        """
        class_name = "Document_" + schema_manager.strip_non_letters(
            self.embedder_manager.selected_embedder.vectorizer
        )

        document = self.client.data_object.get_by_id(
            doc_id,
            class_name=class_name,
        )
        return document

    def reset(self):
        self.client.schema.delete_all()
