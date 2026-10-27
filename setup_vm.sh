#!/usr/bin/env bash
# ==============================================================================
# Remote VM Setup Script for MinerU PDF Converter Pipeline
# Supports Ubuntu/Debian with automatic GPU (CUDA) or CPU detection.
# Fully configures:
#   - MinerU parser engine
#   - PyMuPDF hierarchy corrector
#   - Microsoft Presidio PII redaction + spaCy NLP model
#   - Streamlit web UI & Background watcher
# ==============================================================================

set -eo pipefail

echo "=============================================================================="
echo "🚀 [1/6] Installing OS System Dependencies"
echo "=============================================================================="
sudo apt-get update && sudo apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    git \
    libglib2.0-0 \
    poppler-utils \
    ffmpeg

# Install OpenGL libraries (handle Debian/Ubuntu package name differences)
sudo apt-get install -y libgl1 || sudo apt-get install -y libgl1-mesa-glx || true

echo "=============================================================================="
echo "🐍 [2/6] Setting up Python Virtual Environment"
echo "=============================================================================="
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip setuptools wheel

echo "=============================================================================="
echo "⚡ [3/6] Installing PyTorch & GPU/CPU Drivers"
echo "=============================================================================="
if command -v nvidia-smi &> /dev/null; then
    echo "✓ NVIDIA GPU detected. Installing CUDA 12.1 PyTorch..."
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
else
    echo "ℹ No GPU detected. Installing CPU-optimized PyTorch..."
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
fi

echo "=============================================================================="
echo "📦 [4/6] Installing Project Dependencies & NLP Models"
echo "=============================================================================="
# Install project in editable mode with development/test dependencies
pip install -e ".[dev]"

# Ensure Microsoft Presidio NLP model (spaCy en_core_web_sm) is installed
echo "Downloading spaCy English model for Presidio PII detection..."
python -m spacy download en_core_web_sm

# Ensure runtime input and output directories exist
mkdir -p input output

echo "=============================================================================="
echo "🤖 [5/6] Pre-downloading MinerU Pipeline Model Weights"
echo "=============================================================================="
# Pre-download weights to avoid runtime timeouts on first conversion
if ! mineru-models-download -s huggingface -m pipeline; then
    echo "ℹ HuggingFace download failed or was rate-limited; trying ModelScope mirror..."
    mineru-models-download -s modelscope -m pipeline || true
fi

echo "=============================================================================="
echo "🔍 [6/6] Verifying Component Integrations"
echo "=============================================================================="
mineru-convert --help > /dev/null
echo "✓ MinerU CLI command registered."

# Verify PyMuPDF
python -c "import fitz; print('✓ PyMuPDF (fitz) available for font extraction.')"

# Verify Presidio Analyzer, Anonymizer & spaCy model
python -c "
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()
res = analyzer.analyze('Alice Smith email: alice@example.com', language='en')
assert len(res) >= 1
print('✓ Microsoft Presidio PII analyzer and anonymizer verified.')
"

echo "=============================================================================="
echo "🎉 Setup Complete! Virtual environment is ready."
echo "=============================================================================="
echo ""
echo "To start working:"
echo "  1. Activate virtualenv:   source .venv/bin/activate"
echo "  2. Start Queue Watcher:   mineru-convert listen ./input -o ./output"
echo "  3. Start Web UI:          streamlit run app.py"
echo "  4. Run Unit Tests:        pytest -v"
echo "=============================================================================="
