# KOSPI/KOSDAQ Stock Analyzer

## Commands
```bash
# Install dependencies
pip install -r requirements.txt

# Run web interface
streamlit run app.py

# Run analysis directly
python main.py

# Lint code
flake8 .

# Type check
mypy .
```

## Code Style Guidelines
- **Imports**: Group standard library, external packages, local imports
- **Naming**: snake_case for variables/functions, CamelCase for classes
- **Documentation**: Functions include docstrings with param descriptions
- **Type Hints**: Include type annotations for parameters/returns
- **Error Handling**: Use try/except with specific exception types
- **Async**: Use asyncio for concurrent operations when appropriate
- **Data Cleaning**: Follow clean_markdown pattern for sanitization

## Architecture
- main.py: Core analysis logic with async processing
- app.py: Streamlit web interface
- email_sender.py: Email delivery service
- config.py: Configuration settings