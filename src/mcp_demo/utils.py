import os
from langchain_ibm import ChatWatsonx
from ibm_watsonx_ai import APIClient, Credentials

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

def get_available_models_from_api():
    """Query watsonx.ai API for available models that support tool calling"""
    try:
        credentials = Credentials(
            url=os.environ["WATSONX_URL"],
            api_key=os.environ["WATSONX_API_KEY"],
        )
        client = APIClient(credentials)

        # Get model specs filtered by tool calling support
        model_specs = client.foundation_models.get_model_specs(
            filters="task_function_calling"
        )

        # Extract model IDs from the specs
        models = []
        if model_specs and 'resources' in model_specs:
            for spec in model_specs['resources']:
                model_id = spec.get('model_id')
                if model_id:
                    models.append(model_id)

        return sorted(models)
    except Exception as e:
        print(f"⚠️  Warning: Could not fetch models from API: {e}")
        # Fallback to .env file parsing
        return get_available_models_from_env()

def get_available_models_from_env():
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

def get_available_models():
    """Get available models - tries API first, falls back to .env file"""
    return get_available_models_from_api()

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
