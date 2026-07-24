#!/usr/bin/env python3
"""
QLoRA fine-tuning script for Qwen2.5-14B-Instruct
Optimized for single A100 80GB
"""

import os
import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer

# Configuration
MODEL_NAME = "Qwen/Qwen2.5-14B-Instruct"
OUTPUT_DIR = "./qwen-tooluse-lora"
DATA_FILE = "qwen_agent_training_data.jsonl"

# QLoRA config
LORA_R = 64
LORA_ALPHA = 128
LORA_DROPOUT = 0.05
LORA_TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

# Training config
NUM_EPOCHS = 3
BATCH_SIZE = 4
GRADIENT_ACCUMULATION = 4
LEARNING_RATE = 2e-4
MAX_SEQ_LENGTH = 2048
WARMUP_RATIO = 0.03

def format_chat(example):
    """Format messages into Qwen chat template"""
    messages = example["messages"]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    return {"text": text}

def main():
    global tokenizer
    
    print(f"Loading model: {MODEL_NAME}")
    
    # Quantization config for QLoRA
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    
    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    
    # Load model
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
    )
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model)
    
    # LoRA config
    lora_config = LoraConfig(
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=LORA_TARGET_MODULES,
        bias="none",
        task_type="CAUSAL_LM",
    )
    
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()
    
    # Load dataset
    print(f"Loading dataset: {DATA_FILE}")
    dataset = load_dataset("json", data_files=DATA_FILE, split="train")
    print(f"Dataset size: {len(dataset)}")
    
    # Format dataset
    dataset = dataset.map(format_chat, remove_columns=dataset.column_names)
    
    # Split train/eval
    dataset = dataset.train_test_split(test_size=0.05, seed=42)
    
    # Training arguments
    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=NUM_EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRADIENT_ACCUMULATION,
        learning_rate=LEARNING_RATE,
        weight_decay=0.01,
        warmup_ratio=WARMUP_RATIO,
        lr_scheduler_type="cosine",
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch",
        bf16=True,
        gradient_checkpointing=True,
        max_grad_norm=0.3,
        group_by_length=True,
        report_to="none",
        optim="paged_adamw_8bit",
    )
    
    # Trainer
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset["train"],
        eval_dataset=dataset["test"],
        peft_config=lora_config,
        tokenizer=tokenizer,
        args=training_args,
        max_seq_length=MAX_SEQ_LENGTH,
        dataset_text_field="text",
        packing=False,
    )
    
    print("Starting training...")
    trainer.train()
    
    print(f"Saving model to {OUTPUT_DIR}")
    trainer.save_model(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    
    print("Training complete!")

if __name__ == "__main__":
    main()
