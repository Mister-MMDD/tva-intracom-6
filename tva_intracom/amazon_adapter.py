"""Shim de rétrocompatibilité — amazon_adapter.py.

Ce fichier remplace l'ancien module monolithique.
Tout le code réel est dans tva_intracom/parsers/amazon/.

Les imports existants continuent de fonctionner sans modification :
    from tva_intracom.amazon_adapter import load_amazon_report, AmazonImportResult
"""

from .parsers.amazon import AmazonImportResult, load_amazon_report  # noqa: F401

__all__ = ["load_amazon_report", "AmazonImportResult"]
