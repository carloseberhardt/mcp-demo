import os
import asyncio
import json
from datetime import datetime
from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_ibm import ChatWatsonx

# Phoenix tracing - simplified
import phoenix as px
from phoenix.otel import register
from openinference.instrumentation.langchain import LangChainInstrumentor

load_dotenv()

def get_agent_name_from_prompt_file(prompt_file):
    """Convert prompt filename to agent name - supports multiple formats"""
    # Remove common extensions
    base_name = prompt_file
    for ext in ['.md', '.txt', '.prompt']:
        if base_name.endswith(ext):
            base_name = base_name[:-len(ext)]
            break
    
    # Support multiple naming conventions
    if '_' in base_name:
        # underscore_separated -> Title-Case
        words = base_name.split('_')
        agent_name = '-'.join(word.capitalize() for word in words)
    elif '-' in base_name:
        # hyphen-separated -> Title Case
        words = base_name.split('-')
        agent_name = ' '.join(word.capitalize() for word in words)
    else:
        # single word or already formatted
        agent_name = base_name.capitalize()
    
    return agent_name

def get_available_models():
    """Parse available models from .env file (both commented and uncommented WATSONX_MODELNAME lines)"""
    models = []
    try:
        with open('.env', 'r') as f:
            for line in f:
                line = line.strip()
                if 'WATSONX_MODELNAME=' in line:
                    # Remove comment prefix if present
                    if line.startswith('#'):
                        line = line[1:].strip()
                    # Extract model name
                    if '=' in line:
                        model = line.split('=', 1)[1].strip().strip('"')
                        if model and model not in models:
                            models.append(model)
    except FileNotFoundError:
        pass
    return models

def get_current_model():
    """Get the currently active model from environment"""
    return os.environ.get("WATSONX_MODELNAME", "unknown")

def switch_model(new_model):
    """Switch to a new model and return updated ChatWatsonx instance"""
    os.environ["WATSONX_MODELNAME"] = new_model
    return ChatWatsonx(
        model_id=new_model,
        url=os.environ["WATSONX_URL"],
        apikey=os.environ["WATSONX_API_KEY"],
        project_id=os.environ["WATSONX_PROJECT_ID"],
        params={
            "decoding_method": "greedy",
            "max_new_tokens": 4096,
            "temperature": 0.0
        }
    )
