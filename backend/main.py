import sys, asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import json, sqlite3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agent import agent_executor

app = FastAPI(title="Flowtrace API")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

class RunRequest(BaseModel):
    prompt: str

@app.post("/run")
async def run_workflow(req: RunRequest):
    async def event_stream():
        try:
            for chunk in agent_executor.stream(
                {"messages": [{"role": "user", "content": req.prompt}]},
                stream_mode="updates"
            ):
                if "agent" in chunk:
                    for msg in chunk["agent"].get("messages", []):
                        content = getattr(msg, "content", "")
                        if content:
                            yield f"data: {json.dumps({'type':'thinking','text':str(content)})}\n\n"
                elif "tools" in chunk:
                    for msg in chunk["tools"].get("messages", []):
                        yield f"data: {json.dumps({'type':'tool_result','text':str(msg.content)})}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','text':str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.get("/memory")
def get_memory():
    conn = sqlite3.connect("flowtrace_memory.db")
    rows = conn.execute(
        "SELECT timestamp, tool, input, output FROM actions ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return [{"timestamp":r[0],"tool":r[1],"input":r[2],"output":r[3]} for r in rows]

@app.get("/health")
def health():
    return {"status": "ok"}
