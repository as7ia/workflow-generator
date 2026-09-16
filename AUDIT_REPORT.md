# Audit complet du générateur de workflows

Date de l'audit : 2026-09-16

## 1. Résumé exécutif

Le projet implémente bien les principales couches attendues : extraction sémantique, génération Qwen, parsing JSON, normalisation v2, réparations déterministes, validations, réparation par modèle, analyse de graphe, layout, routing et sérialisation BPMN 2.0.

La suite automatisée est étendue et couvre les principaux scénarios sans GPU. Aucun défaut P0 reproductible n'a été trouvé pendant l'audit. Les risques les plus importants sont :

- deux normalisations successives dans le chemin initial ;
- des réparations présentées comme sûres mais fondées sur des heuristiques lexicales ou causales ;
- un contrôle de coverage trop permissif pour les décisions et incomplet pour les lanes d'interaction et le `kind` des timers ;
- jusqu'à six générations Qwen après les deux générations initiales, avec des prompts structurels contenant le workflow et le schéma complets ;
- une incohérence entre la CLI, qui accepte zéro tentative, et `WorkflowRepairer`, qui l'interdit ;
- d'importants blocs de code legacy inatteignables qui rendent le comportement effectif difficile à auditer.

L'audit n'a modifié ni le moteur ni les tests. Ce fichier est le seul livrable ajouté.

## 2. Inventaire et responsabilités

### Points d'entrée

| Fichier | Point d'entrée | Responsabilité |
|---|---|---|
| `src/run_workflow.py` | `main()` | CLI langage naturel, chargement Qwen, compilation et sauvegarde. |
| `src/json_to_bpmn.py` | `main()`, `convert_json_to_bpmn()` | Conversion déterministe d'un JSON existant vers BPMN, sans Qwen. |
| `src/validate_workflow.py` | `validate()` | Validation utilitaire d'un fichier workflow. |

### Génération

| Fichier | Éléments principaux | Responsabilité |
|---|---|---|
| `src/generation/model_loader.py` | `load_qwen_model()` | Charge tokenizer/modèle Transformers, device automatique et option 4 bits. |
| `src/generation/qwen_generator.py` | `QwenWorkflowGenerator`, `extract_json_object()` | Appelle le modèle, décode uniquement les tokens générés, extrait un objet JSON, expose les métadonnées de parsing et vide le cache CUDA. |
| `src/generation/prompts.py` | quatre constructeurs de prompts | Extraction des exigences, génération v2, repair structurel et repair de coverage. |
| `src/generation/pipeline.py` | `WorkflowCompiler` | Orchestration complète et conservation des artefacts/debug. |

### Workflow

| Fichier | Éléments principaux | Responsabilité |
|---|---|---|
| `src/workflow/normalizer.py` | `WorkflowNormalizer` | Migration v1, canonicalisation v2, IDs/références, timers et rapports. |
| `src/workflow/preparation.py` | `CanonicalWorkflowPreparer` | Safe repairs, split multi-processus, conversion des flux et validation préparée. |
| `src/workflow/semantic_validator.py` | `SemanticValidator` | Références, ownership, graphes locaux, gateways, reachability, cycles et timers. |
| `src/workflow/semantic_requirements.py` | `SemanticRequirements`, `normalize_semantic_requirements()` | Canonicalise les exigences extraites du texte. |
| `src/workflow/coverage_validator.py` | `CoverageValidator` | Compare exigences et workflow. |
| `src/workflow/repair.py` | `WorkflowRepairer`, `_StandardSchemaValidator` | Boucle de repair, scoring, acceptation/rejet et validation JSON Schema interne. |
| `src/workflow/graph_analyzer.py` | `WorkflowGraphAnalyzer` | Analyse process-scoped, SCC, niveaux, branches et index de collaboration. |

### BPMN

