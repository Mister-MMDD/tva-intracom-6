# Module `tva_intracom.ui.tabs.context`

## Description
Module de contexte partagé entre les onglets de l'interface Streamlit. Définit la structure `TabContext` qui contient toutes les données partagées entre les onglets (résultats calculés, résumé, état du gating billing, etc.). Utilise `st.session_state["_tab_ctx"]` pour éviter les fuites mémoire liées aux arguments de fragments.

## Structure de données

### `TabContext` (dataclass)
**Description courte** : Contexte partagé entre tous les onglets de l'interface.

**Description détaillée :**
- Contient toutes les données calculées par le moteur TVA
- Contient l'état du gating billing (quotas, abonnements)
- Contient les métadonnées (période, pays, etc.)
- Stocké dans `st.session_state["_tab_ctx"]`
- Construit une fois avant le rendu des onglets
- Partagé par tous les onglets via lookup dans session_state

**Champs :**

#### Résultats calculés
- `results` (list) : Liste des VatResult des ventes
- `refund_results` (list) : Liste des VatResult des remboursements
- `summary` (ReportSummary) : Résumé du rapport
- `vies_summary` (ViesValidationSummary) : Résumé des validations VIES
- `oss_summary` (dict) : Résumé de l'agrégation OSS

#### Métadonnées
- `period_label` (str) : Période fiscale (ex: "2026-01")
- `period_detected_range` (tuple | None) : Borne de période détectée (start, end)

#### Gating billing
- `can_export` (bool) : Permission d'exporter
- `billing_ok` (bool) : Statut billing OK
- `account_link_blocked` (bool) : Compte bloqué (rattachement manquant)
- `gated_download` (callable) : Fonction de gating pour les téléchargements
- `unlock_label_suffix` (str) : Suffixe pour les labels de déblocage

#### Données entreprise
- `nom_entreprise` (str) : Nom de l'entreprise
- `siren_entreprise` (str) : SIREN de l'entreprise
- `tva_fr` (str) : Numéro TVA FR
- `countries_with_vat` (list) : Pays avec immatriculation TVA
- `local_vat_numbers` (dict) : Numéros TVA locaux par pays

#### Données complémentaires
- `all_fc_transfers` (list) : Liste des transferts FBA
- `all_invoice_credit_notes` (list) : Liste des factures/avoirs
- `oss_tva_net_total` (Decimal | None) : Total TVA OSS net (calculé par l'onglet Déclarations)

#### VIES
- `vies_scope_id` (str) : ID du scope VIES

## Dépendances inter-onglets

### Couplage intentionnel
L'onglet "Téléchargements" dépend de l'onglet "Déclarations" :
- `ctx.oss_tva_net_total` est écrit par `render_declarations()`
- `render_telechargements()` lit cette valeur
- L'ordre des onglets dans `app.py` est donc important

### Vérification
Une assertion dans `render_telechargements()` vérifie que `ctx.oss_tva_net_total` n'est pas None :
```python
assert ctx.oss_tva_net_total is not None, (
    "ctx.oss_tva_net_total est None : render_declarations(ctx) doit être "
    "appelé avant render_telechargements() dans app.py (voir tabs/context.py)."
)
```

## Utilisation

### Construction du contexte
Le contexte est construit dans `app.py` avant le rendu des onglets :
```python
ctx = TabContext(
    results=results,
    refund_results=refund_results,
    summary=summary,
    # ... autres champs
)
st.session_state["_tab_ctx"] = ctx
```

### Lecture du contexte dans un onglet
Chaque onglet lit le contexte depuis session_state :
```python
@st.fragment
def render_telechargements() -> None:
    ctx: TabContext = st.session_state["_tab_ctx"]
    results = ctx.results
    # ... utilisation des données
```

### Pourquoi session_state ?
Utiliser `st.session_state["_tab_ctx"]` au lieu de passer le contexte en paramètre :
- Évite les fuites mémoire liées à la rétention des arguments par Streamlit
- Permet le partage entre onglets sans duplication
- Compatible avec les fragments (`@st.fragment`)

## Risques

### Ordre des onglets
Si l'ordre des onglets change dans `app.py`, le couplage `render_declarations` → `render_telechargements` peut casser.
**Mitigation** : L'assertion dans `render_telechargements` détecte ce cas et lève une erreur explicite.

### Mutation du contexte
Le contexte ne doit pas être muté après sa construction.
**Mitigation** : Utiliser des dataclasses frozen ou documenter clairement les champs read-only.

## Configuration requise

- Aucune configuration spécifique requise
- Dépend de Streamlit pour session_state
