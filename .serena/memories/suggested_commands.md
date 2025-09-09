# Essential Development Commands

## Environment Setup
```bash
uv init --python 3.11                    # Initialize new project
uv add streamlit boto3 python-dotenv     # Add dependencies
uv sync                                   # Install dependencies
```

## Development Workflow
```bash
uv run streamlit run app.py              # Run development server
uv run pytest -q                         # Run tests (when available)
```

## Code Quality & Formatting
```bash
uv run ruff check .                       # Check linting issues
uv run ruff check --fix .                 # Fix auto-fixable linting issues
uv run black .                            # Apply black formatting
uv run pre-commit run --all-files         # Run all pre-commit hooks
uv run pre-commit install                 # Install pre-commit hooks (one-time)
```

## Docker Deployment
```bash
uv export -o requirements.txt             # Export dependencies for container
docker build -t realtime-caption .
docker run --rm -p 8501:8501 -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... realtime-caption
```

## System Commands (Darwin/macOS)
- `ls -la`: List files with details
- `find . -name "*.py"`: Find Python files
- `grep -r "pattern" .`: Search for text patterns
- `git status`: Check git status
- `git log --oneline`: View commit history

## Environment Variables
- `AWS_ACCESS_KEY_ID`: Required for AWS authentication
- `AWS_SECRET_ACCESS_KEY`: Required for AWS authentication  
- `AWS_REGION`: Optional, defaults to us-east-1