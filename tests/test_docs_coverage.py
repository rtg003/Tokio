from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def _leaf_keys(obj: Any, prefix: str = "") -> list[str]:
    if isinstance(obj, dict):
        out: list[str] = []
        for key, value in obj.items():
            out.extend(_leaf_keys(value, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return [prefix]


def test_discovery_v9_documents_every_config_key() -> None:
    """Trava permanente da diretiva humana: variável sem doc quebra a suíte."""
    cfg = yaml.safe_load(Path("config/discovery_config.yaml").read_text())
    doc = Path("docs/discovery_logic_v9.md").read_text()
    # Cobertura da tabela canônica, não de qualquer bloco de código no doc.
    documented = set(re.findall(r"^\| `([^`]+)` \|", doc, flags=re.MULTILINE))
    missing = [key for key in _leaf_keys(cfg) if key not in documented]
    assert missing == []
