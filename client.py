import asyncio
import json
import os
from typing import Optional
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.sse import sse_client
from dotenv import load_dotenv
import boto3

boto3_session = boto3.Session(profile_name='costnorm', region_name='us-east-1')
bedrock_runtime = boto3_session.client('bedrock-runtime')
CLAUDE_MODEL_ID = "anthropic.claude-3-sonnet-20240229-v1:0"


load_dotenv()  # load environment variables from .env


class MCPClient:
    def __init__(self):
        # Initialize session and client objects
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()

    async def connect_to_sse_server(self, server_url: str):
        """Connect to an MCP server running with SSE transport"""
        # Store the context managers so they stay alive
        self._streams_context = sse_client(url=server_url)
        streams = await self._streams_context.__aenter__()

        self._session_context = ClientSession(*streams)
        self.session: ClientSession = await self._session_context.__aenter__()

        # Initialize
        await self.session.initialize()

        # List available tools to verify connection
        print("Initialized SSE client...")
        print("Listing tools...")
        response = await self.session.list_tools()
        tools = response.tools
        print("\nConnected to server with tools:",
              [tool.name for tool in tools])

    async def cleanup(self):
        """Properly clean up the session and streams"""
        if self._session_context:
            await self._session_context.__aexit__(None, None, None)
        if self._streams_context:
            await self._streams_context.__aexit__(None, None, None)

    async def process_query(self, query: str) -> str:
        """Process a query using Claude and available tools"""
        system_prompt = [
            {
                "text": "You are a helpful assistant integrated with an MCP system. Use the provided tools ONLY when the user's request clearly and explicitly matches a tool's specific purpose described in its description. For general questions, requests for information not covered by the tools, or greetings, answer directly based on your knowledge without attempting to use any tool."
            }
        ]
        messages = [
            {
                "role": "user",
                "content": [{"text": query}]
            }
        ]

        response = await self.session.list_tools()
        available_tools = [{
            "toolSpec": {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": {
                    "json": tool.inputSchema
                }
            }
        } for tool in response.tools]

        # Initial Claude API call
        response = bedrock_runtime.converse(
            modelId=CLAUDE_MODEL_ID,
            messages=messages,
            system=system_prompt,
            toolConfig={
                'tools': available_tools
            }
        )

        # Process response and handle tool calls
        tool_results = []
        final_text = []

        response_message = response['output']['message']
        messages.append(response_message)
        stop_reason = response.get('stopReason')

        while stop_reason == 'tool_use':
            tool_use_requests = [
                content for content in response_message['content'] if content.get('toolUse')]
            tool_result_contents = []

            for tool_request in tool_use_requests:
                tool_id = tool_request['toolUse']['toolUseId']
                tool_name = tool_request['toolUse']['name']
                tool_input = tool_request['toolUse']['input']

                # Execute tool call
                print(
                    f"--- Calling tool {tool_name} with input: {tool_input} ---")
                result = await self.session.call_tool(tool_name, tool_input)
                print(f"--- Tool {tool_name} result: {result} ---")

                # Append user-facing text
                final_text.append(
                    f"[Calling tool {tool_name} with args {tool_input}]")

                # Prepare tool result content for the API
                # Extract text content, handle potential errors or different content types if needed
                tool_output_content = []
                if result.isError:
                    # Or more specific error handling
                    tool_output_content.append(
                        {"text": f"Tool execution failed: {result.content}"})
                    # Optionally add status: 'error' below
                elif result.content and isinstance(result.content[0], dict) and 'json' in result.content[0]:
                    tool_output_content.append(
                        {"json": result.content[0]['json']})
                elif result.content and hasattr(result.content[0], 'text'):
                    tool_output_content.append(
                        {"text": result.content[0].text})
                else:
                    # Handle unexpected result format
                    tool_output_content.append(
                        {"text": "Tool returned unexpected content format."})

                tool_result_contents.append({
                    "toolResult": {
                        "toolUseId": tool_id,
                        "content": tool_output_content,
                        # "status": "error" # Uncomment and set if the tool call failed
                    }
                })

            # Create the user message containing tool results
            tool_result_message = {
                "role": "user",
                "content": tool_result_contents
            }
            messages.append(tool_result_message)

            # Get next response from Claude
            print("--- Sending tool results back to Claude ---")
            response = bedrock_runtime.converse(
                modelId=CLAUDE_MODEL_ID,
                messages=messages,
                toolConfig={'tools': available_tools}
            )
            response_message = response['output']['message']
            messages.append(response_message)

            # Append text content from the new response
            assistant_text_content = [
                c.get('text') for c in response_message.get('content', []) if 'text' in c]
            if assistant_text_content:
                final_text.append("\n".join(assistant_text_content))

            stop_reason = response.get('stopReason')

        # Handle final response if it wasn't a tool use
        if stop_reason == 'end_turn':
            # The first response might have already been handled if it wasn't tool_use
            # Check if final_text is empty to avoid duplicate appending
            if not final_text:
                assistant_text_content = [
                    c.get('text') for c in response_message.get('content', []) if 'text' in c]
                if assistant_text_content:
                    final_text.append("\n".join(assistant_text_content))

        return "\n".join(final_text)

    async def chat_loop(self):
        """Run an interactive chat loop"""
        print("\nMCP Client Started!")
        print("Type your queries or 'quit' to exit.")

        while True:
            try:
                query = input("\nQuery: ").strip()

                if query.lower() == 'quit':
                    break

                response = await self.process_query(query)
                print("\n" + response)

            except Exception as e:
                print(
                    f"\nError on line {sys.exc_info()[2].tb_lineno}: {str(e)}")


async def main():
    if len(sys.argv) < 2:
        print("Usage: uv run client.py <URL of SSE MCP server (i.e. http://localhost:8080/sse)>")
        sys.exit(1)

    client = MCPClient()
    try:
        await client.connect_to_sse_server(server_url=sys.argv[1])
        await client.chat_loop()
    except Exception as e:
        print(f"\nError: {str(e)}")
    finally:
        await client.cleanup()


if __name__ == "__main__":
    import sys
    asyncio.run(main())
