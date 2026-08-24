# Modèle canonique de workflow v2

Le JSON intermédiaire officiel utilise `schemaVersion: "2.0"` et représente
explicitement plusieurs processus. L'ownership BPMN est stocké dans le JSON ;
il ne doit pas être déduit de `actor`, du layout ou du nom des éléments.

```text
Workflow
├── Process [1..*]
│   ├── Participant / Pool [0..*]  via participant.processRef
│   ├── Lane [0..*]                via lane.processRef
│   ├── Node [0..*]                via node.processRef
│   └── SequenceFlow [0..*]        via flow.processRef
└── MessageFlow [0..*]
    └── relie deux nodes appartenant à deux processus distincts
```

## Collections canoniques

```text
Workflow
├── schemaVersion = "2.0"
├── processes: Process[]
├── participants: Participant[]
├── lanes: Lane[]
├── nodes: Node[]
├── flows: SequenceFlow[]
├── messageFlows: MessageFlow[]
└── ambiguities: Ambiguity[]
```

Relations de référence :

```text
Participant.processRef ───────────────→ Process.id
Lane.processRef ──────────────────────→ Process.id
Lane.participantRef ──────────────────→ Participant.id (optionnel)
Node.processRef ──────────────────────→ Process.id
Node.participantRef ──────────────────→ Participant.id (optionnel)
Node.laneRef ─────────────────────────→ Lane.id (optionnel)
SequenceFlow.processRef ──────────────→ Process.id
SequenceFlow.source / target ─────────→ Node.id
MessageFlow.source / target ──────────→ Node.id
```

`actor` est une information métier libre. Il n'établit aucune appartenance
BPMN.

## Invariants sémantiques

- Chaque `node.processRef` référence un processus existant.
- Si un node possède `participantRef`, ce participant référence le même process.
- Si un node possède `laneRef`, cette lane référence le même process.
- Une lane et son participant optionnel appartiennent au même process.
- Les deux extrémités d'un sequence flow appartiennent au même process.
- `flow.processRef` correspond au process de ses deux extrémités.
- Les extrémités d'un message flow appartiennent à deux process distincts.
- Chaque process possède son propre graphe de contrôle, ses start events et ses
  end events.

Le JSON Schema vérifie la forme et la présence des références. La vérification
des références croisées et des invariants ci-dessus appartient au futur
`SemanticValidator` v2.

## Types BPMN officiels

```text
startEvent
endEvent
userTask
serviceTask
manualTask
scriptTask
businessRuleTask
sendTask
receiveTask
exclusiveGateway
parallelGateway
inclusiveGateway
eventBasedGateway
subProcess
intermediateCatchEvent
intermediateThrowEvent
```

Il n'existe pas de type canonique `timerEvent`. Un timer est un événement BPMN
supporté, par exemple `intermediateCatchEvent`, portant :

```json
{
  "properties": {
    "timer": {
      "kind": "duration",
      "value": "PT5M"
    }
  }
}
```

`kind` pourra ensuite être étendu à `date` et `cycle` sans créer un nouveau
type de node.

## Frontière JSON / BPMN XML

Dans toutes les représentations JSON internes :

```json
{ "source": "node_a", "target": "node_b" }
```

Uniquement lors de la génération BPMN XML :

```xml
<bpmn:sequenceFlow sourceRef="node_a" targetRef="node_b" />
```

`sourceRef` et `targetRef` sont acceptés par le normalizer comme aliases de
migration, mais ne figurent jamais dans sa sortie canonique.

## Migration v1 vers v2

- `process` devient un élément de `processes`.
- Une ancienne collection `pools` devient `participants`.
- Chaque participant reçoit un `processRef` explicite.
- Les lanes imbriquées sont aplaties dans la collection top-level `lanes`.
- Les anciens `participant.nodeRefs` et `lane.nodeRefs` servent uniquement à
  renseigner `node.participantRef` et `node.laneRef` pendant la migration.
- Plusieurs anciens pools possédant des membres sont migrés vers des processus
  distincts et déterministes.
- Un workflow v1 à processus unique conserve ce process pour les éléments non
  attribués.
- `actor` est conservé sans être utilisé comme ownership.
- `sourceRef`, `source_id` et `from` deviennent `source`; les aliases target
  deviennent `target`.
- `timerEvent` devient `intermediateCatchEvent` avec `properties.timer`.
- Le normalizer ne supprime aucune tâche, ne modifie aucune branche et ne
  convertit aucun sequence flow en message flow.
