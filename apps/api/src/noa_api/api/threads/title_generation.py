from __future__ import annotations


def _extract_text_chunks(value: object) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    if isinstance(value, dict):
        if value.get("type") == "text":
            text_value = value.get("text")
            if isinstance(text_value, str):
                normalized = text_value.strip()
                return [normalized] if normalized else []
        nested_content = value.get("content")
        if nested_content is not None:
            return _extract_text_chunks(nested_content)
        return []
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(_extract_text_chunks(item))
        return chunks
    return []


def _message_text_chunks(message: dict[str, object]) -> list[str]:
    parts = message.get("parts")
    if parts is not None:
        chunks = _extract_text_chunks(parts)
        if chunks:
            return chunks

    content = message.get("content")
    return _extract_text_chunks(content)