| Fichier | Éléments principaux | Responsabilité |
|---|---|---|
| `src/bpmn/constants.py` | constantes et `NODE_TAGS` | Dimensions, espacements et mapping des types. |
| `src/bpmn/nodes.py` | tailles et timers | Helpers historiques de nœuds. |
| `src/bpmn/layout.py` | `BPMNLayoutEngine` | Placement par process, pool, lane, niveau, branche et composant. |
| `src/bpmn/routing.py` | `calculate_edge_waypoints()` | Routage orthogonal et évitement simple des rectangles. |
| `src/bpmn/generator.py` | `BPMNGenerator` | XML BPMN, collaboration, lanes, nœuds, flux et BPMN DI. |
| `src/bpmn/xml_helpers.py` | helpers XML | Namespaces, conditions et labels. |

### Schéma et tests

- `schemas/workflow.schema.json` est le contrat v2 effectif.
- 14 modules de tests couvrent normalisation, préparation, collaboration, validation, repair, coverage, analyse, layout, génération BPMN et pipelines fake.
- 250 fonctions de test sont déclarées ; la paramétrisation produit 305 cas exécutés.

## 3. Architecture et pipeline réel

```text
description
  -> build_semantic_requirements_prompt()
  -> QwenWorkflowGenerator.generate_json()
  -> extract_json_object()
  -> normalize_semantic_requirements()
  -> build_generation_prompt()
  -> QwenWorkflowGenerator.generate_json()
  -> extract_json_object()
  -> WorkflowCompiler.is_meaningful_workflow()
  -> WorkflowNormalizer.normalize_with_report()
  -> CanonicalWorkflowPreparer.prepare_and_validate()
       -> WorkflowNormalizer.normalize_with_report()       [seconde normalisation]
       -> CanonicalWorkflowPreparer.apply_safe_repairs()
       -> _StandardSchemaValidator.validate()
       -> SemanticValidator.validate()
  -> WorkflowRepairer.repair()                            [si erreurs structurelles]
  -> CoverageValidator.validate()
  -> WorkflowRepairer.repair(repair_kind="coverage")      [si coverage incomplet]
  -> WorkflowGraphAnalyzer.analyze()
  -> BPMNLayoutEngine.layout()
  -> BPMNGenerator.generate()
       -> calculate_edge_waypoints()
  -> XML BPMN 2.0
```

### Table du pipeline

| Étape | Entrée | Sortie/source de vérité | Validation/passage | Échec |
|---|---|---|---|---|
| Schéma | fichier | `schema` | mapping JSON | `schema_validation` |
| Exigences | description | `requirements` | mapping et normalisation sans issue | `requirements_extraction` |
| Génération | prompt + schéma | `raw` | mapping + workflow significatif | `generation` |
| Normalisation | `raw` | `normalized` | invariants de non-disparition | `normalization` |
| Préparation | `normalized` | `current_workflow` | significatif, schéma et sémantique calculés | `normalization` |
| Repair structurel | `current_workflow` | meilleur candidat validé | zéro erreur `severity=error` | `repair` |
| Coverage | `current_workflow` | erreurs de coverage | warnings non bloquants | repair si erreurs |
| Repair coverage | copie de `current_workflow` | candidat ou retour à `base_workflow` | amélioration coverage sans régression | `coverage_repair` |
| Analyse | `current_workflow` | `analysis` | analyse sans exception | `analysis` |
| Layout | workflow + analyse | `layout` | géométries calculées | `layout` |
| BPMN | workflow + layout | `bpmn_xml` | XML non vide | `bpmn` |

`current_workflow` est correctement utilisé comme source de vérité après la préparation. Le coverage conserve une copie `base_workflow` et restaure celle-ci si le candidat est refusé.

## 4. Audit du modèle canonique

Le schéma et le code convergent sur :

- `schemaVersion`, `processes`, `participants`, `lanes`, `nodes`, `flows`, `messageFlows`, `ambiguities` ;
- `source`/`target` dans le JSON et `sourceRef`/`targetRef` uniquement lors de la sérialisation XML ;
- ownership explicite par `processRef`, `participantRef`, `laneRef` ;
- communications inter-processus dans `messageFlows`.

