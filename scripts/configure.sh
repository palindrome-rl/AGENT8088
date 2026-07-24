#!/bin/bash
# Agent8088 Configuration Wizard
# Copyright © 2026 Palindrome Research Labs

echo "╔════════════════════════════════════════╗"
echo "║   Agent8088 Configuration Wizard      ║"
echo "║   Palindrome Research Labs            ║"
echo "╚════════════════════════════════════════╝"
echo ""

# Detect OS
OS=$(uname -s)
echo "Detected OS: $OS"
echo ""

# Get Ollama host
read -p "Ollama host [localhost]: " OLLAMA_HOST
OLLAMA_HOST=${OLLAMA_HOST:-localhost}

# Get Ollama port
read -p "Ollama port [11434]: " OLLAMA_PORT
OLLAMA_PORT=${OLLAMA_PORT:-11434}

# Get model name
read -p "Model name [qwen3:14b]: " MODEL_NAME
MODEL_NAME=${MODEL_NAME:-qwen3:14b}

# Create config file
cat > config.ini << EOF
[DEFAULT]
ollama_host = $OLLAMA_HOST
ollama_port = $OLLAMA_PORT
model_name = $MODEL_NAME

[paths]
memory_db = ./agent8088_memory.db
skills_dir = ./skills

[logging]
level = INFO
file = ./agent8088.log
EOF

echo ""
echo "✅ Configuration saved to config.ini"
echo ""
echo "Next steps:"
echo "1. Install dependencies: pip install -r requirements.txt"
echo "2. Test configuration: ./agent8088 --test"
echo "3. Run agent: ./agent8088"
echo ""
