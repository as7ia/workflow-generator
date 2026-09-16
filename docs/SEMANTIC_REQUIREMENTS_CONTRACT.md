# Contrat des exigences sémantiques

`normalize_semantic_requirements()` produit toujours les huit collections suivantes :

- `participants`: objets `{name}` dédupliqués par identité textuelle normalisée;
- `lanes`: objets `{name, participant}`;
- `timers`: objets `{kind, value, activity?}`, avec `kind` parmi `duration`, `date`, `cycle` et une valeur ISO 8601;
- `parallelisms`: objets `{branches: [{activity}, ...]}` comportant au moins deux branches distinctes;
- `decisions`: objets `{name, outcomes}` comportant au moins deux outcomes distincts;
- `interactions`: objets `{source, target, sourceLane?, targetLane?}`;
- `activities`: objets `{name}`;
- `ambiguities`: objets non bloquants décrivant les informations incomplètes ou incertaines.

La normalisation ignore casse, accents, ponctuation, espaces, pluriels simples et variation
infinitif/nom en `-er`/`-e`. Les requirements équivalents sont dédupliqués après cette
normalisation. Une exigence incomplète n'est jamais transformée en erreur de coverage : elle
devient une ambiguity et produit uniquement `SEMANTIC_AMBIGUITY` avec `severity=warning`.

La couverture utilise exclusivement les noms de participants, jamais leurs IDs ni les lanes.
Les lanes sont vérifiées sous leur participant. Les timers exigent un
`intermediateCatchEvent` et un couple `kind/value` identique. Les décisions exigent un gateway
nommément compatible et des branches distinctes compatibles avec les outcomes. Les
parallelismes exigent des activités attendues sur des branches réellement distinctes. Les
interactions sont résolues depuis les endpoints des `messageFlows`, leurs `participantRef` et,
lorsqu'ils sont exigés, leurs contextes de lane.

Les diagnostics absents contiennent `related_node_ids` avec les candidats les plus proches,
sans considérer ces candidats comme une preuve de couverture.
