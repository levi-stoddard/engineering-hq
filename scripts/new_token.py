"""Create an approver token. Run it yourself, alone at the terminal.

    python scripts/new_token.py

Prints a new random token once, and the SHA-256 that goes into the config (or into the
HQ_<ROLE>_TOKEN_SHA256 environment variable). Keep the token in your password manager. The
supervisor never stores it, and it is never typed into chat, a file or a command line.
"""

import hashlib
import secrets

token = secrets.token_urlsafe(32)
print(f"token (shown once, keep it in your password manager): {token}")
print(f"token_sha256 (goes in the config):                     {hashlib.sha256(token.encode()).hexdigest()}")
