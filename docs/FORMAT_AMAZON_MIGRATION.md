# Migration Format Amazon 3 vers Formats 4/5

## Problème

Le Format Amazon 3 ne contient pas de colonne quantité exploitable. Dans le code actuel, `_Format3Parser.qty()` retourne toujours `1`, ce qui signifie que chaque ligne est traitée comme une unité individuelle.

Cela peut entraîner une **surévaluation de l'AIC (Achat Intracommunautaire)** dans le rapport CA3, car le prix moyen par ASIN est calculé en divisant le montant total par la quantité. Si une ligne représente plusieurs unités mais que la quantité est forcée à 1, le prix moyen unitaire sera incorrectement élevé.

## Symptômes

Si vous utilisez le Format Amazon 3 et que vous voyez un avertissement dans l'onglet Téléchargements :

> ⚠️ **Format Amazon 3 avec ventes groupées détecté** : Ce format ne contient pas de colonne quantité. Le calcul de l'AIC peut être surévalué car les quantités sont forcées à 1.

## Solution recommandée

### Migrer vers le Format 4 ou 5

Amazon Seller Central permet de configurer les exports dans différents formats. Les Formats 4 et 5 incluent une colonne quantité explicite, ce qui permet un calcul précis de l'AIC.

**Étapes de migration :**

1. Connectez-vous à [Amazon Seller Central](https://sellercentral.amazon.com/)
2. Allez dans **Reports** > **Fulfillment** > **Inventory** > **Inventory Event Detail**
3. Configurez le rapport pour utiliser le **Format 4** ou **Format 5**
4. Exportez le nouveau fichier
5. Importez-le dans l'application tva-intracom

### Différences entre les formats

| Format | Quantité | Recommandé |
|--------|----------|------------|
| Format 3 | ❌ Non disponible (forcé à 1) | ❌ Déconseillé |
| Format 4 | ✅ Disponible (colonne `quantity`) | ✅ Recommandé |
| Format 5 | ✅ Disponible (colonne `quantity`) | ✅ Recommandé |

## Alternative temporaire

Si vous ne pouvez pas migrer immédiatement vers le Format 4/5 :

1. **Revérifiez manuellement l'AIC** dans le rapport CA3
2. **Calculez manuellement** le prix moyen par ASIN en utilisant vos données de vente
3. **Corrigez l'AIC** si nécessaire avant de soumettre votre déclaration

## Impact sur les calculs

Le Format 3 affecte principalement :

- **AIC (Achat Intracommunautaire)** dans le rapport CA3 France
- Le calcul du prix moyen par ASIN (`asin_avg_price` dans `excel_report.py`)
- L'estimation de l'AIC à partir des transferts de stock FBA

Les autres calculs (TVA, OSS, etc.) ne sont pas affectés car ils ne dépendent pas de la quantité par unité.

## Support

Pour toute question sur la migration des formats Amazon, consultez :

- [Amazon Seller Central Help](https://sellercentral.amazon.com/help/hub)
- La documentation officielle Amazon sur les rapports de vente
