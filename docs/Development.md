# Development

## Test Suite

Run the standard tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run coverage:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing --cov-report=xml --cov-report=html
```

The verified baseline for this branch is:

- Python 3.14.6
- 85 passed
- 0 failed
- 0 skipped
- 407 warnings
- 85% coverage

The warnings are FastAPI/Starlette deprecation warnings under Python 3.14, not application test failures.

## Dependencies

Install runtime dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Install development dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## CI

GitHub Actions runs the full pytest suite with coverage on Windows and uploads `htmlcov/` as an artifact.
