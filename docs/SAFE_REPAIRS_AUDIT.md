# Audit des safe repairs

Les transformations ci-dessous sont exécutées par
`CanonicalWorkflowPreparer.apply_safe_repairs()` dans
`src/workflow/preparation.py`, avant tout repair Qwen. Chaque phase travaille sur une copie,
est revalidée, et est annulée si le nombre d'erreurs sémantiques bloquantes augmente.

| Fonction | Condition et données utilisées | Transformation | Risque initial | Classification actuelle |
|---|---|---|---|---|
| `_repair_convergent_ownership` | Références participant/lane existantes | Aligne `lane.processRef` sur son participant; aligne un node seulement si participant et lane convergent | Faible | SAFE, preuve de convergence obligatoire |
| `_split_shared_processes` | Process partagé, tous les nodes ont un `participantRef` explicite appartenant au process | Crée un process par participant et propage les références | Moyen | SAFE sous précondition exhaustive; sinon refus |
| `_repair_flows` | Endpoints connus et process explicitement distincts | Convertit un sequence flow inter-process en message flow | Faible | SAFE; endpoints ambigus laissés inchangés |
| `_repair_flows` | Sortie d'une `parallelGateway` | Retire une condition interdite par BPMN | Faible | SAFE, correction de représentation |
| `_repair_flows` | Message flows strictement égaux | Supprime uniquement un doublon identique | Élevé lorsque seuls les endpoints étaient comparés | SAFE après durcissement; messages différents conservés |
| `_normalize_message_tasks` | Node legacy `messageTask`, position source/cible explicite | Canonicalise en send/receive; le cas bidirectionnel conserve le fallback générique historique | Moyen | SAFE de représentation; aucun ordre ni flux n'est créé |
| `_normalize_timers` | `kind=duration` et valeur simple entièrement reconnue | Convertit minutes/heures/jours en ISO 8601 | Faible | SAFE |
| `_insert_unambiguous_isolated_timers` | Timer isolé et exactement un flow local portant exactement le même libellé normalisé | Insère le timer dans ce flow | Élevé avec l'ancien recouvrement de tokens/endpoints | SAFE après suppression des inférences lexicales partielles; sinon refus |
| `finalize_collaboration_process_structure` | Une seule racine/sortie locale admissible | Ajoute start/root ou sink/end | Moyen | SAFE après contrôle d'unicité; aucun événement n'est créé avant cette preuve |
| `finalize_collaboration_process_structure` | Un endpoint message sortant possède un unique premier point de réentrée local, non revendiqué par un autre endpoint | Ajoute la continuité locale | Élevé avec l'ancienne distance causale minimale | SAFE après preuve de première réentrée unique et bijective; sinon refus |

## Repairs initialement RISKY

- L'insertion d'un timer par simple intersection de tokens avec un flow ou ses endpoints.
- Le choix du point de raccordement de collaboration par distance causale minimale.
- La création immédiate d'un start/end, même lorsque plusieurs roots/sinks existaient.
- La déduplication de tous les message flows partageant les mêmes endpoints.

## Cas désormais refusés

- plusieurs roots ou plusieurs sinks admissibles;
- plusieurs start events ou end events pouvant recevoir un raccord;
- plusieurs points de réentrée causale pour un même endpoint message;
- un même point de réentrée revendiqué par plusieurs sorties locales;
- timer sans correspondance exacte, ou avec plusieurs flows correspondants;
- ownership de node dont `participantRef` et `laneRef` désignent des process différents;
- conversion inter-process lorsque l'un des endpoints ou son ownership est inconnu.

Les gateways, branches, conditions métier, back edges et activités ne sont jamais créés,
fusionnés ou supprimés par ces phases. `CYCLE_DETECTED` reste un warning et aucune boucle
n'est cassée. Le report ne contient pas le workflow : une entrée `SAFE_REPAIR_TRACE` fournit
uniquement le nom de phase, le statut, la raison et les comptes d'erreurs avant/après.
