# Security Policy

## Reporting a Vulnerability

Please report security issues privately to **pritampkp15@gmail.com** with reproduction steps and affected versions rather than opening a public GitHub issue.

## Sandbox Model

AutoHarness executes trial setups and shell tools inside temporary per-trial directories (`Sandbox`) that enforce path-escape checks on `read_file`/`write_file`/`list_files`. Note that shell commands invoked via `run_bash` or `CommandAgent` execute with your user's OS privileges; run untrusted third-party cases inside an isolated container or VM.
