import os
import anthropic
from anthropic.types import MessageParam, TextBlock, ToolUseBlock, ToolUnionParam
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Union, cast
from dataclasses import dataclass, field
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

anthropic_client = anthropic.AsyncAnthropic()

server_params = StdioServerParameters(
    command="python",
    args=["./mcp_server.py"],
    env=None,
)

@dataclass
class Chat:
    messages: list[MessageParam] = field(default_factory=list)

    system_prompt: str = """You are a master SQLite assistant. 
    Your job is to use the tools at your disposal to execute SQL queries and provide the results to the user."""

    async def process_query(self, session: ClientSession, query: str) -> str:
        try:
            print("🔍 Getting available tools from MCP...")
            response = await session.list_tools()
            available_tools: list[ToolUnionParam] = [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                }
                for tool in response.tools
            ]
            print(f"✅ Got {len(available_tools)} tools")

            self.messages.append(MessageParam(role="user", content=query))
            final_response = ""

            while True:
                print("🤖 Sending message to Claude...")
                res = await anthropic_client.messages.create(
                    model="claude-3-7-sonnet-latest",
                    system=self.system_prompt,
                    max_tokens=8000,
                    messages=self.messages,
                    tools=available_tools,
                )
                print("✅ Received response from Claude")

                assistant_message_content: list[Union[ToolUseBlock, TextBlock]] = []
                tool_uses = []

                for content in res.content:
                    if content.type == "text":
                        print("📝 Claude text:", content.text)
                        assistant_message_content.append(content)
                        final_response += content.text + "\n"
                    elif content.type == "tool_use":
                        print(f"🛠️ Tool use requested: {content.name}")
                        tool_uses.append(content)

                self.messages.append({
                    "role": "assistant",
                    "content": assistant_message_content + tool_uses
                })

                if not tool_uses:
                    print("✅ No tool use requested. Ending loop.")
                    break

                for tool_use in tool_uses:
                    print(f"📡 Calling tool: {tool_use.name} with args {tool_use.input}")
                    result = await session.call_tool(tool_use.name, cast(dict, tool_use.input))
                    result_text = getattr(result.content[0], "text", "")
                    print("📬 Tool result:", result_text)
                    self.messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "content": result_text,
                        }]
                    })

            return final_response.strip()

        except Exception as e:
            print("❌ Error in process_query:", str(e))
            return f"❌ Error during Claude + MCP query: {str(e)}"

chat = Chat()

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        print("⚙️ Starting MCP client session...")
        async with stdio_client(server_params) as (read, write):
            session = ClientSession(read, write)
            await session.initialize()
            app.state.session = session
            print("✅ MCP session initialized.")
            yield
    except Exception as e:
        print("❌ Failed to start MCP session:", e)
        yield

app.router.lifespan_context = lifespan

class UserQuery(BaseModel):
    message: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
async def chat_api(query: UserQuery):
    print("📥 Incoming message:", query.message)
    if hasattr(app.state, "session"):
        result = await chat.process_query(app.state.session, query.message)
        print("📤 Final result:", result)
        return {"response": result}
    else:
        print("⚠️ No session found, falling back.")
        return {"response": f"[Fallback] Echo: {query.message}"}

