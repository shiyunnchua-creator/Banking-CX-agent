# Security & API key safety

This repo is designed so that **publishing it cannot expose your Anthropic account.** Here is exactly how, and the short checklist to keep it that way.

## Why your key is safe here

- **No key is stored in any file.** The notebook asks for the key at runtime with `getpass`, which does not echo it to the screen and does not save it into the notebook. The Python package reads it from the `ANTHROPIC_API_KEY` environment variable.
- **`.env` is git-ignored.** Your real key would live in a local `.env` file, which `.gitignore` blocks from ever being committed. Only `.env.example` (placeholders only) is published.
- **No key appears in cell outputs.** No cell prints the key, so saved notebook outputs cannot leak it.

A reader who copies this repo gets the *code*, not your credentials. They would have to supply their own Anthropic key to run it — they cannot use yours.

## Before you push to GitHub — 60-second checklist

1. **Never paste your key into a code cell.** Always use the `getpass` prompt (already wired in). If you ever typed it into a cell, delete it.
2. **Clear notebook outputs before committing** (Colab: `Edit > Clear all outputs`; Jupyter: `Kernel > Restart & Clear Output`). This guarantees nothing transient is saved.
3. **Confirm `.env` is not staged.** Run `git status` — you should NOT see `.env` listed. You should only see `.env.example`.
4. **Scan for accidental keys** before your first push:
   ```bash
   git grep -nE "sk-ant-[A-Za-z0-9_-]{20,}" || echo "clean - no keys found"
   ```
   If that prints anything other than "clean", remove the key and rotate it.
5. **Do NOT publish the original lecture notebook** `Agentic AI Implementation Session.ipynb` — it contains a hard-coded OpenAI key. Keep it out of the repo entirely.

## If a key ever leaks

Rotate it immediately in the [Anthropic Console](https://console.anthropic.com/) (delete the exposed key, create a new one). Rotating makes the leaked key useless. Removing a key from a later commit does **not** remove it from git history, so rotation is the only reliable fix.

## Tip: keep the key out of your shell history too

Prefer a `.env` file (loaded by `python-dotenv`) over `export ANTHROPIC_API_KEY=...` on the command line, so the key never lands in your shell history.
