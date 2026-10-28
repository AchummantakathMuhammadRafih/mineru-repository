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
echo "⚡ [3/6] Installing PyTorch, NumPy & GPU/CPU Drivers"
echo "=============================================================================="
# Detect NVIDIA GPU and CUDA driver version
if command -v nvidia-smi &> /dev/null; then
    DRIVER_CUDA_VER=$(nvidia-smi | grep -oP 'CUDA Version:\s*\K[0-9]+' | head -n 1 || echo "12")
    echo "✓ NVIDIA GPU detected (CUDA version ~$DRIVER_CUDA_VER)."
    if [ "$DRIVER_CUDA_VER" -ge 12 ]; then
        echo "Installing CUDA 12.1 PyTorch wheel (compatible with CUDA 12.1 - 12.6+)..."
        pip install --upgrade --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
    else
        echo "Installing CUDA 11.8 PyTorch wheel for legacy CUDA drivers..."
        pip install --upgrade --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
    fi
else
    echo "ℹ No GPU detected. Installing CPU-optimized PyTorch..."
    pip install --upgrade --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
fi

# Pin NumPy 1.x before installing downstream packages to avoid NumPy 2.x C-API ABI mismatch
echo "Pinning stable NumPy 1.26.x..."
pip install --upgrade "numpy>=1.26.0,<2.0.0"

echo "=============================================================================="
echo "📦 [4/6] Installing Project Dependencies & NLP Models"
echo "=============================================================================="
# Install project dependencies from requirements.txt, then editable package
pip install -r requirements.txt
pip install -e ".[dev]" --no-deps

# Ensure Microsoft Presidio NLP model (spaCy en_core_web_sm) is installed
echo "Downloading spaCy English model for Presidio PII detection..."
python -m spacy download en_core_web_sm

# Ensure runtime input and output directories exist
mkdir -p input output

echo "=============================================================================="
echo "🤖 [5/6] Pre-downloading MinerU Pipeline Model Weights"
echo "=============================================================================="
# Try positional syntax first, then flag syntax, and support ModelScope mirror
if ! mineru-models-download -s huggingface pipeline 2>/dev/null; then
    if ! mineru-models-download --source huggingface --model_type pipeline 2>/dev/null; then
        echo "ℹ HuggingFace download unavailable or rate-limited; trying ModelScope mirror..."
        mineru-models-download -s modelscope pipeline 2>/dev/null || \
        mineru-models-download --source modelscope --model_type pipeline 2>/dev/null || \
        echo "⚠️ Note: Model download can be completed on first run or manually via mineru-models-download."
    fi
fi

echo "=============================================================================="
echo "🔍 [6/6] Verifying Component Integrations"
echo "=============================================================================="
# Verify MinerU version & CLI
python -c "
import importlib.metadata
try:
    ver = importlib.metadata.version('mineru')
    print(f'✓ MinerU installed successfully: version {ver}')
except Exception:
    import mineru
    print('✓ MinerU package loaded.')
"

# Verify PyTorch & CUDA availability
python -c "
import torch
print(f'✓ PyTorch version: {torch.__version__} (CUDA available: {torch.cuda.is_available()})')
if torch.cuda.is_available():
    print(f'✓ GPU device: {torch.cuda.get_device_name(0)}')
"

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
echo "  4. Start HTTP Server:     python server.py"
echo "  5. Run Unit Tests:        pytest -v"
echo "=============================================================================="

