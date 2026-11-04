#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Avvio del servizio backend RASPINVERTER (wrapper dell'entrypoint Flask).

Esegui dalla radice del repository:
    python scripts/run_backend.py

Imposta la working directory sulla root del repo così `config/` e `data/` restano coerenti.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    os.chdir(repo_root)
    backend_dir = repo_root / "backend"
    sys.path.insert(0, str(backend_dir))

    import inverter_api  # noqa: E402

    inverter_api.main()


if __name__ == "__main__":
    main()
