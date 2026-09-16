# Validation end-to-end réelle

## Campagne

Le runner [`scripts/run_e2e_real_workflows.py`](../scripts/run_e2e_real_workflows.py) couvre dix descriptions indépendantes du code métier : workflow linéaire, décision exclusive, parallélisme, timer ISO, boucle, trois participants, plusieurs lanes, collaboration avec décision et timer, workflow de vingt activités et ambiguïtés volontaires.

Il charge Qwen une seule fois, puis crée un `WorkflowCompiler` neuf par scénario. Chaque exécution continue même après un échec et produit un rapport compact sans prompt complet. Les longueurs de prompts, compteurs d'appels Qwen et compteurs de réparations sont conservés.

## Validations automatiques

Pour une compilation réussie, le runner contrôle :

- présence du workflow, des process et des nodes ;
- absence d'erreurs bloquantes schema, semantic et coverage ;
- cohérence des ownerships participant/lane/process ;
- confinement des sequence flows et séparation inter-process des message flows ;
- XML bien formé et références XML résolues ;
- présence des BPMNShape et BPMNEdge attendus ;
- métriques de collision du layout et du routing.

Les scénarios métier eux-mêmes restent exclusivement dans le runner E2E.

## Résultat de la machine courante

Date de campagne : 16 septembre 2026.

La suite unitaire préalable est valide : **355 tests passés**.

La campagne réelle s'arrête au chargement du modèle, avant tout appel Qwen :

```text
ModuleNotFoundError: No module named 'torch'
Transformers n'est pas installé. Exécutez 'pip install -r requirements.txt'.
```

`nvidia-smi` n'est pas disponible sur cette machine. Cet échec est classé `GENERATION / MODEL_LOAD_FAILED`, et non comme une régression du compilateur.

| Mesure | Résultat |
|---|---:|
| Scénarios préparés | 10 |
| Réussis | 0 |
| Échoués au chargement | 10 |
| Appels Qwen moyens/max | 0 / 0 |
| Repairs exécutés | 0 |
| BPMN produits | 0 |

Les rapports d'échec sont disponibles sous `outputs/e2e/<scenario>/report.json`, avec `workflow_last_good.json`. Le résumé global est `outputs/e2e/summary.json`.

## Échecs observés et cause racine

Tous les scénarios ont le même stage exact : `GENERATION`, avant extraction des requirements. Les compteurs avant/après restent à zéro et aucun dernier workflow valide n'existe. La cause racine est l'absence des dépendances d'inférence locales, pas une erreur de normalisation, préparation, structure, coverage, graphe, layout, routing ou BPMN.

Conformément au protocole, aucune modification du moteur n'a été faite pour contourner cet échec d'environnement.

## Exécution sur une machine Qwen

Après installation de PyTorch compatible avec le matériel et des dépendances du projet :

```bash
python scripts/run_e2e_real_workflows.py
```

Quantification optionnelle :

```bash
python scripts/run_e2e_real_workflows.py --load-in-4bit
```

Un scénario peut être repris isolément sans écraser les autres :

```bash
python scripts/run_e2e_real_workflows.py --scenario linear_simple
```

## Limites et recommandations

- Installer une version de PyTorch adaptée au GPU, puis `transformers`, `accelerate` et éventuellement `bitsandbytes`.
- Relancer les dix scénarios avec le modèle `Qwen/Qwen3-4B` disponible localement ou téléchargeable.
- Examiner les rapports avant toute correction si un scénario atteint ensuite un stage `NORMALIZATION`, `PREPARATION`, `STRUCTURAL`, `COVERAGE`, `GRAPH`, `LAYOUT`, `ROUTING` ou `BPMN`.
- Ne corriger le moteur qu'après reproduction d'une cause générique ; les fixtures ne doivent jamais devenir des règles métier.
