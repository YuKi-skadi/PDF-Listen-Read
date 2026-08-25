import re


def split_text_for_reading(text: str, max_chunk_length: int = 200) -> list[dict]:
    """Split text into stable, readable segments while preserving paragraphs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w+)-\s*\n\s*(\w+)", r"\1\2", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[dict] = []
    chunk_id = 0

    for paragraph in paragraphs:
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if not paragraph:
            continue

        if len(paragraph) <= max_chunk_length:
            chunks.append({"id": chunk_id, "text": paragraph})
            chunk_id += 1
            continue

        sentences = re.split(r"([。！？.!?]+)", paragraph)
        current = ""
        for index in range(0, len(sentences), 2):
            sentence = sentences[index]
            punctuation = sentences[index + 1] if index + 1 < len(sentences) else ""
            candidate = current + sentence + punctuation

            if current and len(candidate) > max_chunk_length:
                chunks.append({"id": chunk_id, "text": current.strip()})
                chunk_id += 1
                current = sentence + punctuation
            else:
                current = candidate

            while len(current) > max_chunk_length:
                chunks.append({
                    "id": chunk_id,
                    "text": current[:max_chunk_length].strip(),
                })
                chunk_id += 1
                current = current[max_chunk_length:].strip()

        if current.strip():
            chunks.append({"id": chunk_id, "text": current.strip()})
            chunk_id += 1

    return chunks
