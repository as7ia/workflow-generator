import copy,hashlib,re
from pathlib import Path
import pytest
from src.generation.pipeline import WorkflowCompiler
from src.generation.prompts import build_generation_prompt,build_semantic_requirements_prompt
from src.workflow.semantic_requirements import (normalize_semantic_requirements,
    validate_requirements_against_description)

SCHEMA=Path(__file__).parents[1]/"schemas"/"workflow.schema.json"
KEYS=("participants","lanes","timers","parallelisms","decisions","interactions","activities","ambiguities")

def empty(**values):
    result={key:[] for key in KEYS};result.update(values);return result

def validate(description,**values):
    normalized,_=normalize_semantic_requirements(empty(**values))
    return validate_requirements_against_description(normalized,description)

class IsolatedGenerator:
    def __init__(self):self.prompts=[]
    def generate_json(self,prompt,max_new_tokens=None):
        del max_new_tokens;self.prompts.append(prompt)
        description=re.search(r"<USER_DESCRIPTION>\n(.*?)\n</USER_DESCRIPTION>",prompt,re.S).group(1)
        name="Actor Alpha" if "Alpha" in description else "Actor Beta"
        activity="Activity Alpha" if "Alpha" in description else "Activity Beta"
        if prompt.startswith("SEMANTIC_REQUIREMENTS_EXTRACTION"):
            return empty(participants=[{"name":name}],activities=[{"name":activity}])
        slug="alpha" if "Alpha" in description else "beta";participant=f"participant_{slug}";process=f"process_{slug}"
        return {"schemaVersion":"2.0","processes":[{"id":process,"name":name,"properties":{}}],
            "participants":[{"id":participant,"name":name,"processRef":process,"properties":{}}],"lanes":[],
            "nodes":[{"id":f"start_{slug}","type":"startEvent","name":"Start","processRef":process,"participantRef":participant,"laneRef":None,"actor":None,"properties":{}},
                {"id":f"task_{slug}","type":"userTask","name":activity,"processRef":process,"participantRef":participant,"laneRef":None,"actor":None,"properties":{}},
                {"id":f"end_{slug}","type":"endEvent","name":"End","processRef":process,"participantRef":participant,"laneRef":None,"actor":None,"properties":{}}],
            "flows":[{"id":f"flow_{slug}_1","source":f"start_{slug}","target":f"task_{slug}","processRef":process,"condition":None,"isDefault":False,"properties":{}},
                {"id":f"flow_{slug}_2","source":f"task_{slug}","target":f"end_{slug}","processRef":process,"condition":None,"isDefault":False,"properties":{}}],"messageFlows":[],"ambiguities":[]}

@pytest.mark.parametrize("order",[("Alpha","Beta"),("Beta","Alpha")])
def test_successive_compilations_are_isolated_in_both_orders(order):
    generator=IsolatedGenerator();compiler=WorkflowCompiler(generator,SCHEMA,max_repair_attempts=0)
    results=[]
    for value in order:
        description=f"Actor {value} performs Activity {value}.";before=len(generator.prompts);result=compiler.compile(description);results.append(result)
        assert result["success"] and all(description in prompt for prompt in generator.prompts[before:before+2])
        assert result["pipeline_debug"]["input"]=={"description_length":len(description),"description_sha256":hashlib.sha256(description.encode()).hexdigest()}
        assert result["pipeline_debug"]["semantic_requirements_before_validation"]["participants"]==1
        assert result["pipeline_debug"]["semantic_requirements_after_validation"]["participants"]==1
    second=json_text(results[1]);assert order[0] not in second and order[1] in second

def json_text(result):
    import json
    return json.dumps({"requirements":result["semantic_requirements"],"workflow":result["normalized_workflow"]})

def test_explicit_timer_is_preserved_and_other_duration_is_rejected():
    result,issues=validate("The process must wait 2 days.",timers=[{"value":"P2D"},{"value":"P3D"}])
    assert result["timers"]==[{"kind":"duration","value":"P2D"}] and issues

