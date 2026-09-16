# Audit des appels Qwen

Le chemin actif part de `WorkflowCompiler.compile()`. Le modèle est chargé une seule fois
dans `run_workflow.main()` puis le même objet `QwenWorkflowGenerator` est réutilisé pendant
toute la compilation.

| Phase | Appelante | Raison | Plafond par défaut | Contenu du prompt | Doublon évité |
|---|---|---|---:|---|---|
| Exigences | `WorkflowCompiler.compile` | Extraire les exigences utilisées par le coverage | 1024 | Description et contrat des exigences | Un appel par compilation |
| Génération initiale | `WorkflowCompiler.compile` | Produire le workflow canonique complet | 4096 | Description, schéma officiel et règles v2 | Un appel par compilation |
| Repair structurel | `WorkflowRepairer.repair` via `_PhaseGenerator` | Corriger les seules erreurs structurelles bloquantes restantes | 2048 | Workflow courant, erreurs `severity=error` dédupliquées, règles essentielles | Aucun appel après safe repair réussi, warning seul, budget nul ou plafond global |
| Repair coverage | `WorkflowRepairer.repair` via `_PhaseGenerator` | Ajouter les exigences de coverage encore absentes | 2048 | Workflow courant, exigences concernées et erreurs de coverage bloquantes dédupliquées | Aucun appel lorsque le coverage est déjà valide |

Les fonctions legacy conservées sous des retours anticipés ne contiennent aucun appel au
générateur. `json_to_bpmn` est entièrement déterministe. Il n'existe donc pas d'autre chemin
Qwen actif dans le dépôt.

Le maximum historique était de 8 appels : exigences, génération initiale, trois repairs
structurels et trois repairs coverage. Le plafond global par défaut est maintenant de 4 appels,
toutes phases confondues. Les limites structurelle et coverage restent configurables séparément,
mais ne peuvent jamais dépasser ensemble le budget global disponible.

Les prompts complets ne sont jamais enregistrés dans le résultat. Seules leurs tailles utiles
figurent dans `prompt_stats`. `generation_stats` expose les compteurs par phase et le total réel.
