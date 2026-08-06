# Diagramme du modèle

```text
Workflow
├── schemaVersion
├── process : Process
├── participants : Participant [0..*]
├── nodes : Node [2..*]
├── flows : Flow [1..*]
└── ambiguities : Ambiguity [0..*]

Node.actor → Participant.id
Flow.source → Node.id
Flow.target → Node.id
Ambiguity.relatedNode → Node.id
```

```mermaid
classDiagram
class Workflow {
 +string schemaVersion
 +Process process
 +Participant[] participants
 +Node[] nodes
 +Flow[] flows
 +Ambiguity[] ambiguities
}
class Process { +string id +string name +string description +boolean executable }
class Participant { +string id +string name +ParticipantType type }
class Node { +string id +NodeType type +string name +string|null actor +object properties }
class Flow { +string id +string source +string target +string|null condition +boolean isDefault }
class Ambiguity { +string id +string message +string|null relatedNode +Severity severity }
Workflow "1" *-- "1" Process
Workflow "1" *-- "0..*" Participant
Workflow "1" *-- "2..*" Node
Workflow "1" *-- "1..*" Flow
Workflow "1" *-- "0..*" Ambiguity
Node --> Participant : actor
Flow --> Node : source/target
Ambiguity --> Node : relatedNode
```
