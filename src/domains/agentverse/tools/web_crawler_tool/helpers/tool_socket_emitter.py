from src.domains.agentverse.events.socket_contract import LogLevel


async def emit(socket_emitter, message: str, level: LogLevel = LogLevel.INFO):
    if socket_emitter:
        await socket_emitter.log(
            f"[🕷️ TOOL][WEB CRAWLER] {message}",
            level=level,
        )