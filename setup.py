from setuptools import setup, find_packages

setup(
    name="mineru-converter",
    version="1.0.0",
    description="Modular CLI and Python framework for high-fidelity PDF to MinerU conversion.",
    author="Muhammad Rafih",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "mineru>=3.0.0",
        "click>=8.0.0",
        "rich>=13.0.0",
        "pydantic>=2.0.0",
        "pypdf>=3.0.0",
        "pymupdf>=1.23.0",
        "streamlit>=1.30.0",
        "presidio-analyzer>=2.2.0",
        "presidio-anonymizer>=2.2.0",
        "spacy>=3.7.0",
        "fastapi>=0.100.0",
        "uvicorn>=0.20.0",
        "python-multipart>=0.0.6",
    ],
    extras_require={
        "dev": [
            "pytest>=8.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "mineru-convert=mineru_converter.cli.app:cli",
            "mineru-converter=mineru_converter.cli.app:cli",
            "mineru-server=server:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
)
