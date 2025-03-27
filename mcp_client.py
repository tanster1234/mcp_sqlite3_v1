import streamlit as st
import asyncio
import anthropic
import textwrap
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typing import Union, cast, List, Dict, Any
import pandas as pd
import io
import logging
import uuid
import json
import re
import plotly.express as px
import plotly.graph_objects as go

class SQLiteAssistantApp:
    def __init__(self):
        st.set_page_config(
            page_title="Enterprise SQLite Assistant",
            page_icon="🔍",
            layout="wide",
            initial_sidebar_state="expanded"
        )

        if 'messages' not in st.session_state:
            st.session_state.messages = []

        if 'selectbox_keys' not in st.session_state:
            st.session_state.selectbox_keys = set()

        if 'last_query_result' not in st.session_state:
            st.session_state.last_query_result = ""

        if 'sql_finished' not in st.session_state:
            st.session_state.sql_finished = False

        self.anthropic_client = anthropic.AsyncAnthropic()

        self.server_params = StdioServerParameters(
            command="python",
            args=["./mcp_server.py"],
            env=None
        )

    def get_unique_key(self, prefix=''):
        while True:
            key = f"{prefix}_{uuid.uuid4().hex[:8]}"
            if key not in st.session_state.selectbox_keys:
                st.session_state.selectbox_keys.add(key)
                return key

    def render_header(self):
        st.markdown("""
        # 📂 Enterprise SQLite Intelligence
        ### Advanced Data Query & Insights Platform
        """)
        st.divider()

    def render_sidebar(self):
        with st.sidebar:
            st.header("🛠️ Query Configuration")

            model_key = self.get_unique_key('model')
            model = st.selectbox(
                "Select AI Model", 
                ["claude-3-7-sonnet-latest"],
                key=model_key
            )

            tokens_key = self.get_unique_key('tokens')
            max_tokens = st.slider(
                "Max Response Tokens", 
                min_value=1000, 
                max_value=16000, 
                value=8000,
                key=tokens_key
            )

            st.divider()
            st.info("""
            💡 Pro Tip:
            - Use clear, precise SQL queries
            - Check table names before querying
            - Leverage AI for complex data analysis
            """)

        return model, max_tokens

    async def generate_visualizations(self, model):
        print('inside function: generate visualization')
        result_text = st.session_state.last_query_result
        if not result_text:
            st.warning("No final output available for visualization.")
            return

        system_prompt = """
        You are a data visualization expert. You will receive the result of a SQL query in plain text (not a DataFrame).
        It may contain insights or summaries, not necessarily tabular data. Your task is to propose meaningful and
        relevant visualizations using plotly based on the textual result. Only return Python code that creates the visualizations.
        """

        messages = [
            {"role": "user", "content": f"Here is the SQL query result:\n\n{result_text}\n\nPlease generate visualizations if appropriate."}
        ]

        try:
            response = await self.anthropic_client.messages.create(
                model=model,
                system=system_prompt,
                max_tokens=2000,
                messages=messages
            )

            for i, content in enumerate(response.content):
                if content.type == "text":
                    st.code(content.text, language='python')
                    try:
                        cleaned_code = re.sub(r'^```(?:python)?\n|```$', '', content.text.strip(), flags=re.MULTILINE).strip()
                        exec_globals = {
                            "st": st,
                            "pd": pd,
                            "px": px,
                            "go": go
                        }

                        # Patch timeline to avoid x_start == x_end error
                        original_timeline = px.timeline
                        def safe_timeline(*args, **kwargs):
                            df = kwargs.get('data_frame', args[0] if args else None)
                            if df is not None and 'x_start' in kwargs and 'x_end' in kwargs:
                                if isinstance(df, pd.DataFrame):
                                    df = df.copy()
                                    x_start = kwargs['x_start']
                                    x_end = kwargs['x_end']
                                    if (df[x_end] == df[x_start]).all():
                                        df[x_end] = pd.to_datetime(df[x_end]) + pd.Timedelta(days=1)
                                    kwargs['data_frame'] = df
                            fig = original_timeline(*args, **kwargs)
                            fig.show = lambda: st.plotly_chart(fig, use_container_width=True)
                            return fig
                        exec_globals['px'].timeline = safe_timeline

                        # Patch all show() methods to use Streamlit
                        def patched_show(fig):
                            st.plotly_chart(fig, use_container_width=True)
                        exec_globals['go'].Figure.show = patched_show
                        exec_globals['px'].pie().show = patched_show
                        exec_globals['px'].scatter().show = patched_show
                        exec_globals['px'].bar().show = patched_show
                        exec_globals['px'].line().show = patched_show

                        exec(cleaned_code, exec_globals)
                    except Exception as exec_err:
                        st.error(f"Execution error: {exec_err}")

        except Exception as e:
            st.warning(f"Could not generate visualizations: {e}")

    async def process_query(self, session, query, model, max_tokens):
        try:
            messages = st.session_state.messages + [
                {"role": "user", "content": query}
            ]

            response = await session.list_tools()
            available_tools = [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                }
                for tool in response.tools
            ]

            system_prompt = textwrap.dedent("""\
                You are a master SQLite assistant. 
                Before executing any query, first verify the table names and structure. 
                If tables are missing, explain why the query cannot be executed. 
                Your job is to use the tools at your disposal to execute SQL queries and provide the results to the user.
            """)

            while True:
                ai_response = await self.anthropic_client.messages.create(
                    model=model,
                    system=system_prompt,
                    max_tokens=max_tokens,
                    messages=messages,
                    tools=available_tools
                )

                assistant_message_content: List[Dict[str, Any]] = []
                tool_uses = []

                for content in ai_response.content:
                    if content.type == "text":
                        assistant_message_content.append({"type": "text", "text": content.text})
                        st.chat_message("assistant").write(content.text)
                        st.session_state.last_query_result = content.text
                    elif content.type == "tool_use":
                        tool_uses.append(content)
                        assistant_message_content.append({
                            "type": "tool_use", 
                            "id": content.id, 
                            "name": content.name, 
                            "input": content.input
                        })

                messages.append({
                    "role": "assistant",
                    "content": assistant_message_content
                })

                if not tool_uses:
                    break

                tool_results = []
                for i, tool_use in enumerate(tool_uses):
                    try:
                        result = await session.call_tool(
                            tool_use.name, 
                            cast(dict, tool_use.input)
                        )

                        result_text = getattr(result.content[0], "text", "")
                        result_text = result_text.strip().replace('\x00', '')

                        with st.expander("📜 Executed SQL Query"):
                            try:
                                sql_display = tool_use.input.get("query") if isinstance(tool_use.input, dict) else str(tool_use.input)
                                if sql_display:
                                    st.code(sql_display, language='sql')
                                else:
                                    st.write("Raw tool input:", tool_use.input)
                            except Exception as e:
                                st.warning("Failed to retrieve SQL query.")
                                st.write("Raw tool input:", tool_use.input)

                        st.code(result_text)

                        tool_result = {
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "content": result_text
                        }
                        tool_results.append(tool_result)

                    except Exception as tool_error:
                        st.error(f"Tool execution error: {tool_error}")

                messages.append({
                    "role": "user",
                    "content": tool_results
                })

            st.session_state.sql_finished = True

        except Exception as e:
            st.error(f"Query processing error: {e}")
            logging.error(f"Query processing error: {e}")

    def run(self):
        self.render_header()
        model, max_tokens = self.render_sidebar()

        query = st.chat_input("Enter your SQL query...")

        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                if isinstance(message["content"], list):
                    for content in message["content"]:
                        if content.get("type") == "text":
                            st.write(content["text"])
                else:
                    st.write(message["content"])

        if query:
            st.session_state.sql_finished = False
            with st.chat_message("user"):
                st.write(query)

            async def async_runner():
                async with stdio_client(self.server_params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await self.process_query(
                            session, 
                            query, 
                            model=model, 
                            max_tokens=max_tokens
                        )

            asyncio.run(async_runner())

        if st.session_state.get("sql_finished"):
            with st.expander("📊 AI-Generated Visualizations"):
                asyncio.run(self.generate_visualizations(model))
                st.session_state.sql_finished = False

def main():
    app = SQLiteAssistantApp()
    app.run()

if __name__ == "__main__":
    main()