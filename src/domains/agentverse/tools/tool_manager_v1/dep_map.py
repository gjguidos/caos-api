from typing import Any


def dep_map(agent) -> dict[str, Any]:
    # single place to define injectable names
    return {
        "llm": agent.llm,
        "image_client": agent.image,
        "video": agent.video,
        "audio": agent.audio,
        "transcription_client": agent.audio,
        "db": agent.db,
        "cache": agent.cache,
        "vectordb": agent.vectordb,
        "messaging": agent.messaging,
        "green_claim_service": agent.http.get("green_claim_service"),
    }