Différences ou compatibilités legacy :

- le normalizer accepte encore `process`, `pools`, `swimlanes`, `sourceRef`, `targetRef`, `nodeRefs`, `message_flows`, `messages` et `timerEvent` à la frontière v1 ;
- le schéma exige davantage que le contrat minimal annoncé : `description`, `executable`, `properties`, `actor`, `condition` et `isDefault` sont obligatoires ;
- `participantRef` et `laneRef` sont obligatoires mais nullables ;
- `nodes` peut être vide selon le schéma, alors que le pipeline l'interdit par `is_meaningful_workflow()` ; cette différence est intentionnelle mais devrait être documentée ;
- le générateur BPMN accepte théoriquement des participants comme endpoints de message flow, tandis que `SemanticValidator` ne résout que des endpoints de type node. Le pipeline canonique ne permet donc pas réellement ce cas.

## 5. Audit Qwen et parsing

### Comportement vérifié

- `extract_json_object()` ignore les accolades dans les chaînes et accepte Markdown/texte périphérique.
- Une absence, un JSON incomplet ou un résultat non objet lève une erreur ; aucun `{}` n'est fabriqué par le parser.
- Le pipeline rejette un objet vide ou sans nœud avant normalisation.
- Le texte complet Qwen n'est pas enregistré ; seules longueur, réussite et cause concise sont exposées.
- Les erreurs structurelles envoyées au repair sont bloquantes et dédupliquées.

### Risques CUDA/OOM

- Deux appels Qwen sont systématiques avant tout repair : exigences puis workflow.
- Jusqu'à trois repairs structurels puis trois repairs coverage sont possibles, soit jusqu'à huit générations par compilation.
- `max_new_tokens=4096` est élevé pour chaque appel.
- Le prompt de génération contient le JSON Schema complet.
- Le prompt structurel contient à nouveau workflow complet + schéma complet.
- `torch.cuda.empty_cache()` libère seulement les blocs non utilisés ; il ne réduit ni les poids du modèle ni le pic mémoire d'une génération.
- Aucun budget de taille de prompt, de tokens d'entrée ou de workflow n'est appliqué.
- Le mode 4 bits existe mais reste optionnel.

Le repair ne réinjecte pas un historique complet, ce qui limite déjà la croissance des prompts.

## 6. Audit du normalizer

Le normalizer préserve les objets valides, travaille sur copie et maintient des mappings ancien ID -> ID canonique. Les champs v2 absents ne sont pas inventés silencieusement : ils restent invalides et sont ensuite signalés par le schéma ou le validateur.

Comptage disponible dans le pipeline :

```text
before normalization = pipeline_debug.after_qwen_parse
after normalization  = pipeline_debug.after_normalization
```

Les invariants empêchent désormais qu'une collection non vide de processus, participants, nœuds ou flows devienne entièrement vide sans exception. Les éléments individuels non mappings peuvent toutefois être ignorés tant que la collection normalisée conserve au moins un élément ; le rapport contient alors un warning.

Les IDs sont assainis et les doublons renommés. Cela est nécessaire au schéma mais peut rendre les références externes non prévues difficiles à rapprocher si elles ne passent pas par les mappings connus.

## 7. Split multi-processus et safe repairs

Le split se trouve dans `CanonicalWorkflowPreparer._split_shared_processes()`.

Précondition : plusieurs participants référencent le même process et tous les nœuds de ce process possèdent un `participantRef` explicite appartenant à ces participants. Sans cette preuve, aucun split n'est fait.

Transformations :

- clone du process par participant ;
- mise à jour de `participant.processRef`, `node.processRef` et `lane.processRef` ;
- `_repair_flows()` conserve les séquences locales et convertit les séquences devenues inter-processus en message flows ;
- `finalize_collaboration_process_structure()` rétablit certaines continuités locales.

