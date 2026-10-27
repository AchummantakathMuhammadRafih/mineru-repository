#!/usr/bin/env bash
# ==============================================================================
# Remote VM Setup Script for MinerU PDF Converter
# Supports Ubuntu/Debian with automatic GPU (CUDA) or CPU detection.
# ==============================================================================

set -e

echo "=== [1/5] Installing OS System Dependencies ==="
sudo apt-get update && sudo apt-get install -y \
    python3-pip \
    python3-venv \
    git \
    libgl1-mesa-glx \
    libglib2.0-0 \
    poppler-utils \
    ffmpeg

echo "=== [2/5] Setting up Python Virtual Environment ==="
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip setuptools wheel

echo "=== [3/5] Installing PyTorch & Project Dependencies ==="
if command -v nvidia-smi &> /dev/null; then
    echo "✓ NVIDIA GPU detected. Installing CUDA PyTorch..."
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
else
    echo "ℹ No GPU detected. Installing CPU-optimized PyTorch..."
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
fi

pip install -e .

echo "=== [4/5] Pre-downloading MinerU Model Weights ==="
# Pre-download pipeline model weights to avoid runtime timeouts/failures
mineru-models-download -s huggingface -m pipeline

echo "=== [5/5] Verifying Installation & Model Config ==="
mineru-convert info

echo "=============================================================================="
echo "✓ Setup Complete! Activate virtualenv anytime with: source .venv/bin/activate"
echo "  Run conversion: mineru-convert convert input/document.pdf -o output/results"
echo "=============================================================================="
