# Token-Optimized Development Workflow

## Subagent Execution Rules
- Use the `@developer` subagent to write or modify codebase files.
- Use the `@reviewer` subagent exclusively for read-only static analysis and auditing.
- Before executing a multi-step task, provide a written plan to save token exploration loops.

## Token Control Commands
- When the active conversation history reaches 15–20 messages, run the `/compact` command to compress the context.
- If switching to an entirely unrelated task, run `/clear` immediately to wipe the context window clean.