### Classification des repairs

| Repair | Classification | Motif/risque |
|---|---|---|
| Split par ownership explicite | SAFE | Preuve structurelle forte ; aucune attribution devinée. |
| Conversion sequence inter-processus -> message flow | SAFE | Imposée par BPMN ; endpoints préservés. |
| Suppression condition de sortie d'une parallel gateway | SAFE | Contrainte BPMN claire. |
| Déduplication exacte des message flows | SAFE | Même paire source/target. Une différence de nom/propriétés peut néanmoins être perdue. |
| Normalisation `messageTask` source/target unique | SAFE | Direction prouvée par les message flows. |
| Normalisation d'un `messageTask` ambigu en `serviceTask` | RISKY | Change la nature BPMN sans preuve métier. |
| Durées anglaises simples vers ISO 8601 | SAFE mais limitée | Déterministe, mais uniquement vocabulaire anglais. |
| Insertion d'un timer isolé selon tokens de noms | RISKY | Heuristique lexicale, sensible à la langue et aux noms. |
| Création technique de start/end manquants | RISKY | Ajoute des événements ; acceptable structurellement mais non neutre. |
| Raccord start/root ou sink/end unique | SAFE si unicité réelle | Protégé contre plusieurs candidats et événements déjà connectés. |
| Raccord causal source message -> target message | RISKY | Le plus court chemin causal unique est une preuve topologique utile, mais pas toujours une preuve d'ordre métier local. |

Branches, gateways et boucles locales sont préservées par le split. Le principal risque se situe après conversion, lors du raccord de composants de collaboration.

## 8. Audit JSON Schema

Le schéma couvre correctement les types de nœuds, timers, propriétés, références et collections du modèle courant. `_StandardSchemaValidator` implémente les mots-clés effectivement utilisés : types, `const`, `enum`, longueur, motif, items, required, additionalProperties, `anyOf`, `allOf/if/then` et `$ref` local.

Décalages :

- absence de `minItems` sur `nodes`, compensée par le pipeline uniquement ;
- aucune contrainte de référence croisée possible dans JSON Schema, donc correctement reportée au validateur sémantique ;
- participant endpoint de message flow accepté syntaxiquement mais pas sémantiquement ;
- le schéma accepte une ambiguïté `severity=error`, mais `SemanticValidator` ne traite pas la collection `ambiguities` comme erreur de graphe.

## 9. Audit SemanticValidator

Toutes les issues sont `error` par défaut, sauf `CYCLE_DETECTED`, explicitement `warning`. Ce choix est cohérent avec le pipeline : `_blocking_errors()` filtre uniquement `severity=error`, donc une boucle intentionnelle ne déclenche pas Qwen.

| Groupe de codes | Sévérité actuelle/recommandée | Observation |
|---|---|---|
| `INVALID_WORKFLOW`, collections/IDs manquants, doublons | error / error | Contrat canonique inexploitable. |
| `MISSING_PROCESS_NAME` | error / error | Requis par schéma. |
| `UNKNOWN_*`, mismatches ownership/process/lane | error / error | Références invalides. |
| `MULTIPLE_PARTICIPANTS_FOR_PROCESS` | error / error | Le modèle cible impose un process par pool. |
| endpoints flow/message inconnus ou absents | error / error | Graphe non sérialisable. |
| `SEQUENCE_FLOW_CROSSES_PROCESSES`, `MESSAGE_FLOW_WITHIN_SAME_PROCESS` | error / error | Violation de frontière BPMN. |
| erreurs timer | error / error | Génération XML impossible ou sens invalide. |
| start/end, incoming/outgoing, isolated, unreachable, no path | error / error | Structure locale invalide. |
| erreurs gateway et `DEAD_END_BRANCH` | error / error | Branche ou sémantique de gateway invalide. |
| `CYCLE_DETECTED` | warning / warning | Boucles BPMN légitimes. |

