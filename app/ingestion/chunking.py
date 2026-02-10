def semantic_chunking(text: str):
    chunks = [block.strip() for block in text.split("\n\n") if block.strip()]
    return chunks
