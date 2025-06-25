import os
import asyncio
import warnings
from dotenv import load_dotenv

# Suppress SQLAlchemy reflection warnings that clutter startup output
warnings.filterwarnings("ignore", message="Skipped unsupported reflection of expression-based index.*", category=DeprecationWarning)
warnings.filterwarnings("ignore", message="Skipped unsupported reflection of expression-based index.*", category=UserWarning)
warnings.filterwarnings("ignore", message="Skipped unsupported reflection of expression-based index.*")
# Also catch the broader SAWarning category
try:
    from sqlalchemy.exc import SAWarning
    warnings.filterwarnings("ignore", category=SAWarning, message="Skipped unsupported reflection.*")
except ImportError:
    pass
from mcp_demo.agent import create_agent, get_tools, init_tracing
from langchain_core.messages import HumanMessage, SystemMessage
from mcp_demo.utils import (
    get_agent_name_from_prompt_file,
    get_available_models,
    get_current_model,
    switch_model,
)

async def cli():
    """Main CLI loop for the agent"""
    load_dotenv()
    
    prompt_file = os.environ.get("AGENT_PROMPT_FILE")
    if not prompt_file:
        print("❌ AGENT_PROMPT_FILE environment variable is required")
        return

    agent_name = get_agent_name_from_prompt_file(prompt_file)
    
    print(f"🚀 Starting {agent_name} with Phoenix tracing...")
    print(f"🤖 Model: {get_current_model()}")
    
    init_tracing()
    
    model = switch_model(get_current_model()) # Initialize model
    tools = await get_tools()
    current_agent, system_prompt = create_agent(model, tools)
    
    print(f"💬 {agent_name} ready! Type a question (or 'quit' to exit, 'help' for commands)…")
    
    # Maintain conversation history
    messages = []
    
    while True:
        try:
            q = input("\n› ")
            
            if q.lower() in ['quit', 'exit', 'q']:
                break
            elif q.lower() in ['/help', 'help']:
                print("📋 Available commands:")
                print("  /help   - Show this help message")
                print("  /clear  - Clear conversation history") 
                print("  /model  - Show current model")
                print("  /switch - Switch to a different model")
                print("  quit    - Exit the chat (also: exit, q)")
                print("  Or just type your question!")
                continue
            elif q.lower() in ['/clear', 'clear']:
                messages = []
                print("🧹 Conversation history cleared!")
                continue
            elif q.lower() == '/model':
                print(f"🤖 Current model: {get_current_model()}")
                continue
            elif q.lower() == '/switch':
                available_models = get_available_models()
                if not available_models:
                    print("❌ No models found in .env file")
                    continue
                
                current_model_name = get_current_model()
                print("🔄 Select model:")
                for i, model_name in enumerate(available_models, 1):
                    marker = " (current)" if model_name == current_model_name else ""
                    print(f"  {i}. {model_name}{marker}")
                
                try:
                    choice = input("Enter number: ").strip()
                    if choice.isdigit():
                        choice_idx = int(choice) - 1
                        if 0 <= choice_idx < len(available_models):
                            new_model_name = available_models[choice_idx]
                            if new_model_name == current_model_name:
                                print(f"🤖 Already using {new_model_name}")
                            else:
                                model = switch_model(new_model_name)
                                current_agent, system_prompt = create_agent(model, tools)
                                messages = [] # Clear history on model switch
                                print(f"🤖 Switched to {new_model_name}")
                        else:
                            print("❌ Invalid selection")
                    else:
                        print("❌ Please enter a number")
                except (ValueError, KeyboardInterrupt):
                    print("❌ Selection cancelled")
                continue
            elif not q.strip():
                continue
                
            print("🤔 Processing...")
            
            # Add the user's question to the message history
            messages.append(HumanMessage(content=q))
            
            try:
                # Stream events to show progress and capture final state
                final_state = None
                async for event in current_agent.astream_events(
                    {"messages": [SystemMessage(content=system_prompt)] + messages},
                    version="v1"
                ):
                    kind = event.get("event")
                    if kind == "on_chat_model_stream":
                        content = event["data"]["chunk"].content
                        if content:
                            print(content, end="")
                    elif kind == "on_chat_model_end":
                        # Show any reasoning text the model generated
                        message = event.get("data", {}).get("output", {})
                        if hasattr(message, 'content') and message.content:
                            if not message.tool_calls:  # Only show if no tool calls (final response)
                                print(f"\n🧠 Model reasoning: {message.content[:100]}{'...' if len(message.content) > 100 else ''}")
                    elif kind == "on_tool_start":
                        tool_input = event.get("data", {}).get("input", {})
                        print(f"\n\n🛠️ Calling tool `{event['name']}` with input:")
                        print(f"   Query: {tool_input.get('query', 'N/A')[:100]}{'...' if len(str(tool_input.get('query', ''))) > 100 else ''}")
                        print(f"   Variables: {tool_input.get('variables', 'N/A')}\n")
                    elif kind == "on_tool_end":
                        result = event.get("data", {}).get("output", "")
                        print(f"\n`{event['name']}` returned:\n{str(result)[:200]}{'...' if len(str(result)) > 200 else ''}\n")
                    elif kind == "on_chain_end" and event.get("name") == "LangGraph":
                        # Capture final state when the main graph completes
                        final_state = event.get("data", {}).get("output")
                
                if final_state and "messages" in final_state:
                    # Keep all messages except the initial system prompt for conversation history
                    messages = [msg for msg in final_state['messages'][1:] if not isinstance(msg, SystemMessage)]

                print("\n") # Newline after streaming is done

            except Exception as e:
                print(f"❌ Agent Error: {e}")
                
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

def main():
    try:
        asyncio.run(cli())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")

if __name__ == "__main__":
    main()