Limites : plusieurs start/end events sont permis ; les sémantiques avancées d'event-based gateway et de terminate/end variants ne sont pas modélisées ; les ambiguïtés présentes dans le workflow ne sont pas intégrées à cette validation.

## 10. Audit GraphAnalyzer

Constats conformes :

- bucket par `processRef` ;
- message flows exclus de l'adjacence locale ;
- adjacency/reverse, degrés, composantes non orientées, SCC de Tarjan, DAG condensé, niveaux, ordre topologique, cycles et back edges ;
- splits/joins déterminés par degrés et types gateway ;
- déterminisme par tris ;
- aucune dépendance à un nom métier.

Limites : les DFS/Tarjan sont récursifs et peuvent atteindre la limite de récursion sur un graphe très profond ; l'identification du join choisit le meilleur nœud commun selon distance, ce qui reste une approximation pour les graphes non structurés complexes.

## 11. Audit des exigences et du coverage

`normalize_semantic_requirements()` sépare participants et lanes, normalise les interactions au niveau participant, groupe les branches parallèles, élimine les parallélismes de moins de deux branches, convertit les durées simples et déduplique toutes les collections. Les ambiguïtés restent des warnings dans le coverage.

| Coverage | Matching | Risques |
|---|---|---|
| Participant | tokens du nom et de l'ID | Bon pour variantes simples ; faux positifs possibles par inclusion de petits ensembles. |
| Lane | nom + participant propriétaire | Structure correcte. |
| Timer | valeur exacte + contexte lexical | Le `kind` demandé n'est pas comparé. |
| Parallelism | gateway parallèle + branches immédiates | Ne couvre pas les activités plus profondes dans chaque branche. |
| Decision | présence de n'importe quelle gateway décisionnelle | Le nom et les outcomes requis ne sont pas comparés : faux positifs importants. |
| Interaction | participants des endpoints message flow | `sourceLane`/`targetLane` normalisés mais ignorés lors du matching. |
| Activity | matching lexical symétrique | Une activité au nom très générique peut couvrir trop de besoins. |

`related_node_ids` est toujours vide, y compris lorsqu'un candidat proche existe. Cela réduit fortement la qualité des diagnostics et des repairs ciblés.

## 12. Audit du Repairer

```text
workflow
  -> prepare_and_validate
  -> current + current_errors
  -> prompt(errors bloquantes dédupliquées)
  -> candidat Qwen
  -> prepare_and_validate
  -> schema + semantic + coverage
  -> score / garde anti-régression
  -> current si acceptable
  -> best seulement si score strictement meilleur
  -> retour best à épuisement
```

`best` et `best_score` protègent correctement le meilleur candidat structurel. Un candidat avec score supérieur n'est pas accepté. Pour le coverage, les gardes refusent disparition de processus/participants/nœuds, augmentation des erreurs structurelles, références inconnues supplémentaires et absence d'amélioration coverage.

Risques :

- un candidat structurel de score égal peut devenir `current` même s'il change substantiellement le workflow ; il ne remplace pas `best`, mais influence le prompt suivant ;
- le score compare les nombres d'erreurs, pas la conservation de toutes les activités/branches ;
- la protection catastrophique ne détecte que quelques formes de réduction ;
- les rapports conservent les erreurs de chaque tentative, mais celles-ci ne sont pas toutes réinjectées dans le prompt ;
- du code d'ancienne implémentation subsiste après des `return`, donc est inatteignable.

## 13. Audit pipeline_debug

Les clés demandées existent :

- `after_qwen_parse`
- `after_normalization`
- `after_initial_prepare`
- `after_structural_repair`
- `before_coverage_validation`
- `coverage_candidate`
- `final_current_workflow`

Chaque compteur utilise les six collections attendues. Les étapes non atteintes restent `None`, ce qui est approprié. `generation` expose le statut de parsing sans texte complet.

