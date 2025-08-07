import os
import asyncio
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END, add_messages
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from typing import TypedDict, Annotated
from playwright.async_api import async_playwright

base_url = "http://localhost:11434/v1"
api_key = "ollama"
model_name = "gpt-oss:20b"

# 1. Define Tools
@tool
async def run_api(command: str) -> str:
    """
    Executes a command against the MMGIS Frontend JavaScript API by connecting to a running browser instance.
    Example: 'window.mmgisAPI.map.panTo([-5.4, 137.8], 8);'
    """
    print(f"---TOOL CALLED: run_api with command: {command}---")
    try:
        async with async_playwright() as p:
            # Connect to the running browser
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            # Assume MMGIS is in the first tab of the first context
            page = browser.contexts[0].pages[0] 
            
            # Check if we're on the right page
            if "localhost:8888" not in page.url:
                 return f"Error: Could not find MMGIS page. Make sure it's open on http://localhost:8888 in the connected browser."

            # Execute the command but do not return the massive map object.
            await page.evaluate(command)
            await browser.close()
            return "Success: The command was executed in the browser."
    except Exception as e:
        return f"Error executing command with Playwright: {str(e)}"

@tool
def read_file(file_path: str) -> str:
    """Read contents of a file"""
    print(f"---TOOL CALLED: read_file on {file_path}---")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"

@tool
def list_directory(directory_path: str) -> str:
    """List contents of a directory"""
    print(f"---TOOL CALLED: list_directory on {directory_path}---")
    try:
        items = os.listdir(directory_path)
        return "\n".join(items)
    except Exception as e:
        return f"Error listing directory {directory_path}: {str(e)}"

@tool
def find_files_by_pattern(pattern: str, directory: str = ".") -> str:
    """Find files matching a glob pattern"""
    print(f"---TOOL CALLED: find_files_by_pattern with pattern {pattern}---")
    try:
        import glob
        search_pattern = os.path.join(directory, pattern)
        matches = glob.glob(search_pattern, recursive=True)
        return "\n".join(matches) if matches else "No files found matching pattern"
    except Exception as e:
        return f"Error searching for pattern {pattern}: {str(e)}"

tools = [run_api, read_file, list_directory, find_files_by_pattern]

# 2. Define Agent State and Agents
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

def create_planner_model():
    return ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0
    )

def create_api_agent_model():
    llm = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=0
    )
    return llm.bind_tools(tools)

# 3. Assemble the graph
def create_agent():
    
    planner_prompt = """You are an expert planner and orchestrator for the MMGIS application. Your primary goal is to understand complex, multi-step user requests and break them down into a clear, sequential plan of actionable steps. You do not have direct access to tools. For any action that requires interacting with the MMGIS API (for either the backend or frontend), modifying files, or accessing the file system, you must delegate the task to the api_agent. Formulate a single, clear, and actionable instruction for the api_agent for each step in your plan. Wait for the api_agent to report success before proceeding to the next step. Simple tasks might be one step, while complex ones may require multiple steps of delegation."""
    
    api_agent_prompt = """You are an API and file system specialist for the MMGIS application. You have access to a comprehensive API documentation file located at 'LLM/APIs.md'.

Your primary role is to:
1.  **Consult the API Documentation:** Before taking any action, you MUST read the 'LLM/APIs.md' file to understand the available endpoints, their parameters, and the correct data structures.
2.  **Use Your Tools:** You have access to tools for making API calls (`run_api`), reading files (`read_file`), listing directories (`list_directory`), and finding files by pattern (`find_files`).
3.  **Formulate a Plan:** Based on the user's request and the API documentation, formulate a plan of action. This may involve multiple tool calls.
4.  **Execute and Handle Errors:** Execute your plan, carefully checking the output of each tool call. If you encounter an error, analyze the error message, consult the documentation again, and revise your plan.
5.  **Return Structured Results:** When you have completed your task, return a structured result to the planner.

You are responsible for all external API calls and file operations. Be methodical, be precise, and always refer to the documentation."""

    planner_model = create_planner_model()
    api_agent_model = create_api_agent_model()

    async def planner_node(state: AgentState):
        return {"messages": [await planner_model.ainvoke(state["messages"])]}

    async def api_agent_node(state: AgentState):
        return {"messages": [await api_agent_model.ainvoke(state["messages"])]}

    tool_node = ToolNode(tools)

    graph = StateGraph(AgentState)
    graph.add_node("planner", planner_node)
    graph.add_node("api_agent", api_agent_node)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("api_agent")

    graph.add_edge("planner", "api_agent")
    
    def should_continue(state: AgentState):
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            return "tools"
        return END

    graph.add_conditional_edges(
        "api_agent",
        should_continue,
        {"tools": "tools", END: END}
    )
    
    graph.add_edge("tools", "api_agent")

    agent = graph.compile()
    return agent

if __name__ == "__main__":
    agent = create_agent()
    async def main():
        response = await agent.ainvoke({"messages":[("user", "List all the files in the current directory.")]})
        print(response)
    asyncio.run(main())
