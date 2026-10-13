from src.workflow.semantic_requirements import ground_semantic_requirements
from tests.test_generation_pipeline import CoverageGenerator,SCHEMA,Xml,empty_requirements,v2
from src.generation.pipeline import WorkflowCompiler


def ground(description,**raw):
    return ground_semantic_requirements(empty_requirements(**raw),description)


def test_three_explicit_actor_actions_are_supported():
    result,_,report=ground("A receives a request. A verifies the request. A approves the request.",participants=["A"])
    assert len(result["activities"])==3
    assert all(item["status"]=="SUPPORTED" for item in report if item["type"]=="activities")


def test_if_otherwise_creates_one_supported_decision_with_two_outcomes():
    result,_,report=ground("If the document is complete, continue. Otherwise request missing information.")
    assert len(result["decisions"])==1 and len(result["decisions"][0]["outcomes"])==2
    assert any(item["type"]=="decisions" and item["status"]=="SUPPORTED" for item in report)


def test_all_explicit_directed_interactions_are_accumulated():
    description=("A sends a file to B. B requests additional information from A. "
                 "A sends the information to B. B informs C of the result.")
    result,_,_=ground(description,participants=["A","B","C"])
    assert {(item["source"],item["target"]) for item in result["interactions"]}=={("A","B"),("B","A"),("B","C")}


def test_explicit_timer_and_notification_activity_are_both_preserved():
    description="If payment is missing, wait two days and notify the customer."
    result,_,report=ground(description,participants=["Customer"],activities=[{"name":"Notify Customer"}])
    assert result["timers"]==[{"kind":"duration","value":"P2D"}]
    assert result["activities"]==[{"name":"Notify Customer"}]
    assert all(item["status"]=="SUPPORTED" for item in report if item["type"] in {"timers","activities"})


def test_complex_description_supplements_missing_categories_without_hallucinating():
    description=("A receives a dossier from B. A checks the dossier and records the result. "
                 "If the dossier is complete, A forwards it to C. Otherwise A requests details from B. "
                 "B provides the details to A. A checks the dossier again. "
                 "If the result is valid, C approves it. Otherwise C rejects it. "
                 "C waits two days and informs A of the result.")
    raw=empty_requirements(participants=["A","B","C"],timers=[{"kind":"duration","value":"P2D"}])
    result,_,_=ground_semantic_requirements(raw,description)
    assert len(result["participants"])==3
    assert len(result["activities"])>=8
    assert len(result["decisions"])==2
    assert result["timers"]==[{"kind":"duration","value":"P2D"}]
    assert len(result["interactions"])>=4


def test_grounding_report_separates_supported_and_unsupported_candidates():
    result,_,report=ground("A sends a file to B.",participants=["A","B"],
        activities=[{"name":"Send File"},{"name":"Archive Invoice"}],
        interactions=[{"source":"A","target":"B"},{"source":"B","target":"A"}],
        timers=[{"kind":"duration","value":"P1D"}])
    statuses={(item["type"],str(item["requirement"])):item["status"] for item in report}
    assert any(kind=="activities" and "Send File" in requirement and status=="SUPPORTED" for (kind,requirement),status in statuses.items())
    assert any(kind=="activities" and "Archive Invoice" in requirement and status=="UNSUPPORTED" for (kind,requirement),status in statuses.items())
    assert result["timers"]==[] and result["interactions"]==[{"source":"A","target":"B"}]


def test_pipeline_debug_keeps_raw_grounded_and_grounding_report():
    raw=empty_requirements(participants=["A","B"],activities=[{"name":"Send File"},{"name":"Invented Work"}],interactions=[{"source":"A","target":"B"}])
    generator=CoverageGenerator(raw,v2());compiler=WorkflowCompiler(generator,SCHEMA,max_repair_attempts=0);compiler._bpmn_generator=Xml()
    result=compiler.compile("A sends a file to B.")
    debug=result["pipeline_debug"]
    assert debug["semantic_requirements_raw"]==raw
    assert debug["semantic_requirements_grounded"]==result["semantic_requirements"]
    assert any(item["status"]=="UNSUPPORTED" for item in debug["requirement_grounding_report"])