Diagnostics encore utiles : taille du prompt et tokens d'entrée/sortie par appel, nombre/type d'appels Qwen, score avant/après chaque candidat, raison normalisée de rejet, et pic mémoire GPU si disponible. Ces métriques ne doivent pas contenir le prompt ni les données métier.

## 14. Audit layout, routing et BPMN

### Layout

Le layout est process-scoped, empile les pools, puis les lanes, conserve une bande technique pour les nœuds sans participant/lane, sépare branches et composants, centre les joins et réserve des canaux de back edges. Les tailles varient selon le type BPMN. Une validation interne signale collisions et containment.

Limites : la largeur d'un pool utilise les niveaux de tous les nœuds du process, pas seulement du participant ; sur de très grandes collaborations cela peut produire beaucoup d'espace vide. Les graphes très non structurés peuvent nécessiter plus qu'un simple `branch_index`.

### Routing

Le routeur produit des segments orthogonaux et pénalise les intersections avec les rectangles de nœuds. Il utilise toujours l'ancre droite de la source et gauche de la cible, même pour un back edge ou un message vertical/inter-pool. Les pools et lanes ne sont pas des obstacles. Les canaux calculés par le layout ne sont pas consommés par `calculate_edge_waypoints()`.

### BPMNGenerator

Les process IDs, participants, lanes, types de nœuds, timers, sequence flows, message flows et BPMN DI sont correctement sérialisés. Les sequence flows sont vérifiés contre leur process. Les message flows sont placés dans la collaboration. Chaque shape/edge exige une géométrie.

Limite principale : le générateur autorise des endpoints participant pour les message flows, mais le validateur et le layout sont surtout conçus pour des endpoints node. Cette capacité n'est donc pas cohérente sur toute la chaîne.

## 15. Audit des tests

### Classification

- Unitaires : normalizer, semantic requirements/coverage, semantic validator, graph analyzer, routing via générateur, helpers BPMN.
- Intégration : preparation v2, pool boundaries, collaboration finalization, repair.
- Régression : safe repairs real-Qwen-shaped, candidat coverage destructif, workflow vide, JSON entouré de Markdown.
- End-to-end sans GPU : `test_generation_pipeline.py`, `test_initial_generation_pipeline.py` et plusieurs tests de `test_safe_repairs_real_qwen.py` utilisent des générateurs fake jusqu'au XML.

Couverture présente : workflow simple, N processus, participants, lanes, timers, gateways exclusives/parallèles/inclusives/event-based, boucles, message flows, process sans lane, plusieurs lanes, workflows invalides, JSON invalide, candidat pire, coverage repair, workflow valide, workflow vide et nombres variables de participants/processus.

Zones faibles :

- aucun test avec un vrai tokenizer/model minimal simulant précisément tenseurs, découpe du prompt et métadonnées de parsing ;
- pas de test de budget/pic CUDA ni de prompt très volumineux ;
- peu de tests de routing dédié pour messages verticaux, back edges et obstacles de pools ;
- pas de round-trip BPMN via un validateur BPMN externe ;
- pas de stress test avec milliers de nœuds/profondeur récursive ;
- pas de test démontrant la conservation métier pour un candidat de score égal ;
- pas de tests précis sur `related_node_ids`, le `kind` timer, les outcomes décisionnels et les lanes d'interaction.

## 16. Problèmes priorisés

### P1 — Double normalisation du workflow initial

- Symptôme : `WorkflowCompiler.compile()` normalise explicitement, puis `CanonicalWorkflowPreparer.prepare_and_validate()` normalise une seconde fois.
- Cause racine : ajout du checkpoint `after_normalization` sans point d'entrée « déjà normalisé » dans le preparer.
- Fichiers/fonctions : `generation/pipeline.py::compile`, `workflow/preparation.py::prepare_and_validate`.
- Impact : rapports dupliqués, coût inutile, risque qu'une transformation non idempotente soit appliquée deux fois.
- Correction recommandée : ajouter une méthode interne de validation/préparation d'un workflow déjà normalisé, sans changer l'API publique.
- Tests : égalité stricte simple/double préparation et absence de warnings dupliqués.

