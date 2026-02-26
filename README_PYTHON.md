# 8th Grader Python Assistant (CLI)

The CLI version of the lazy 8th grader assistant, mimicking the web IDE functionality.

## Installation

1.  **Install requirements**:
    ```bash
    pip install -r requirements.txt
    ```

## Usage

### 1. Interactive Chat
Start a conversation with the lazy 8th grader:
```bash
python assistant.py chat
```

### 2. Code Completion
Provide a file to get suggestions based on its content:
```bash
python assistant.py complete main.py
```

## Persona Rules
- Uses simple variables (`a`, `b`, `x`).
- Lazy code (repetitive, simple).
- No unnecessary imports or complex optimizations.
- No markdown formatting in output.
