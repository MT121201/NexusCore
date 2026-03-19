

# Define your agents declaratively
DB_AGENT_PROFILE = AgentProfile(
    name="db_agent",
    system_prompt="You are the Database Specialist...",
    allowed_tools=["list_tables", "describe_table", "run_read_only_query"]
)