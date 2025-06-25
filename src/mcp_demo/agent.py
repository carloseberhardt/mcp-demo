import os
import json
from datetime import datetime
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage, SystemMessage
from langgraph.graph.message import add_messages
from langchain_ibm import ChatWatsonx
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from langchain_mcp_adapters.client import MultiServerMCPClient

# Phoenix tracing
import phoenix as px
from openinference.instrumentation.langchain import LangChainInstrumentor
from phoenix.otel import register


# This defines the structure of the agent's state.
# `add_messages` is a special function that appends new messages to the list.
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def wrap_tool_for_error_handling(tool):
    """Wrap a tool to catch HTTP errors and return them as results instead of throwing"""
    original_func = None
    method_name = None

    if hasattr(tool, "coroutine") and tool.coroutine:
        original_func = tool.coroutine
        method_name = "coroutine"
    elif hasattr(tool, "func") and tool.func:
        original_func = tool.func
        method_name = "func"
    elif hasattr(tool, "_arun"):
        original_func = tool._arun
        method_name = "_arun"

    if not original_func:
        return tool

    def extract_response_body(exception):
        """Extract HTTP response body from exception"""
        if hasattr(exception, "response") and exception.response is not None:
            try:
                return exception.response.text
            except:
                pass
        return None

    def format_error_response(error_str, response_body=None):
        """Format error response with consistent structure"""
        if "400 Bad Request" in error_str or "HTTPStatusError" in error_str:
            error_msg = "Error: GraphQL query failed with 400 Bad Request."
            if response_body:
                error_msg += f"\n\nDetailed error from API:\n{response_body}"
            error_msg += f"\n\nFull exception: {error_str}"
            error_msg += (
                "\n\nPlease fix the query based on the error details and try again."
            )
            return (error_msg, {"error": "400_bad_request", "details": error_str})
        else:
            return (
                f"Error: Tool call failed with: {error_str}",
                {"error": "unknown", "details": error_str},
            )

    async def wrapped_func(*args, **kwargs):
        try:
            result = await original_func(*args, **kwargs)
            return result
        except BaseExceptionGroup as eg:
            error_str = str(eg)
            response_body = None
            if hasattr(eg, "exceptions"):
                for exc in eg.exceptions:
                    response_body = extract_response_body(exc)
                    if response_body:
                        break
            return format_error_response(error_str, response_body)
        except Exception as e:
            error_str = str(e)
            response_body = extract_response_body(e)
            return format_error_response(error_str, response_body)

    try:
        setattr(tool, method_name, wrapped_func)
    except Exception:
        return tool

    return tool


async def get_tools():
    """Connect to MCP server and get tools"""
    client = MultiServerMCPClient(
        {
            "stepzen": {
                "url": os.environ["STEPZEN_MCP_URL"],
                "transport": "streamable_http",
                "headers": {"Authorization": os.environ["STEPZEN_API_KEY"]},
            }
        }
    )

    tools = await client.get_tools()
    return [wrap_tool_for_error_handling(tool) for tool in tools]


def create_agent(model: ChatWatsonx, tools: list):
    """
    Creates a custom ReAct-style agent using LangGraph.

    This function builds a state machine (a StateGraph) that defines the agent's behavior.
    It includes custom logic to handle cases where the model generates invalid tool calls,
    making the agent more robust.
    """
    # 1. Define the nodes for the graph
    #    - agent: Calls the LLM
    #    - action: Executes tools
    #    - handle_error: Responds to the LLM when it makes a formatting mistake

    # The `bind_tools` method makes the model aware of the tools it can use.
    model_with_tools = model.bind_tools(tools)

    def agent_node(state: AgentState):
        """Calls the model with the current set of messages."""
        response = model_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    def handle_error_node(state: AgentState):
        """
        Handles cases where the model generates invalid tool calls.
        This node creates a ToolMessage for each invalid call to inform the model of its mistake,
        allowing it to self-correct in the next turn.
        """
        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage) or not last_message.invalid_tool_calls:
            return {"messages": []}

        error_messages = []
        for itc in last_message.invalid_tool_calls:
            content = (
                f"Error: The model generated an invalid tool call for tool '{itc.get('name', 'unknown')}' "
                f"with arguments '{itc.get('args', '')}'. "
                f"Please check the tool's schema and your formatting and try again."
            )
            error_messages.append(ToolMessage(content=content, tool_call_id=itc["id"]))
        
        return {"messages": error_messages}

    def should_continue_node(state: AgentState):
        """
        Handle both successful and failed tool executions in a model-agnostic way.
        Forces retry on errors, forces final answer on success.
        """
        # Check if the last tool result was an error
        last_tool_message = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, ToolMessage):
                last_tool_message = msg
                break
        
        if last_tool_message and "Error:" in last_tool_message.content:
            # Tool failed - encourage retry instead of explanation
            decision_prompt = (
                "The previous tool call failed with an error. "
                "DO NOT explain the error or provide suggestions. "
                "Instead, make a corrected tool call that fixes the specific error mentioned above. "
                "Try again with the proper syntax based on the error message."
            )
        else:
            # Tool succeeded - provide final answer
            decision_prompt = (
                "Based on the tool results above, provide your final answer to the user's question. "
                "Do NOT make any more tool calls. Analyze the data and give a complete response."
            )
        
        decision_message = SystemMessage(content=decision_prompt)
        return {"messages": [decision_message]}

    # The ToolNode is a pre-built node that executes tool calls.
    tool_node = ToolNode(tools)

    # 2. Define the routing logic (the "sanity check")
    def router(state: AgentState) -> str:
        """
        Determines the next step for the agent based on the last message.
        """
        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage):
            return END

        # If the model generated invalid tool calls, route to the error handler.
        if last_message.invalid_tool_calls:
            return "handle_error"
        # If the model generated valid tool calls, route to the tool executor.
        if last_message.tool_calls:
            return "action"
        # Otherwise, the conversation is finished.
        return END

    # 3. Assemble the graph
    prompt_file = os.environ.get("AGENT_PROMPT_FILE")
    prompts_dir = os.environ.get("AGENT_PROMPTS_DIR", "prompts")
    prompt_path = os.path.join(prompts_dir, prompt_file)

    with open(prompt_path) as f:
        prompt_template = f.read()

    current_date = datetime.now().strftime("%Y-%m-%d (%A)")
    formatted_prompt = prompt_template.replace("{{CURRENT_DATE}}", current_date)

    # The initial state of the graph includes the system prompt.
    initial_state = {"messages": [SystemMessage(content=formatted_prompt)]}

    workflow = StateGraph(AgentState)

    workflow.add_node("agent", agent_node)
    workflow.add_node("action", tool_node)
    workflow.add_node("handle_error", handle_error_node)
    workflow.add_node("should_continue", should_continue_node)

    workflow.set_entry_point("agent")

    workflow.add_conditional_edges(
        "agent",
        router,
        {
            "action": "action",
            "handle_error": "handle_error",
            END: END,
        },
    )
    workflow.add_edge("action", "should_continue")
    workflow.add_edge("should_continue", "agent")
    workflow.add_edge("handle_error", "agent")

    # 4. Compile the graph and return it
    return workflow.compile(), formatted_prompt


def init_tracing():
    """Initialize Phoenix tracing with clean output"""
    import sys
    from io import StringIO
    
    # Temporarily capture stdout to suppress verbose Phoenix messages
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    
    try:
        session = px.launch_app()
        tracer_provider = register()
        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
    finally:
        # Restore stdout
        sys.stdout = old_stdout
    
    # Only show the clean UI message
    print(f"🔍 Phoenix UI: {session.url}")