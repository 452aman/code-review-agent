from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage
from typing import Annotated
from typing_extensions import TypedDict
from pathlib import Path
from dotenv import load_dotenv
import os

from tools import run_code, search_web
from rag_tool import search_docs

load_dotenv(Path(__file__).parent / ".env")

SYSTEM_PROMPT = """You are a Python code review expert. When given code:
1. Identify bugs and issues
2. Suggest best practices
3. Recommend improvements

Respond directly with your review. Only use tools when you absolutely need to execute code or search for something specific."""

class State(TypedDict):
    messages: Annotated[list, add_messages]

tools = [run_code, search_web, search_docs]
llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"))
llm_with_tools = llm.bind_tools(tools)

def chatbot(state: State):
    messages = list(state["messages"])
    if not any(isinstance(m, SystemMessage) for m in messages):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
    return {"messages": [llm_with_tools.invoke(messages)]}

tool_node = ToolNode(tools=tools)

graph_builder = StateGraph(State)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_node("tools", tool_node)
graph_builder.add_edge(START, "chatbot")
graph_builder.add_conditional_edges("chatbot", tools_condition)
graph_builder.add_edge("tools", "chatbot")

graph = graph_builder.compile()

if __name__ == "__main__":
    print("Graph compiled OK:", graph)
