# OMNI architecture

The project is organized as a compact orchestrator with these responsibilities:

- planner: turns a user goal into dependency-aware task steps
- safety: validates workspace and action requests
- adapters: exposes desktop metadata and safe launch access
- verification: independently checks postconditions
- orchestrator: runs ready steps and manages retries
- api: exposes local HTTP and WebSocket interfaces
- dashboard: renders live status information