def test_description_without_duration_has_no_timer():
    result,issues=validate("An analyst reviews the file.",timers=[{"value":"P1D"}])
    assert result["timers"]==[] and issues[0]["severity"]=="warning"

def test_external_participants_and_internal_lanes_are_not_confused():
    description="External actor Alpha contacts organization Beta. Internal roles Reviewer and Approver work inside Beta."
    result,_=validate(description,participants=[{"name":"Actor Alpha"},{"name":"Beta"},{"name":"Reviewer"}],
        lanes=[{"name":"Reviewer","participant":"Beta"},{"name":"Approver","participant":"Beta"}])
    assert [item["name"] for item in result["participants"]]==["Actor Alpha","Beta"]
    assert result["lanes"]==[{"name":"Reviewer","participant":"Beta"},{"name":"Approver","participant":"Beta"}]

def test_explicit_parallelism_is_preserved_but_invented_one_is_not():
    branches=[{"activity":"Review file"},{"activity":"Archive file"}]
    explicit,_=validate("Review file and archive file simultaneously.",parallelisms=[{"branches":branches}])
    absent,issues=validate("Review file, then archive file.",parallelisms=[{"branches":[{"activity":"Review file"},{"activity":"Foreign work"}]}])
    assert explicit["parallelisms"]==[{"branches":branches}]
    assert absent["parallelisms"]==[] and issues

def test_french_terminology_is_preserved():
    result,_=validate("Le Réviseur contrôle le dossier puis l'Archiviste classe le dossier.",
        participants=[{"name":"Réviseur"},{"name":"Archiviste"}],activities=[{"name":"Contrôler le dossier"},{"name":"Classer le dossier"}])
    assert result["participants"]==[{"name":"Réviseur"},{"name":"Archiviste"}]
    assert result["activities"]==[{"name":"Contrôler le dossier"},{"name":"Classer le dossier"}]

def test_prompts_isolate_the_exact_description_and_validated_requirements():
    description="Actor Delta performs Task Delta."
    semantic=build_semantic_requirements_prompt(description);generation=build_generation_prompt(description,{},empty())
    for prompt in (semantic,generation):
        assert f"<USER_DESCRIPTION>\n{description}\n</USER_DESCRIPTION>" in prompt
        assert "derived ONLY" in prompt and "Do not invent" in prompt
    assert "<VALIDATED_SEMANTIC_REQUIREMENTS>" in generation

def test_rich_requirements_are_not_emptied_by_conservative_validation():
    description=("Organization North has internal roles Intake and Review. Intake receives a case and Review checks completeness. "
        "After waiting two days, Review decides accepted or rejected while Notify and Archive run simultaneously. North informs Actor South.")
    result,_=validate(description,participants=[{"name":"Organization North"},{"name":"Actor South"}],
        lanes=[{"name":"Intake","participant":"Organization North"},{"name":"Review","participant":"Organization North"}],
        timers=[{"value":"P2D"}],parallelisms=[{"branches":[{"activity":"Notify"},{"activity":"Archive"}]}],
        decisions=[{"name":"Completeness decision","outcomes":["accepted","rejected"]}],
        interactions=[{"source":"Organization North","target":"Actor South"}],activities=[{"name":"Receive case"},{"name":"Completeness verification"}])
    assert all(result[key] for key in ("participants","lanes","timers","parallelisms","decisions","interactions","activities"))

def test_paraphrased_activity_and_close_participant_are_preserved():
    result,issues=validate("The reception service verifies whether the supplied information is complete.",
        participants=[{"name":"Reception"}],activities=[{"name":"Completeness verification"}])
    assert result["participants"] and result["activities"]
    assert all(issue["severity"]=="warning" for issue in issues)

def test_complete_participant_filtering_is_diagnosed():
    result,issues=validate("Reviewer is an internal role inside an unspecified organization.",
        participants=[{"name":"Reviewer"}],lanes=[{"name":"Reviewer","participant":"Unspecified organization"}])
    assert result["participants"]==[]
    assert "SEMANTIC_REQUIREMENTS_OVERFILTERED" in {issue["code"] for issue in issues}
