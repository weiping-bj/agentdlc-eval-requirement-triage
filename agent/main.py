"""AgentCore Runtime entrypoint (B path).

Payload: {"feedback": {<one feedback item>}}  or  {"prompt": "<free text>"}
Returns the triage JSON (see data/HANDOFF.md for the output contract).
"""
from __future__ import annotations

import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from s12agent.core import triage

logging.basicConfig(level=logging.INFO)
app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context=None):
    item = payload.get("feedback") or payload.get("prompt") or ""
    session_id = getattr(context, "session_id", None) if context is not None else None
    out = triage(item, session_id=session_id)
    return {"result": out["result"], "raw": out["raw"], "session_id": out["session_id"]}


if __name__ == "__main__":
    app.run()