### P1 — Safe repairs heuristiques pouvant modifier l'ordre métier

- Symptôme : insertion d'un timer par tokens ou raccord de composants par chemin causal global.
- Cause : absence d'information explicite d'ordre local après conversion en message flows.
- Fichiers/fonctions : `preparation.py::_insert_unambiguous_isolated_timers`, `finalize_collaboration_process_structure`, `_causal_distances`.
- Impact : un graphe valide structurellement peut exprimer un ordre métier non demandé.
- Correction : séparer les repairs prouvés des repairs heuristiques, exiger une preuve supplémentaire ou les rendre opt-in.
- Tests : graphes symétriques, boucles de messages, deux chemins causaux de longueurs différentes mais sémantiquement ambigus.

### P1 — Coverage des décisions trop permissif

- Symptôme : toute gateway décisionnelle couvre toute exigence de décision.
- Cause : le validator teste seulement `if not decisions`.
- Fichier/fonction : `coverage_validator.py::validate`.
- Impact : exigences métier manquantes déclarées couvertes.
- Correction : comparer nom/contexte et outcomes aux branches/conditions.
- Tests : deux décisions distinctes, outcomes absents, gateway sans rapport.

### P1 — Incohérence `max_repair_attempts=0`

- Symptôme : la CLI autorise zéro (`< 0` rejeté), mais `WorkflowRepairer` exige au moins un.
- Cause : contrats divergents.
- Fichiers : `run_workflow.py::main`, `repair.py::WorkflowRepairer.__init__`.
- Impact : workflow valide fonctionne, workflow nécessitant repair échoue tardivement.
- Correction : soit rejeter `<= 0` dans la CLI, soit supporter explicitement zéro dans le pipeline.
- Tests : compilation valide/invalide avec zéro tentative.

### P1 — Risque GPU non borné

- Symptôme : jusqu'à huit générations, `max_new_tokens=4096`, prompts complets.
- Cause : budgets indépendants et aucune limite globale tokens/mémoire.
- Fichiers : `qwen_generator.py`, `pipeline.py`, `prompts.py`, `repair.py`.
- Impact : CUDA OOM ou latence importante sur workflows volumineux.
- Correction : budget global d'appels/tokens, seuils de prompt, arrêt sur absence d'amélioration, 4 bits configurable par défaut selon environnement.
- Tests : générateur compteur, candidat identique répété, gros workflow synthétique.

### P2 — Candidat structurel égal utilisé comme `current`

- Symptôme : `candidate_score <= current_score` accepte l'égalité.
- Cause : progression autorisée sans métrique de conservation métier.
- Fichier : `repair.py::repair`.
- Impact : le prompt suivant part d'un candidat différent sans amélioration mesurée.
- Correction : exiger une amélioration stricte ou une métrique secondaire de préservation.
- Tests : score égal avec suppression/remplacement d'une activité.

### P2 — Coverage incomplet

- Symptômes : `kind` timer ignoré, lanes d'interaction ignorées, `related_node_ids=[]`, branches seulement immédiates.
- Fichier : `coverage_validator.py`.
- Impact : faux positifs et diagnostics peu ciblés.
- Correction : enrichir les index et retourner les candidats proches.
- Tests : timer même valeur/autre kind, mêmes participants/autres lanes, activité à deux niveaux sous gateway.

### P2 — Endpoints participant incohérents

- Symptôme : pris en charge par le générateur XML mais rejetés par validation/analyse/layout.
- Fichiers : `semantic_validator.py`, `graph_analyzer.py`, `layout.py`, `generator.py`.
- Impact : contrat implicite contradictoire.
- Correction : décider officiellement « node uniquement » ou supporter participant de bout en bout.
- Tests : message participant->participant et node->node.

