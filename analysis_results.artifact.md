# Analyse de Cinquième Passe - Moteur TVA Intracom

Voici la liste des bugs identifiés lors de cette investigation poussée sur les cas limites de reporting, les délais légaux et les mécanismes de synchronisation UI.

## Bugs de Haute Priorité (Impact fiscal et conformité)

### 1. Loophole critique du Seuil OSS (Avoirs)
Dans `_run_oss_loop`, le cumul OSS est net (ventes + avoirs).
- **Problème** : Si un remboursement important fait repasser le cumul net sous les 10 000 €, les ventes suivantes de la même année sont reclassées en `DOMESTIC`.
- **Règle légale** : Une fois franchi, le seuil est définitivement dépassé pour le reste de l'année civile. Un avoir ne permet pas de "revenir en arrière" fiscalement (Art. 59 quater Dir. 2006/112/CE).
- **Impact** : Risque de redressement fiscal majeur pour application erronée de la TVA d'origine au lieu de la TVA de destination.

### 2. Double comptage du CA HT dans le Dashboard
Dans `declarations.py`, les ventes en régime DDP vers le pays d'origine (ex: vendeur FR livrant un client FR depuis un stock DE) sont comptées deux fois.
- **Cause** : Elles apparaissent dans le total `FR_DOMESTIC` ET dans le total DDP.
- **Impact** : Le CA HT net total affiché est sur-évalué, faussant la vision de l'activité.

### 3. Bug EMEBI : Délais légaux erronés
Dans `excel_report.py`, le calcul de la date limite Intrastat (10e jour ouvré) ignore les jours fériés.
- **Problème** : Le code ne retire que les samedis et dimanches. Si un jour férié tombe en semaine (ex: 1er mai, 8 mai), la date limite calculée est fausse.
- **Impact** : L'utilisateur risque une pénalité pour dépôt tardif s'il se fie aveuglément au calendrier généré.

### 4. AIC Monaco ignorée dans le rapport CA3
Les transferts de stock FBA vers ou depuis Monaco sont sautés dans `ca3_report.py` car le code compare les pays bruts (`arr != seller_country`) au lieu d'utiliser `fiscal_equivalent_country`.
- **Impact** : Sous-évaluation de la base AIC et de la TVA auto-liquidée sur la déclaration CA3 française pour les vendeurs stockant à Monaco.

### 5. Bug de valorisation AIC persistant (ca3_report.py)
Alors que le bug du "prix moyen par ligne" a été corrigé dans l'export Excel, la fonction `_asin_avg_price_and_category` dans `ca3_report.py` contient toujours la version fautive.
- **Impact** : Les montants AIC de la CA3 restent massivement sur-évalués par rapport à l'onglet Audit de l'Excel.

---

## Bugs Logiques et Graphiques

### 6. Perte de données dans le graphique "TVA par pays"
Dans `visualisations.py`, la fusion des dictionnaires `{**oss, **local}` écrase les valeurs si un pays est présent dans les deux canaux.
- **Impact** : Pour un pays comme l'Allemagne (souvent à la fois en OSS et en Local), l'un des deux montants de TVA disparaît du graphique.

### 7. Graphique Circulaire (Pie) trompeur
Le graphique compare votre TVA **nette** (ventes - avoirs) avec la TVA Amazon **brute** (ventes uniquement).
- **Impact** : La part de TVA collectée par Amazon paraît artificiellement plus grosse qu'elle ne l'est réellement par rapport à la vôtre.

### 8. Désynchronisation des Toggles (UX)
Dans la sidebar, certains toggles sont forcés par le code (ex: désactiver le seuil OSS si le flag N-1 est actif) mais l'état graphique du bouton ne change pas. L'utilisateur voit le bouton sur "ON" alors que le système traite la valeur comme "OFF".

---

## Sécurité et Fiabilité

### 9. Persistance de session post-déconnexion
La fonction `get_user_by_session_token` est mise en cache pour 1 heure (`ttl=3600`).
- **Risque** : Cliquer sur "Déconnexion" ne vide pas ce cache. La session reste utilisable pendant 60 minutes si un attaquant possède le jeton.

### 10. Bug de réactivité post-paiement (PAYG)
Le paramètre `export_ok=1` ne vide pas le cache `_cached_db_read`.
- **Impact** : Un utilisateur PAYG peut rester bloqué devant le paywall jusqu'à 20 secondes après son retour de Stripe, alors qu'il a déjà payé.

---

## Recommandations de Correction

1.  **Priorité 1** : Fixer le 10k OSS loophole (flag `ever_exceeded`) et le double comptage Dashboard.
2.  **Priorité 2** : Harmoniser le calcul du prix moyen dans `ca3_report.py` et intégrer Monaco dans l'AIC.
3.  **Priorité 3** : Ajouter `get_user_by_session_token.clear()` dans la logique de déconnexion.
4.  **Priorité 4** : Utiliser un calendrier des jours fériés pour les deadlines EMEBI.
