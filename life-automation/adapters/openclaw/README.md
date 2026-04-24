# OpenClaw/Maxwell Adapter

Status: **Active**

## Setup

No additional setup — uses existing NFS bridge via `sync-openclaw-memory.sh`.

## Protocol

- Reads: filtered daily note synced to gateway memory dir every 15 min
- Writes: Maxwell dispatches ingested by `ingest_dispatches.py`
- Episodic: logged on sync (daily_note_appended) and dispatch ingestion (dispatch_ingested)
