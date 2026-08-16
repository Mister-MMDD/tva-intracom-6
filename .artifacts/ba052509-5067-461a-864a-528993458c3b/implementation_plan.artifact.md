# Fix Gating for VIES Reclassifications Tables

The user reported that the "N° TVA rejeté" table is not locked for free accounts in the VIES tab, even though it should be.
Investigation revealed that:
1. The main "N° TVA rejeté" table (`true_rejections`) is correctly calling `_gated_preview_table`, but it relies on `exclude_safe_cols` instead of `lock_all=True`.
2. The secondary "Identifiant national NIF" table (`national_ids`) is NOT gated at all; it uses `st.dataframe` directly, leaking sensitive VAT/NIF numbers to free users.
3. The user mentioned that "none of the default safe columns exist in this table," suggesting they expect all columns to be locked for free users.

## Proposed Changes

### [VIES UI Component]

#### [MODIFY] [vies_ui.py](file:///D:/Utilisateurs/matth/Visual Studio projets/tva-intracom 6/tva_intracom/ui/tabs/vies_ui.py)
- Update the `true_rejections` table call to use `lock_all=True` instead of `exclude_safe_cols`. This is more explicit and ensures all columns are locked regardless of their names matching default safe ones.
- Replace the direct `st.dataframe` call for the `national_ids` table with `_gated_preview_table` to protect sensitive data for free accounts.

## Verification Plan

### Manual Verification
- Log in with a free account (or simulate `can_export=False`).
- Navigate to the VIES tab with a file containing reclassifications (both B2B rejections and national IDs).
- Verify that both the "N° TVA rejeté" table and the "Identifiant national NIF" table show only the first 5 rows, with all sensitive columns masked (🔒 Gated).
- Verify that the warning message about gated preview is displayed.
