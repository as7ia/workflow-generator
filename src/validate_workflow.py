from __future__ import annotations
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parent.parent
SCHEMA=ROOT/"schemas"/"workflow.schema.json"

def load(p): return json.loads(p.read_text(encoding="utf-8"))
def validate(path):
 schema=load(SCHEMA); data=load(path); errors=[]
 for e in Draft202012Validator(schema).iter_errors(data):
  loc=".".join(map(str,e.absolute_path)) or "racine"; errors.append(f"{loc}: {e.message}")
 if not errors:
  pids={p["id"] for p in data["participants"]}; nids=[n["id"] for n in data["nodes"]]; fids=[f["id"] for f in data["flows"]]
  if len(nids)!=len(set(nids)): errors.append("IDs de nœuds dupliqués")
  if len(fids)!=len(set(fids)): errors.append("IDs de flux dupliqués")
  if not any(n["type"]=="startEvent" for n in data["nodes"]): errors.append("startEvent manquant")
  if not any(n["type"]=="endEvent" for n in data["nodes"]): errors.append("endEvent manquant")
  for n in data["nodes"]:
   if n["actor"] is not None and n["actor"] not in pids: errors.append(f"Acteur inexistant pour {n['id']}")
  nidset=set(nids)
  for f in data["flows"]:
   if f["source"] not in nidset: errors.append(f"Source inexistante dans {f['id']}")
   if f["target"] not in nidset: errors.append(f"Cible inexistante dans {f['id']}")
 if errors:
  print(f"[INVALIDE] {path.name}")
  for e in errors: print(" -",e)
  return False
 print(f"[VALIDE] {path.name}"); return True

if __name__=="__main__":
 paths=[ROOT/sys.argv[1]] if len(sys.argv)>1 else sorted((ROOT/"examples").glob("*.json"))
 raise SystemExit(0 if all(validate(p) for p in paths) else 1)
