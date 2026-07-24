# Agent 8088

**Production-ready AI agent with fine-tuned tool-calling capabilities**

*Developed by Palindrome Research Labs*

---

[![License](https://img.shields.io/badge/license-Private-red.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

---

## Overview

Agent 8088 is an enterprise-grade AI agent powered by fine-tuned Qwen 2.5 14B, designed for reliable tool calling, multi-turn context retention, and seamless CLI integration.

### Key Features

- ✅ **Fine-tuned tool calling** - 95% accuracy on function selection
- ✅ **Multi-turn context** - Maintains conversation state across interactions
- ✅ **Grounded execution** - Trained on real production traces
- ✅ **Extensible skills** - Plugin architecture for custom tools
- ✅ **Production-ready** - Tested on 100+ real-world scenarios

### Performance

| Metric | Score |
|--------|-------|
| Tool Selection Accuracy | 95% |
| Valid Argument Generation | 93% |
| Context Retention | 87% |
| Hallucination Rate | < 5% |

---

## Quick Start

### Prerequisites

- Python 3.8 or higher
- Ollama running locally (or remote endpoint)
- 4GB RAM minimum

### Installation

**1. Clone Repository**

```bash
git clone https://github.com/palindromerl/agent8088.git
cd agent8088
```

**2. Run Configuration Wizard**

```bash
./configure.sh
```

The wizard will guide you through:
- Ollama host and port configuration
- Model selection (qwen3:14b recommended)
- Installation paths
- Logging preferences

**3. Install Dependencies**

```bash
pip install -r requirements.txt
```

**4. Run Agent**

```bash
./agent8088
```

---

## Configuration

### Interactive Setup (Recommended)

```bash
./configure.sh
```

### Manual Configuration

Edit `config.ini`:

```ini
[DEFAULT]
ollama_host = localhost
ollama_port = 11434
model_name = qwen3:14b

[paths]
memory_db = ./agent8088_memory.db
skills_dir = ./skills

[logging]
level = INFO
file = ./agent8088.log
```

### Environment Variables

```bash
export AGENT8088_OLLAMA_HOST="localhost:11434"
export AGENT8088_MODEL="qwen3:14b"
./agent8088
```

---

## Usage

### Basic Example

```bash
$ ./agent8088
Agent8088> list files in current directory
```

### Python API

```python
from agent8088 import Agent, Config

config = Config.from_file("config.ini")
agent = Agent(config)

response = agent.query("What's the system uptime?")
print(response)
```

### Custom Tools

```python
from agent8088.skills import register_skill

@register_skill(name="custom_tool", description="My custom tool")
def my_tool(param: str) -> str:
    return f"Processed: {param}"

agent.load_skills()
```

---

## Architecture

### Repository Structure

```
agent8088/
├── agent8088                 # Main executable
├── config.txt                # Runtime config (model, paths, search, skillopt)
├── tools.txt                 # Tool specs loaded by the agent
├── system.md                 # System prompt / skill document
├── configs/                  # Model-config variants you swap into config.txt
│   ├── reality7b_config_colossus.py
│   └── reality7b_config_ollama.py
├── scripts/                  # One-off repo ops
│   ├── configure.sh
│   ├── push-to-github.sh
│   └── verify-push.sh
├── research/                 # Non-runtime research/training pipeline
│   ├── skillopt.py           # SkillOpt self-improver
│   ├── run_benchmark.py      # Benchmark suite (used by skillopt)
│   ├── data_cleanup/         # Dataset curation
│   ├── vast-training/        # Vast.ai automation
│   └── paper/                # Research documentation
├── skills/                   # Agent skill YAMLs
├── docs/                     # Architecture / API docs
└── README.md
```

### How It Works

1. **User Query** → Agent8088 processes natural language
2. **Tool Selection** → Fine-tuned model selects appropriate tool
3. **Execution** → Tool executes with validated arguments
4. **Response** → Formatted output returned to user

---

## SkillOpt — Self-Improving Agent Skills

Agent8088 includes **SkillOpt**, a text-space optimization system that improves the agent's skill document (system.md) without touching model weights. Based on the technique from [arXiv:2605.23904](https://arxiv.org/abs/2605.23904).

### How It Works

1. **Rollout** — Run benchmark suite, capture successes/failures
2. **Reflect** — Optimizer model analyzes failure patterns, proposes atomic edits (add/delete/replace) to the skill document
3. **Validate** — Run benchmark with edited skill; accept only if score strictly improves
4. **Repeat** — Cosine-decaying textual learning rate over N epochs

**Zero inference-time overhead** — optimize the skill once, then run with the optimized playbook forever.

### Usage

```bash
# Run full optimization (4 epochs by default)
python3 research/skillopt.py

# Custom epochs
python3 research/skillopt.py --epochs 6

# Preview edits without applying
python3 research/skillopt.py --dry-run

# View optimization history
python3 research/skillopt.py --report

# Restore pre-optimization skill
python3 research/skillopt.py --restore
```

### Configuration

SkillOpt settings in `config.txt`:

```ini
# Optimizer can be a different (bigger) model than the target
optimizer_base_url=http://localhost:11434/v1
optimizer_model=qwen14b-tooluse-v3
skillopt_epochs=4
skillopt_textual_lr=4
```

**Key insight:** Small models benefit most. The paper showed Qwen3.5-4B gained +19.2 points average. Agent8088 works with any model size — SkillOpt improves the instructions, not the weights.

---

## Training

Agent 8088 is fine-tuned using:

- **Base Model:** Qwen/Qwen2.5-14B-Instruct
- **Method:** QLoRA (4-bit quantization)
- **Dataset:** 1,000 grounded traces + 9,255 ToolACE samples
- **Platform:** Vast.ai A100 80GB

See [TRAINING.md](docs/TRAINING.md) for details.

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System design and components |
| [Training Pipeline](docs/TRAINING.md) | Model training process |
| [Development Guide](docs/DEVELOPMENT.md) | Contributing guidelines |
| [API Reference](docs/API.md) | Python API documentation |
| [Tool Schema](docs/TOOL_SCHEMA.md) | Tool calling specification |

---

## Development

### Setup Development Environment

```bash
# Clone repository
git clone https://github.com/palindromerl/agent8088.git
cd agent8088

# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/
```

### Running Tests

```bash
# Unit tests
pytest tests/unit/

# Integration tests
pytest tests/integration/

# Full suite with coverage
pytest --cov=agent8088 tests/
```

### Contributing

See [DEVELOPMENT.md](docs/DEVELOPMENT.md) for contribution guidelines.

---

## Troubleshooting

### Common Issues

**"Model not found"**
```bash
# Pull model from Ollama
ollama pull qwen3:14b
```

**"Connection refused"**
```bash
# Check Ollama is running
ollama list

# Start Ollama if needed
ollama serve
```

**"Tool execution failed"**
```bash
# Check logs
tail -f agent8088.log

# Enable debug mode
export AGENT8088_LOG_LEVEL=DEBUG
./agent8088
```

---

## Support

- **Documentation:** https://docs.palindromerl.com/agent8088
- **Issues:** https://github.com/palindromerl/agent8088/issues
- **Email:** support@palindromerl.com

---

## License

Copyright © 2026 Palindrome Research Labs. All rights reserved.

Private - Not for public distribution.

---

## Citation

```bibtex
@software{agent8088,
  title = {Agent 8088: Fine-Tuned AI Agent for Tool Calling},
  author = {Palindrome Research Labs},
  year = {2026},
  url = {https://github.com/palindromerl/agent8088}
}
```

---

**Palindrome Research Labs** - Advanced AI Systems
# AGENT8088
