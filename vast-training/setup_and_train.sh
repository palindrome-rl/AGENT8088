#!/bin/bash
set -e

echo "=== Setting up environment ==="
pip install -U pip
pip install -r requirements.txt

echo "=== Starting training ==="
python train_qlora.py

echo "=== Training complete ==="
echo "LoRA adapter saved to ./qwen-tooluse-lora"
