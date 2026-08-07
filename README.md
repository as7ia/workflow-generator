# Workflow Generator

Compilateur déterministe qui transforme une description métier en workflow JSON avec Qwen 3, valide et répare ce JSON, puis produit un fichier BPMN 2.0 avec BPMN DI ouvrable dans [bpmn.io](https://bpmn.io/).

## Architecture

```text
Description métier → Qwen 3 → normalisation → JSON Schema
→ validation sémantique → réparation éventuelle → analyse du graphe
→ layout → génération BPMN XML déterministe → workflow.bpmn
```

Qwen génère uniquement le JSON. Le XML BPMN n'est jamais généré par le modèle.

## Installation locale

Python 3.10 ou plus récent est recommandé.

```bash
python -m pip install -r requirements.txt
```

Installez PyTorch selon votre machine depuis la documentation officielle. Google Colab fournit généralement déjà `torch`. La quantification 4 bits est facultative :

```bash
python -m pip install bitsandbytes
```

## Utilisation

Mode interactif :

```bash
python src/run_workflow.py
```

Mode direct :

```bash
python src/run_workflow.py --description "Un client soumet une demande, un agent la vérifie puis accepte ou refuse la demande." --output output/test1
```

Options utiles :

```text
--model Qwen/Qwen3-4B
--max-repair-attempts 3
--output output/test1
--load-in-4bit
```

Le BPMN final est enregistré dans `output/test1/workflow.bpmn`.

## Validation d'un workflow existant

Tous les exemples :

```bash
python src/validate_workflow.py
```

Un fichier précis :

```bash
python src/validate_workflow.py examples/01-linear-complaint.json
```

## Google Colab

Monter Google Drive :

```python
from google.colab import drive
drive.mount("/content/drive")
```

Récupérer ou actualiser le projet :

```bash
%cd /content/drive/MyDrive/workflow-generator-phase1
!git pull
!pip install -r requirements.txt
```

Charger Qwen et créer le compilateur une seule fois :

```python
from src.generation import QwenWorkflowGenerator, WorkflowCompiler, load_qwen_model

tokenizer, model = load_qwen_model("Qwen/Qwen3-4B")
generator = QwenWorkflowGenerator(tokenizer, model)
compiler = WorkflowCompiler(
    generator=generator,
    schema_path="schemas/workflow.schema.json",
    max_repair_attempts=3,
)
```

L'utilisateur fournit ensuite uniquement sa description :

```python
description = """
Un client soumet une demande. Le service vérifie le dossier puis accepte
ou refuse la demande et informe le client.
"""

result = compiler.compile_and_save(description, output_dir="output/test")
print(result["success"])
print("output/test/workflow.bpmn")
```

Télécharger le BPMN :

```python
from google.colab import files
files.download("output/test/workflow.bpmn")
```

Pour économiser la mémoire GPU avec une machine compatible, installez `bitsandbytes` puis utilisez :

```python
tokenizer, model = load_qwen_model("Qwen/Qwen3-4B", load_in_4bit=True)
```

## Tests

```bash
python -m pytest -q
```
