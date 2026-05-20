"""Source connectors for the Collection AI Agent.

Each connector implements the ConnectorABC contract:
- list_resources(): enumerate available channels/spaces/repos
- fetch_since(resource, ts): pull new payloads since the watermark
- normalize(payload): convert source-native shape → events row

The 2-tier capture rule (design v0.3):
  CAPTURE = wide (everything the user/tenant authorizes)
  DISTILL = narrow (signal filter inside the strategy YAML)

Concrete connectors:
- discord.py: HTTP API + bot token (no MCP dependency)
- (future) slack.py, notion.py, github.py, google_drive.py
"""
