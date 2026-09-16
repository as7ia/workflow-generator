# Audit legacy et passage à l'échelle

## Code supprimé

- `WorkflowCompiler._apply_safe_repairs`: l'ancienne implémentation d'environ 300 lignes
  était placée après un `return CanonicalWorkflowPreparer.apply_safe_repairs(...)`. Aucun
  appel ne pouvait donc l'atteindre. Les helpers exclusivement utilisés par ce bloc
  (`_explicit_node_participants`, `_to_canonical_schema_flows`) ont également été retirés.
- `WorkflowCompiler._validate_schema`: aucune référence dans `src` ou `tests`; la validation
  effective passe par `CanonicalWorkflowPreparer`.
- `WorkflowRepairer.build_repair_prompt`: l'ancien constructeur de prompt était situé après
  les retours vers les builders canoniques de `generation.prompts`.
- `WorkflowRepairer._validate` et ses imports dédiés: aucune référence; la préparation et les
  validations passent par `CanonicalWorkflowPreparer`.

La suppression ne change pas le chemin actif : génération → normalisation → préparation et
validation → repair structurel → coverage → analyse → BPMN.

## Code conservé

- La migration v1 de `WorkflowNormalizer` reste utilisée par l'API publique, la conversion
  JSON et plusieurs tests; elle n'est pas legacy inatteignable.
- `CanonicalWorkflowPreparer.prepare` reste un alias public rétrocompatible.
- Les fallbacks d'import `PYTHONPATH=src` restent nécessaires aux scripts exécutables.
- `_StandardSchemaValidator._visit` reste récursif. Sa profondeur suit la profondeur du JSON
  Schema et des objets imbriqués, pas le nombre de nodes/flows; le modèle canonique a une
  profondeur bornée et faible.
- Le matching biparti de coverage conserve une petite récursion sur le nombre de branches.
  Il n'est pas modifié dans cette phase et ne parcourt pas la profondeur du graphe.

## Traversées rendues itératives

| Fonction | Avant | Après | Complexité |
|---|---|---|---|
| `WorkflowGraphAnalyzer._scc` | Tarjan récursif | Kosaraju itératif | O(V + E) |
| `WorkflowGraphAnalyzer._back_edges` | DFS récursif | pile de frames explicite | O(V + E) |
| `SemanticValidator._validate_cycles` | DFS récursif | pile de frames et index de chemin | O(V + E), hors matérialisation des cycles |
| `finalize_collaboration_process_structure` | rappel récursif jusqu'à stabilité | au plus une seconde passe explicite bornée | dépend des processus et interactions |

Les reachability walks, composants faibles, distances, collecte de branches et niveaux étaient
déjà itératifs. Les niveaux utilisent désormais un tas au lieu de trier la file après chaque
insertion. Les composants faibles n'appellent plus `min(unseen)` pour chaque composant.

## Hotspots et limites restantes

- Construction des index nodes/flows/messages : O(V + E + M).
- SCC, reachability, cycles et condensation : O(V + E).
- Analyse des branches : O(S × (V + E)) pour `S` gateways de split; coût inhérent à la
  production détaillée de chaque branche.
- Validation géométrique du layout : O(V²), conservée car Layout est hors périmètre.
- Routing : comparaison aux obstacles potentiellement O(E × V), conservée hors périmètre.
- Déduplication des message flows utilise maintenant une clé JSON canonique et passe de O(M²)
  à O(M) en nombre de messages, hors coût de sérialisation.

Les tests synthétiques couvrent une chaîne de 3000 nodes, un cycle de 1500 nodes, plusieurs
SCC, quatre processus de 500 nodes et 1000 messageFlows. Ils vérifient l'absence de
`RecursionError` sans modifier les limites globales de récursion Python.
