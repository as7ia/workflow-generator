import copy

import pytest

from src.generation.pipeline import WorkflowCompiler
from src.generation.qwen_generator import extract_json_object
from src.workflow.normalizer import WorkflowNormalizer
from tests.test_generation_pipeline import SCHEMA, Xml, empty_requirements, f, n, v2


class InitialGenerator:
    def __init__(self,value=None,error=None):
        self.value=value;self.error=error;self.workflow_calls=0

    def generate_json(self,prompt):
        if prompt.startswith("SEMANTIC_REQUIREMENTS_EXTRACTION"):
            return empty_requirements()
        self.workflow_calls+=1
        if self.error:raise self.error
        return copy.deepcopy(self.value)


def compile_value(value=None,error=None):
    generator=InitialGenerator(value,error)
    compiler=WorkflowCompiler(generator,SCHEMA);compiler._bpmn_generator=Xml()
    return generator,compiler.compile("generic workflow")


def test_valid_two_participant_generation_survives_all_initial_stages():
    participants=[{"id":"u0","name":"u0","processRef":"p0","properties":{}},
                  {"id":"u1","name":"u1","processRef":"p1","properties":{}}]
    nodes=[n("s0","p0","startEvent","u0"),n("x0","p0","sendTask","u0"),n("e0","p0","endEvent","u0"),
           n("s1","p1","startEvent","u1"),n("x1","p1","receiveTask","u1"),n("e1","p1","endEvent","u1")]
    flows=[f("a0","s0","x0","p0"),f("a1","x0","e0","p0"),
           f("b0","s1","x1","p1"),f("b1","x1","e1","p1")]
    value=v2(("p0","p1"),nodes,flows,participants,messages=[{"id":"m","source":"x0","target":"x1","properties":{}}])
    _,result=compile_value(value)
    for stage in ("after_qwen_parse","after_normalization","after_initial_prepare"):
        assert result["pipeline_debug"][stage]["nodes"]==6
        assert result["pipeline_debug"][stage]["participants"]==2
    assert result["pipeline_debug"]["generation"]["parse_success"] is True


def test_fenced_json_is_extracted():
    assert extract_json_object('```json\n{"nodes": [{"id": "x"}]}\n```')["nodes"][0]["id"]=="x"


def test_json_surrounded_by_text_is_extracted():
    assert extract_json_object('prefix text {"nodes": [{"type": "serviceTask"}]} suffix')["nodes"][0]["type"]=="serviceTask"


def test_invalid_json_fails_generation_instead_of_creating_empty_workflow():
    generator,result=compile_value(error=ValueError("Objet JSON invalide"))
    assert result["error"]["stage"]=="generation"
    assert result["pipeline_debug"]["generation"]["parse_success"] is False
    assert result["pipeline_debug"]["after_initial_prepare"] is None
    assert generator.workflow_calls==1


def test_normalizer_preserves_ten_nodes():
    value=v2(nodes=[n(f"x{index}") for index in range(10)],flows=[])
    normalized,_=WorkflowNormalizer().normalize_with_report(value)
    assert len(normalized["nodes"])==10


def test_empty_initial_workflow_fails_before_repair_and_coverage():
    generator,result=compile_value({})
    assert result["error"]=={"stage":"generation","message":"La génération n'a produit aucun workflow exploitable."}
    assert result["repair_report"]==[] and result["coverage_errors"]==[]
    assert result["pipeline_debug"]["after_qwen_parse"]=={
        "processes":0,"participants":0,"lanes":0,"nodes":0,"flows":0,"messageFlows":0}
    assert result["pipeline_debug"]["after_normalization"] is None
    assert generator.workflow_calls==1


def test_normalizer_reports_total_collection_loss_as_error():
    value=v2(nodes=[None for _ in range(10)],flows=[])
    with pytest.raises(ValueError,match="nodes"):
        WorkflowNormalizer().normalize_with_report(value)
