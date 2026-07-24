"""
Agent8088 Configuration for LFM2.5-8B-A1B via Ollama

Uses the Liquid AI LFM2.5 model (8.3B MoE, 1.5B active)
Optimized for tool calling, built for on-device deployment.
"""

import json
import httpx

OLLAMA_URL = "http://<IP_ADDRESS>:11434/api/chat"
MODEL_NAME = "lfm25-tooluse"
TIMEOUT_SECONDS = 120

# System prompt - asks model to output tool call JSON inline
SYSTEM_PROMPT = """You are Agent8088, a precise task completion agent built for tool calling.

You have tools available. When you need to use a tool, think step by step inside <|thinking|> tags, then output:

{"name": "tool_name", "arguments": {"param": "value"}}

Available tools:
- search_web(query: str): Search the web for current info
- calculator(expression: str): Perform math calculations (e.g. "2+2", "15 * 7")
- read_file(filename: str): Read file contents
- write_file(filename: str, content: str): Write content to a file
- multi_step_plan(task: str): Plan and execute compound tasks

ALWAYS call the tool rather than just describing what you would do.
After the tool runs and you see the result, continue with the next step.
When creating files or websites, use write_file for each file."""


def get_client(use_adapter=True):
    """Return a client dict (Ollama has no OpenAI client)."""
    return {"base_url": OLLAMA_URL, "model": MODEL_NAME}


def create_completion(client, messages, tools, max_tokens=2000, system_prompt=None):
    """Send messages to Ollama and return response."""
    # Build messages with system prompt
    full_messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT}]
    full_messages.extend(messages)

    payload = {
        "model": MODEL_NAME,
        "messages": full_messages,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": max_tokens,
        }
    }

    with httpx.Client(timeout=TIMEOUT_SECONDS) as http:
        resp = http.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

    # Build an OpenAI-compatible response object
    msg_text = data["message"]["content"]

    # The model outputs <|thinking|>...<|/thinking|> or thinking/response tags
    # Strip thinking tags for display
    import re
    display_text = re.sub(r'<\|thinking\|>.*?<\|/thinking\|>', '', msg_text, flags=re.DOTALL).strip()
    display_text = re.sub(r'^.*?thinking.*?\n', '', display_text, flags=re.DOTALL).strip()
    display_text = re.sub(r'^.*?\bresponse\b\s*', '', display_text).strip()

    class Choice:
        class Message:
            def __init__(self, content):
                self.content = content
        def __init__(self, content):
            self.message = self.Message(content)

    class Response:
        def __init__(self, choices):
            self.choices = choices

    return Response([Choice(display_text if display_text else msg_text)])