async def main():
    # Get agent configuration
    prompt_file = os.environ.get("AGENT_PROMPT_FILE")
    if not prompt_file:
        print("❌ AGENT_PROMPT_FILE environment variable is required")
        return
    
    prompts_dir = os.environ.get("AGENT_PROMPTS_DIR", "prompts")
    agent_name = get_agent_name_from_prompt_file(prompt_file)
    
    print(f"🚀 Starting {agent_name} with Phoenix tracing...")
    print(f"🤖 Model: {get_current_model()}")
    
    # Start Phoenix
    session = px.launch_app()
    print(f"🔍 Phoenix UI: {session.url}")
    
    # Instrument LangChain for tracing
    tracer_provider = register()
    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
    
    # Initialize watsonx.ai model
    model = ChatWatsonx(
        model_id=os.environ["WATSONX_MODELNAME"],
        url=os.environ["WATSONX_URL"],
        apikey=os.environ["WATSONX_API_KEY"],
        project_id=os.environ["WATSONX_PROJECT_ID"],
        params={
            "decoding_method": "greedy",
            "max_new_tokens": 4096,
            "temperature": 0.0
        }
    )
    
    # Connect to MCP server and get tools
    client = MultiServerMCPClient({
        "stepzen": {
            "url": os.environ["STEPZEN_MCP_URL"],
            "transport": "streamable_http",
            "headers": {
                "Authorization": os.environ["STEPZEN_API_KEY"]
            }
        }
    })
    
    tools = await client.get_tools()
    
    # Wrap tools to handle HTTP errors gracefully
    def wrap_tool_for_error_handling(tool):
        """Wrap a tool to catch HTTP errors and return them as results instead of throwing"""
        # Try different execution methods in order of preference
        original_func = None
        method_name = None
        
        if hasattr(tool, 'coroutine') and tool.coroutine:
            original_func = tool.coroutine
            method_name = 'coroutine'
        elif hasattr(tool, 'func') and tool.func:
            original_func = tool.func
            method_name = 'func'
        elif hasattr(tool, '_arun'):
            original_func = tool._arun
            method_name = '_arun'
        
        if not original_func:
            return tool
        
        def extract_response_body(exception):
            """Extract HTTP response body from exception"""
            if hasattr(exception, 'response') and exception.response is not None:
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
                error_msg += "\n\nPlease fix the query based on the error details above and try again."
                return (error_msg, {"error": "400_bad_request", "details": error_str})
            else:
                return (f"Error: Tool call failed with: {error_str}", {"error": "unknown", "details": error_str})
            
        async def wrapped_func(*args, **kwargs):
            # Fix the variables parameter - convert string to object if needed
            if 'variables' in kwargs and isinstance(kwargs['variables'], str):
                try:
                    kwargs['variables'] = json.loads(kwargs['variables'])
                except json.JSONDecodeError:
                    pass  # Keep as string if it's not valid JSON
            
            try:
                result = await original_func(*args, **kwargs)
                return result
            except BaseExceptionGroup as eg:
                # Handle ExceptionGroup from anyio TaskGroup
                error_str = str(eg)
                response_body = None
                
                # Try to extract HTTP response body from nested exceptions
                if hasattr(eg, 'exceptions'):
                    for exc in eg.exceptions:
                        response_body = extract_response_body(exc)
                        if response_body:
                            break
                
                return format_error_response(error_str, response_body)
            except Exception as e:
                # Handle regular exceptions
                error_str = str(e)
                response_body = extract_response_body(e)
                return format_error_response(error_str, response_body)
        
        # Wrap the identified method
        try:
            setattr(tool, method_name, wrapped_func)
        except Exception:
            return tool
            
        return tool
    
    # Apply error handling to all tools
    wrapped_tools = [wrap_tool_for_error_handling(tool) for tool in tools]
    
    # Cache the prompt template to avoid repeated file I/O
    prompt_path = os.path.join(prompts_dir, prompt_file)
    with open(prompt_path) as f:
        prompt_template = f.read()
    
    def create_agent():
        # Format the prompt with current date
        current_date = datetime.now().strftime("%Y-%m-%d (%A)")
        formatted_prompt = prompt_template.format(current_date=current_date)
        
        return create_react_agent(
            model=model,
            tools=wrapped_tools,
            prompt=formatted_prompt,
            debug=False  # Clean output
        )
    
    current_agent = create_agent()
    print(f"💬 {agent_name} ready! Type a question (or 'quit' to exit, 'help' for commands)…")
    
    while True:
        try:
            q = input("\n› ")
            
            # Handle commands
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
                current_agent = create_agent()
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
                
                current_model = get_current_model()
                print("🔄 Select model:")
                for i, model_name in enumerate(available_models, 1):
                    marker = " (current)" if model_name == current_model else ""
                    print(f"  {i}. {model_name}{marker}")
                
                try:
                    choice = input("Enter number: ").strip()
                    if choice.isdigit():
                        choice_idx = int(choice) - 1
                        if 0 <= choice_idx < len(available_models):
                            new_model = available_models[choice_idx]
                            if new_model == current_model:
                                print(f"🤖 Already using {new_model}")
                            else:
                                # Switch model and create new agent (conversation history will be lost)
                                model = switch_model(new_model)
                                current_agent = create_agent()
                                print(f"🤖 Switched to {new_model}")
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
                
            try:
                # Use ainvoke for now to get the response, then add streaming later
                res = await current_agent.ainvoke({"messages": [{"role": "user", "content": q}]})
                
                # Extract final answer - it should be the last message
                if res and res.get('messages'):
                    final_message = res['messages'][-1]
                    if hasattr(final_message, 'content') and final_message.content:
                        print(f"\n💬 {final_message.content}\n")
                    else:
                        print("❌ No content in final message")
                else:
                    print("❌ No response generated")
                
            except Exception as e:
                print(f"❌ Agent Error: {e}")
                
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
