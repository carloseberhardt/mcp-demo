# MCP Agent Demo

A conversational AI agent that connects IBM watsonx.ai models with StepZen's MCP (Model Context Protocol) server for GraphQL data access.

## Features

- **Multiple AI Models**: Switch between various watsonx.ai models on-the-fly
- **MCP Integration**: Connect to StepZen MCP servers for GraphQL tool access
- **Flexible Prompts**: Use any prompt file with automatic agent naming
- **Phoenix Tracing**: Built-in observability with Phoenix tracing
- **Error Handling**: Graceful handling of GraphQL errors with helpful feedback
- **Interactive Chat**: Clean command-line interface with conversation history

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for dependency management
- IBM watsonx.ai account and API key
- StepZen MCP server endpoint and API key

## Quick Start

1. **Install uv** (if not already installed):
   ```bash
   # macOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # Windows
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   
   # Or with pip
   pip install uv
   ```

2. **Clone and install dependencies**:
   ```bash
   git clone <repository-url>
   cd mcp-demo
   uv sync
   ```

3. **Set up environment**:
   ```bash
   cp sample.env .env
   # Edit .env with your actual credentials and configuration
   ```

4. **Create a prompt file**:
   ```bash
   mkdir -p prompts
   echo "You are a helpful AI assistant." > prompts/my_agent.md
   ```

5. **Update your .env**:
   ```bash
   AGENT_PROMPT_FILE=my_agent.md
   ```

6. **Run the agent**:
   ```bash
   uv run python agent.py
   ```

### Alternative: Using pip/requirements.txt

If you prefer traditional pip workflow, you can generate a requirements.txt:
```bash
uv pip compile pyproject.toml -o requirements.txt
pip install -r requirements.txt
python agent.py
```

## Configuration

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `STEPZEN_MCP_URL` | Yes | Your StepZen MCP endpoint URL | - |
| `STEPZEN_API_KEY` | Yes | Your StepZen API key | - |
| `WATSONX_API_KEY` | Yes | IBM watsonx.ai API key | - |
| `WATSONX_PROJECT_ID` | Yes | IBM watsonx.ai project ID | - |
| `WATSONX_URL` | Yes | IBM watsonx.ai service URL | - |
| `WATSONX_MODELNAME` | Yes | Active model name | - |
| `AGENT_PROMPT_FILE` | Yes | Path to your prompt file | - |
| `AGENT_PROMPTS_DIR` | No | Directory containing prompts | `prompts` |

### Supported Models

The agent supports various watsonx.ai models including:
- **Mistral**: `mistralai/mistral-large`, `mistralai/mistral-medium-2505`
- **IBM Granite**: `ibm/granite-3-3-8b-instruct`
- **Meta Llama**: `meta-llama/llama-3-3-70b-instruct`

See `sample.env` for the complete list.

## Usage

### Interactive Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear conversation history |
| `/model` | Show current model |
| `/switch` | Switch to a different model |
| `quit`, `exit`, `q` | Exit the agent |

### Prompt Files

Create prompt files in your `AGENT_PROMPTS_DIR` (default: `prompts/`):

```bash
# Example prompt file
prompts/cost_optimizer.md
```

**Agent Naming**: Filenames are automatically converted to agent names:
- `cost_optimizer.md` -> "Cost-Optimizer"
- `data-analyst.md` -> "Data Analyst"
- `researcher.txt` -> "Researcher"

**Supported Extensions**: `.md`, `.txt`, `.prompt`

### Template Variables

Your prompt files can use template variables:
- `{{CURRENT_DATE}}` - Current date in YYYY-MM-DD (Day) format

Example:
```markdown
You are a helpful assistant. Today is {{CURRENT_DATE}}.
```

**Note**: We use `{{CURRENT_DATE}}` instead of `{current_date}` to avoid conflicts with GraphQL syntax, which uses single braces `{ }` for queries.

## Model Switching

The `/switch` command allows you to change models during conversation:

1. Lists all models found in your `.env` file (both active and commented)
2. Shows which model is currently active
3. Switches to the selected model
4. **Note**: Conversation history is reset when switching models

## Error Handling

The agent gracefully handles GraphQL errors from the MCP server:
- Converts HTTP errors into readable feedback
- Extracts detailed error information from API responses
- Allows the agent to self-correct based on error details
- Prevents crashes during tool failures

## Observability

Phoenix tracing is automatically enabled:
- Traces all LangChain operations
- Provides detailed execution insights
- UI accessible at the URL shown on startup

## Troubleshooting

### Common Issues

**"AGENT_PROMPT_FILE environment variable is required"**
- Ensure `AGENT_PROMPT_FILE` is set in your `.env`
- Verify the file exists in your `AGENT_PROMPTS_DIR`

**"No models found in .env file"**
- Add commented `WATSONX_MODELNAME` lines to your `.env`
- Format: `#WATSONX_MODELNAME="model-name-here"`

**GraphQL errors**
- The agent will show detailed error messages
- Use the error details to correct your queries
- Common issues: invalid syntax, missing fields, wrong variable types

### Debug Mode

To enable debug output, modify `agent.py`:
```python
debug=True  # in create_react_agent()
```

## Development

To modify the agent behavior:
1. Update prompt files for different personalities/capabilities
2. Modify `agent.py` for new features
3. Add new MCP servers in the client configuration
4. Extend error handling for additional tool types

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.