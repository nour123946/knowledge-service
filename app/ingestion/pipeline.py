from app.ingestion.parsers import parse_txt, parse_pdf
from app.ingestion.chunking import semantic_chunking
from app.embeddings.hf_provider import embed_texts
from app.vectorstore.chroma_store import store_chunks

def ingest_file(file_path: str):
    if file_path.endswith(".pdf"):
        text = parse_pdf(file_path)
    else:
        text = parse_txt(file_path)

    chunks = semantic_chunking(text)
    embeddings = embed_texts(chunks)
    store_chunks(chunks, embeddings)

    return {"chunks_indexed": len(chunks)}
