# vault/

Empty layout for a simulation vault. Each folder holds a `.gitkeep`; real contents are gitignored.

- `snapshots/`: read-only preserved versions with manifests
- `staging/`: candidates with their three results (runs, tests, validated)
- `approvals/`: approval records naming exact snapshot hashes
- `active/`: approved versions
- `archive/`: earlier approved versions
- `runtime/`: restored working copies
- `launcher/`: `vault_cli.py` and the launch allowlist format

How it works: [docs/simulation-vault.md](../docs/simulation-vault.md).
