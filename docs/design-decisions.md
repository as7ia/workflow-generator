# Décisions de conception

- `schemaVersion` permet l'évolution du contrat.
- Les participants sont séparés pour éviter la duplication.
- Tous les éléments BPMN sont rangés dans `nodes`.
- Les connexions sont séparées dans `flows`.
- `properties` reste extensible pour Flowable.
- `ambiguities` empêche Qwen d'inventer les informations manquantes.
- JSON Schema valide la structure ; Python valide la cohérence du graphe.
