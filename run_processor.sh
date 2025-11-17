#!/bin/bash

# Processing Runner
# Loads environment and runs processor

set -e

# Load environment variables from .env file
if [ -f .env ]; then
    echo "Loading environment variables from .env..."
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "Error: .env file not found!"
    echo "Please copy .env.example to .env and fill in your credentials."
    exit 1
fi

# Check required environment variables
required_vars=(
    "DATABASE_URL"
    "R2_ACCESS_KEY_ID"
    "R2_SECRET_ACCESS_KEY"
    "R2_ENDPOINT"
    "HUGGINGFACE_API_KEY"
)

missing_vars=()
for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        missing_vars+=("$var")
    fi
done

if [ ${#missing_vars[@]} -gt 0 ]; then
    echo "Error: Missing required environment variables:"
    printf '  - %s\n' "${missing_vars[@]}"
    echo ""
    echo "Please update your .env file with the required values."
    exit 1
fi

echo "All required environment variables are set."
echo ""

# Check if Python dependencies are installed
if ! python3 -c "import psycopg2, boto3" 2>/dev/null; then
    echo "Installing Python dependencies..."
    pip3 install -r requirements.txt
fi

# Run the processor
echo "Starting video processor..."
echo "================================"
python3 process_videos.py
