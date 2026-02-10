import chromadb

client = chromadb.Client()
collection = client.get_or_create_collection(name="knowledge")

def store_chunks(chunks, embeddings):
    ids = [f"id_{i}" for i in range(len(chunks))]
    collection.add(documents=chunks, embeddings=embeddings, ids=ids)

def search_chunks(query_embedding, intent: str = "other", top_k: int | None = None):
    # Auto top_k selon l’intent
    if top_k is None:
        if intent in ["product_info", "catalog"]:
            top_k = 20      # liste / catalogue => plus de résultats
        elif intent in ["pricing", "services", "orders"]:
            top_k = 5       # question précise => moins
        else:
            top_k = 5

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k
    )

    # Chroma retourne souvent: {"documents": [[...]]}
    docs = results.get("documents", [])
    return docs[0] if docs and isinstance(docs[0], list) else docs

