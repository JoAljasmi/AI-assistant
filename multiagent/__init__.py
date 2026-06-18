"""Personal AI assistant — a tool-using agent with multiple skills and transports.

Subpackages:
  core/        the agent loop, model provider, context builder, budget, approvals
  skills/      tools the agent can call (tasks, notes, web search/read, weather)
  transports/  how you talk to it (terminal console, Discord bot)
  runtime/     persistence + background jobs (sessions, settings, reminder scheduler)
  safety/      command sandbox + secret filtering

Run from the folder ABOVE this one with:  python -m multiagent.main
"""