### P2 — Code legacy inatteignable

- Symptôme : anciennes implémentations après `return` dans `pipeline.py::_apply_safe_repairs` et `repair.py::build_repair_prompt`.
- Cause : migrations conservées dans les fichiers actifs.
- Impact : audit difficile, dette et risque de correction au mauvais endroit.
- Correction : suppression ciblée après couverture de caractérisation ; ce n'est pas un refactor architectural.
- Tests : conserver les tests actuels avant suppression.

### P2 — Routage limité

- Symptôme : ancres droite/gauche fixes, pools non obstacles, canaux back-edge non utilisés.
- Fichier : `bpmn/routing.py` et intégration `generator.py`.
- Impact : collisions visuelles dans collaborations verticales ou boucles.
- Correction : choisir les ancres selon direction et exploiter les métadonnées de layout.
- Tests : inter-pool vertical, target à gauche, boucle, lane boundary.

### P2 — Récursion sur grands graphes

- Symptôme : Tarjan et DFS récursifs.
- Fichiers : `graph_analyzer.py::_scc`, `_back_edges`, `semantic_validator.py::_validate_cycles`.
- Impact : `RecursionError` sur workflows très profonds.
- Correction : variantes itératives ou garde de taille documentée.
- Tests : chaîne et cycle dépassant 1 000 nœuds.

### P3 — Heuristiques linguistiques

- Symptôme : listes de stopwords et unités surtout français/anglais.
- Fichiers : `preparation.py`, `normalizer.py`, `semantic_requirements.py`, `coverage_validator.py`.
- Impact : comportement variable selon langue.
- Correction : privilégier structure et données normalisées ; isoler la lexicalisation dans une stratégie configurable.
- Tests : mêmes structures avec libellés non latins et langues supplémentaires.

### P3 — Observabilité Qwen incomplète

- Symptôme : pas de taille de prompt, tokens, durée ou mémoire.
- Fichiers : `qwen_generator.py`, `pipeline.py`.
- Impact : diagnostic OOM/performance incomplet.
- Correction : métriques numériques sans contenu sensible.
- Tests : faux tokenizer exposant longueurs.

## 17. Risques de régression

- Modifier les règles de split peut casser les migrations v1 et les collaborations partagées.
- Durcir le coverage peut augmenter les repairs Qwen et donc l'usage GPU.
- Supprimer la double normalisation nécessite de préserver exactement les rapports et l'immuabilité.
- Durcir l'acceptation des scores égaux peut réduire la capacité d'une seconde tentative à converger.
- Améliorer routing/layout ne doit pas changer la déterminisme des coordonnées attendu par les tests.
- Supprimer le code legacy doit se faire après vérification des imports directs/notebooks utilisant les méthodes privées.

## 18. Plan de résolution ordonné

1. Corriger le contrat zéro tentative et ajouter le test correspondant.
2. Éliminer la double normalisation via un chemin « normalized -> safe repairs -> validations » couvert par caractérisation.
3. Introduire un budget global Qwen et arrêter les tentatives sans amélioration.
4. Reclasser/documenter les repairs heuristiques et renforcer leurs preuves.
5. Corriger le coverage décision/timer/lane et renseigner `related_node_ids`.
6. Décider le contrat officiel des endpoints de message flow.
7. Supprimer les blocs legacy inatteignables.
8. Renforcer routing et tests visuels seulement après stabilisation du pipeline.
9. Ajouter stress tests de graphe et remplacer les parcours récursifs si nécessaire.

## 19. Conclusion

Le cœur canonique, les frontières de process, la validation locale et la conservation du meilleur candidat sont solides pour les scénarios actuellement couverts. La priorité n'est pas un refactor massif : elle est de réduire les ambiguïtés de contrat, borner Qwen et distinguer strictement réparation prouvée et réparation heuristique.
