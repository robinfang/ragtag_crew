"""AgentSession — self-built agent loop (LLM <-> tool call cycle)."""

# TODO: implement
# - AgentSession class with prompt(), subscribe(), abort(), reset()
# - while loop: call LLM -> if tool_calls: execute -> continue; else: break
# - event system: agent_start, message_update, tool_execution_start/end, agent_end
# - max_turns guard, timeout, error handling
