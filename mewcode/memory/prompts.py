MEMORY_UPDATE_SCHEMA = '[{"action":"create|update|delete","level":"project|user","type":"user_preference|correction_feedback|project_knowledge|reference_material","title":"...","slug":"...","filename":"...","content":"..."}]'


def build_memory_prompt(messages, project_index: str, user_index: str) -> str:
    recent = "\n".join(f"{m.role}: {m.content}" for m in messages)
    return f"Extract durable memory. Return ONLY a JSON array matching this schema: {MEMORY_UPDATE_SCHEMA}\nProject index:\n{project_index}\nUser index:\n{user_index}\nRecent conversation:\n{recent}"
