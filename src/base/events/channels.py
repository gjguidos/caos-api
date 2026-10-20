def user_event_channel(user_id: str) -> str:
    if not user_id:
        raise ValueError(
            "user_id is required for a user event channel."
        )

    return f"agentverse:events:user:{user_id}"