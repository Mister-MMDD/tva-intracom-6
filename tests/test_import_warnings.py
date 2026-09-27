from tva_intracom.ui.import_warnings import warning_to_table_row


def test_warning_to_table_row_extracts_file_line_reference_and_message():
    row = warning_to_table_row(
        "amazon-refunds.tsv :: Ligne du fichier 8 — "
        "commande/remboursement ORD-2024-002 : refund à montant nul (0 €) — "
        "conservée dans le rapport, à vérifier."
    )

    assert row == {
        "file": "amazon-refunds.tsv",
        "line": "8",
        "reference": "ORD-2024-002",
        "warning": "refund à montant nul (0 €) — conservée dans le rapport, à vérifier.",
    }


def test_warning_to_table_row_keeps_generic_warning_and_file():
    row = warning_to_table_row(
        "orders.csv :: Taux BCE indisponible pour 2 ventes."
    )

    assert row == {
        "file": "orders.csv",
        "line": "",
        "reference": "",
        "warning": "Taux BCE indisponible pour 2 ventes.",
    }
