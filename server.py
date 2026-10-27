#!/usr/bin/env python
"""Top-level entrypoint to run the MinerU Document Processing Server."""

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="MinerU PDF Parser Async Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    print(f"🚀 Starting MinerU Conversion Server on http://{args.host}:{args.port}")
    print(f"📄 Web UI: http://localhost:{args.port}/")
    print(f"📖 Swagger Docs: http://localhost:{args.port}/docs")
    print("⚙️ Worker: Async Sequential FIFO Queue (MinerU -> Hierarchy Correction -> Presidio Redaction)")

    uvicorn.run(
        "mineru_converter.server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
