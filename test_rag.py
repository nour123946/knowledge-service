from app.ingestion.pipeline import ingest_file
from app.embeddings.hf_provider import embed_texts
from app.vectorstore.chroma_store import search_chunks

# Indexation
ingest_file("data/business_data.txt")

# Recherche
query = "prix ultraboost"
query_embedding = embed_texts([query])[0]
results = search_chunks(query_embedding)

print("\nRESULTATS TROUVÉS :\n")
print(results)
