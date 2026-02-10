# Filesystem Skill

Tool name: `filesystem`

Use for reading, writing, listing, and deleting files under the configured workspace.

Guidelines:
- Prefer `list_dir` before deep reads.
- Use exact relative paths within workspace.
- For destructive actions, ensure user intent is explicit.
