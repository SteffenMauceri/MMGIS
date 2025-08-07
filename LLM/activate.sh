#!/bin/bash
# Activation script for the sea_ice project
# Usage: source activate.sh

echo "Activating sea_ice conda environment..."
conda activate sea_ice

if [ $? -eq 0 ]; then
    echo "✅ sea_ice environment activated successfully!"
    echo "📁 Current directory: $(pwd)"
    echo "🐍 Python version: $(python --version)"
    echo "📦 OpenAI package: $(pip show openai | grep Version)"
    echo ""
    echo "Ready to run your project! Try: python test.py"
else
    echo "❌ Failed to activate sea_ice environment"
    echo "Make sure the environment exists: conda env list"
fi
