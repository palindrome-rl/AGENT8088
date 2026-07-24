"""
Agent8088 Configuration for Qwen 14B Tool-Use via Ollama

Reads settings from .env file (gitignored — never checked in).
Create .env from .env.template with your local values.
"""
import os
from pathlib import Path
from openai import OpenAI

# Load .env from the same directory as this file or parent project dir
_env_path = Path(__file__).parent / '.env'
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass  # fallback to os.environ or defaults

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen14b-tooluse-v3")
TIMEOUT_SECONDS = int(os.environ.get("TIMEOUT_SECONDS", "120"))
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888")

SYSTEM_PROMPT = """You are Agent8088, a precise task completion agent.

IMPORTANT: You use TOOLS to do things. Never just talk about tools — call them.

When you need to use a tool, output:
<tool_call>
{"name": "tool_name", "arguments": {"param": "value"}}
</tool_call>

Available tools:
- execute_shell(command: str): Run ANY shell command.
- search_web(query: str): Search the web for info.
- calculator(expression: str): Do math.
- read_file(filename: str): Read a file.
- write_file(filename: str, content: str): Write code or text to a file. USE THIS FOR CODE.
- get_last_output(): Get the FULL output from your last tool run.

RULES:
- When writing code, ALWAYS use write_file to save it. Never output raw code as text.
- After saving a file with write_file, tell the user what was saved.
- After getting tool results, give your FINAL ANSWER immediately.
- Never output raw tool JSON in your final answer — only plain text.
- Never call get_last_output() as a first action — call the real tool first.
- For simple factual questions (capitals, oceans, dates, symbols), just answer directly. Do not search the web for things you already know.
- When you need to run a system command, execute_shell is your primary tool for that."""


def get_client(use_adapter=True):
    """Get OpenAI-compatible client for Ollama."""
    client = OpenAI(
        base_url=OLLAMA_URL,
        api_key="ollama",
        timeout=TIMEOUT_SECONDS
    )
    return client


def create_completion(client, messages, tools, max_tokens=2000, system_prompt=None, temperature=0.1):
    """Create a completion via Ollama's OpenAI-compatible endpoint."""
    full_messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT}]
    full_messages.extend(messages)

    kwargs = dict(
        model=MODEL_NAME,
        messages=full_messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if tools:
        kwargs["tools"] = tools
    response = client.chat.completions.create(**kwargs)
    return response
