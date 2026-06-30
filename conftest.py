"""Ajoute la racine du depot au sys.path pour permettre l'import du package
sans installation (utile pour `pytest` en local et en CI)."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
