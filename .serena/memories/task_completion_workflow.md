# Task Completion Workflow

## Before Committing Code
1. **Run Code Quality Checks**:
   ```bash
   uv run ruff check --fix .              # Fix auto-fixable issues
   uv run black .                         # Format code
   uv run pre-commit run --all-files      # Run all hooks
   ```

2. **Test the Application**:
   ```bash
   uv run streamlit run app.py            # Verify app starts correctly
   # Manual testing of core functionality
   ```

3. **Check Git Status**:
   ```bash
   git status                             # Review changed files
   git diff                               # Review changes
   ```

## Commit Process
1. **Stage Changes**:
   ```bash
   git add <files>                        # Stage specific files
   ```

2. **Commit with Conventional Format**:
   ```bash
   git commit -m "type(scope): description"
   ```
   
   **Types**: feat, fix, docs, chore, refactor, test, ci
   **Examples**:
   - `feat(transcribe): add sentence completion logic`
   - `fix(ui): resolve caption overlap with controls`
   - `docs(readme): update setup instructions`

## Post-Commit Verification
1. **Verify Streamlit Server**:
   - Check app starts without errors
   - Test core transcription functionality
   - Verify UI responsiveness

2. **Monitor Background Processes**:
   - Check for any running Streamlit instances
   - Kill orphaned processes if needed

## Deployment Considerations
- Export requirements for Docker: `uv export -o requirements.txt`
- Verify environment variables are properly configured
- Test AWS credentials and service connectivity
- Ensure proper error handling for network issues

## Common Issues to Check
- AWS credential configuration
- Audio device permissions in browser
- WebSocket connection stability
- UI layout and responsiveness
- Translation accuracy and latency