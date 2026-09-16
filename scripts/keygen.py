#!/usr/bin/env python3
"""Generate an ed25519 keypair for signing skill packages.

Usage:
    python scripts/keygen.py --out ~/.config/skill-exchange/key

The private key is written with 0600 permissions and NEVER leaves your
machine. The public key is printed -- paste it into your publish calls and
share it freely; it goes into every skill version you sign.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.signing import generate_keypair


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a skill-signing keypair")
    parser.add_argument("--out", required=True,
                        help="Path to write the PRIVATE key (0600)")
    args = parser.parse_args()

    private_hex, public_hex = generate_keypair()

    out = os.path.expanduser(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as fh:
        fh.write(private_hex + "\n")
    os.chmod(out, 0o600)

    print(f"private key written to {out} (keep it secret, keep it safe)")
    print()
    print("PUBLIC KEY (share freely, include with every publish):")
    print(public_hex)


if __name__ == "__main__":
    main()
