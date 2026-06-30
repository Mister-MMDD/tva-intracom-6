"""Sous-package parsers/amazon/ — chargement des rapports Amazon VAT Transactions.

Interface publique : identique à l'ancien amazon_adapter.py.
  from tva_intracom.parsers.amazon import load_amazon_report, AmazonImportResult
"""

from .loader import AmazonImportResult, load_amazon_report

__all__ = ["load_amazon_report", "AmazonImportResult"]
