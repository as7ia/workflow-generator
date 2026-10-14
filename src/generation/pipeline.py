"""End-to-end natural-language to analyzed BPMN workflow compilation."""

from __future__ import annotations

import copy
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

try:
    from ..bpmn import BPMNGenerator, BPMNLayoutEngine
    from ..workflow import (
        CanonicalWorkflowPreparer,
        WorkflowGraphAnalyzer,
        WorkflowNormalizer,
        WorkflowRepairer,
        CoverageValidator,
        normalize_semantic_requirements,
        ground_semantic_requirements,
        SemanticValidator,
        semantic_label_match,
        GroundedWorkflowCanonicalizer,
    )
except ImportError:  # pragma: no cover - supports PYTHONPATH=src notebooks.
    from bpmn import BPMNGenerator, BPMNLayoutEngine
    from workflow import (
        CanonicalWorkflowPreparer,
        WorkflowGraphAnalyzer,
        WorkflowNormalizer,
        WorkflowRepairer,
        CoverageValidator,
        normalize_semantic_requirements,
        ground_semantic_requirements,
        SemanticValidator,
        semantic_label_match,
        GroundedWorkflowCanonicalizer,
    )

from .prompts import build_generation_prompt, build_semantic_requirements_prompt


CompilationResult = dict[str, Any]


class _GenerationBudgetExceeded(RuntimeError):
    generation_budget_exhausted = True


class _PhaseGenerator:
    """Count and budget repair calls without changing repairer architecture."""

    def __init__(self, compiler: "WorkflowCompiler", result: CompilationResult, phase: str) -> None:
        self.compiler = compiler
        self.result = result
        self.phase = phase

    def generate_json(self, prompt: str) -> dict[str, Any]:
        return self.compiler._call_generator(self.result, self.phase, prompt)


class WorkflowCompiler:
    """Coordinate generation, normalization, repair, analysis and layout."""

    def __init__(
        self,
        generator: Any,
        schema_path: str | Path,
        max_repair_attempts: int = 3,
        max_structural_repair_attempts: int | None = None,
        max_coverage_repair_attempts: int | None = None,
        max_total_generation_calls: int = 4,
        initial_max_new_tokens: int = 4096,
        repair_max_new_tokens: int = 2048,
        requirements_max_new_tokens: int = 1024,
    ) -> None:
        self.generator = generator
        self.schema_path = Path(schema_path)
        self.max_repair_attempts = max_repair_attempts
        self.max_structural_repair_attempts = (
            max_repair_attempts if max_structural_repair_attempts is None
            else max_structural_repair_attempts
        )
        self.max_coverage_repair_attempts = (
            max_repair_attempts if max_coverage_repair_attempts is None
            else max_coverage_repair_attempts
        )
        limits = {
            "max_repair_attempts": max_repair_attempts,
            "max_structural_repair_attempts": self.max_structural_repair_attempts,
            "max_coverage_repair_attempts": self.max_coverage_repair_attempts,
            "max_total_generation_calls": max_total_generation_calls,
        }
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
               for value in limits.values()):
            raise ValueError("Les limites de gÃ©nÃ©ration doivent Ãªtre des entiers positifs ou nuls.")
        token_limits = (initial_max_new_tokens, repair_max_new_tokens, requirements_max_new_tokens)
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
               for value in token_limits):
            raise ValueError("Les limites de tokens doivent Ãªtre des entiers strictement positifs.")
        self.max_total_generation_calls = max_total_generation_calls
        self.initial_max_new_tokens = initial_max_new_tokens
        self.repair_max_new_tokens = repair_max_new_tokens
        self.requirements_max_new_tokens = requirements_max_new_tokens
        self._normalizer = WorkflowNormalizer()
        self._analyzer = WorkflowGraphAnalyzer()
        self._layout_engine = BPMNLayoutEngine()
        self._bpmn_generator = BPMNGenerator()

    def compile(self, description: str) -> CompilationResult:
        """Compile one natural-language description without leaking exceptions."""
        result = self._empty_result(description)
        try:
            schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
            if not isinstance(schema, dict):
                raise TypeError("Le JSON Schema doit Ãªtre un objet.")
        except Exception as exc:
            return self._fail(result, "schema_validation", exc)

        try:
            requirements_prompt=build_semantic_requirements_prompt(description)
            extracted=self._call_generator(result,"semantic_requirement",requirements_prompt)
            result["pipeline_debug"]["semantic_requirements_raw"]=copy.deepcopy(extracted)
            requirements,requirement_issues,grounding_report=ground_semantic_requirements(extracted,description)
            result["pipeline_debug"]["semantic_requirements_grounded"]=copy.deepcopy(requirements)
            result["pipeline_debug"]["requirement_grounding_report"]=copy.deepcopy(grounding_report)
            result["semantic_requirements"]=requirements
            if requirement_issues:return self._fail(result,"requirements_extraction",requirement_issues[0]["message"])
        except Exception as exc:
            return self._fail(result,"requirements_extraction",exc)

        try:
            prompt = build_generation_prompt(description, schema)
            generate_json = getattr(self.generator, "generate_json", None)
            if not callable(generate_json):
                raise TypeError(
                    "Le gÃ©nÃ©rateur doit exposer generate_json(prompt: str) -> dict."
                )
            raw = self._call_generator(result,"initial_generation",prompt)
            if not isinstance(raw, Mapping):
                raise TypeError("Le gÃ©nÃ©rateur doit retourner un dictionnaire JSON.")
            result["raw_workflow"] = copy.deepcopy(dict(raw))
            result["pipeline_debug"]["generation"] = self._generation_metadata(True)
            result["pipeline_debug"]["after_qwen_parse"] = self._workflow_counts(raw)
        except Exception as exc:
            result["pipeline_debug"]["generation"] = self._generation_metadata(False,exc)
            return self._fail(result, "generation", exc)

        if not self.is_meaningful_workflow(raw):
            return self._fail(result,"generation","La génération n'a produit aucun workflow exploitable.")

        preparer = CanonicalWorkflowPreparer(schema)
        try:
            normalized,normalization_report=self._normalizer.normalize_with_report(dict(raw))
            result["pipeline_debug"]["after_normalization"] = self._workflow_counts(normalized)
            if not self.is_meaningful_workflow(normalized):
                return self._fail(result,"generation","La génération n'a produit aucun workflow exploitable.")
            result["pipeline_debug"]["pre_canonical_workflow"] = self._workflow_counts(normalized)
            result["pipeline_debug"]["pre_canonical_entities"] = self._canonical_entities(normalized)
            canonical,canonicalization_report=GroundedWorkflowCanonicalizer.canonicalize(normalized,requirements)
            result["pipeline_debug"]["post_canonical_workflow"] = self._workflow_counts(canonical)
            result["pipeline_debug"]["post_canonical_entities"] = self._canonical_entities(canonical)
            result["pipeline_debug"]["canonicalization_report"] = canonicalization_report
            prepared = preparer.prepare_normalized_and_validate(canonical,normalization_report)
            current_workflow = copy.deepcopy(prepared["workflow"])
            self._assert_initial_prepare_preserved(normalized,current_workflow)
            preparation_reports=(prepared["normalization_report"],prepared["safe_repair_report"])
            current_workflow = self._align_semantic_roles_transactionally(current_workflow,requirements)
            prepared = preparer.validate_prepared(current_workflow)
            prepared["normalization_report"],prepared["safe_repair_report"]=preparation_reports
            if not self.is_meaningful_workflow(current_workflow):
                return self._fail(result,"generation","La génération n'a produit aucun workflow exploitable.")
            result["normalized_workflow"] = copy.deepcopy(current_workflow)
            result["pipeline_debug"]["after_initial_prepare"] = self._workflow_counts(current_workflow)
            result["warnings"].extend(
                entry for entry in prepared["normalization_report"] if entry.get("severity") == "warning"
            )
            result["warnings"].extend(prepared["safe_repair_report"])
        except Exception as exc:
            return self._fail(result, "normalization", exc)

        schema_errors = prepared["schema_errors"]
        semantic_errors = prepared["semantic_errors"]
        result["schema_errors"] = schema_errors
        result["semantic_errors"] = semantic_errors
        result["coverage_errors"]=[]
        blocking = self._blocking_errors(schema_errors, semantic_errors)

        if blocking:
            try:
                repairer = WorkflowRepairer(
                    max_attempts=self.max_structural_repair_attempts, schema=schema
                )
                repaired_workflow, repair_report = repairer.repair(
                    workflow=current_workflow,
                    errors=blocking,
                    generator=_PhaseGenerator(self,result,"structural_repair"),
                    workflow_is_prepared=True,
                )
                preservation=WorkflowRepairer.structural_preservation_report(current_workflow,repaired_workflow,blocking)
                if preservation["destructive_candidate"]:
                    repaired_workflow=copy.deepcopy(current_workflow)
                prepared = preparer.validate_prepared(repaired_workflow)
                accepted_workflow = copy.deepcopy(prepared["workflow"])
                result["warnings"].extend(
                    entry
                    for entry in prepared["normalization_report"]
                    if entry.get("severity") == "warning"
                )
                result["warnings"].extend(prepared["safe_repair_report"])
                result["repair_report"] = repair_report
                result["schema_errors"] = prepared["schema_errors"]
                result["semantic_errors"] = prepared["semantic_errors"]
                blocking = self._blocking_errors(
                    result["schema_errors"], result["semantic_errors"]
                )
                current_workflow = accepted_workflow
                result["normalized_workflow"] = copy.deepcopy(current_workflow)
                result["pipeline_debug"]["after_structural_repair"] = self._workflow_counts(current_workflow)
            except Exception as exc:
                return self._fail(result, "repair", exc)
            if blocking:
                result["pipeline_debug"]["final_current_workflow"] = self._workflow_counts(current_workflow)
                result["error"] = {
                    "stage": "repair",
                    "message": "Le meilleur workflow obtenu reste invalide aprÃ¨s les tentatives de rÃ©paration.",
                }
                return result

        result["pipeline_debug"]["after_structural_repair"] = self._workflow_counts(current_workflow)
        result["pipeline_debug"]["before_coverage_validation"] = self._workflow_counts(current_workflow)
        result["coverage_errors"],result["pipeline_debug"]["coverage_matching_report"]=CoverageValidator().validate_with_report(requirements,current_workflow)
        blocking=self._blocking_errors(result["coverage_errors"])
        if blocking:
            try:
                base_workflow=copy.deepcopy(current_workflow)
                required_coverage=self._requirements_for_errors(requirements,blocking)
                repairer=WorkflowRepairer(max_attempts=self.max_coverage_repair_attempts,schema=schema)
                coverage_candidate,coverage_report=repairer.repair(
                    workflow=copy.deepcopy(base_workflow),errors=blocking,
                    generator=_PhaseGenerator(self,result,"coverage_repair"),
                    requirements=required_coverage,repair_kind="coverage",workflow_is_prepared=True)
                candidate_prepared=preparer.validate_prepared(coverage_candidate)
                candidate_workflow=candidate_prepared["workflow"]
                attempted_counts=next((
                    attempt.get("coverage_repair",{}).get("candidate_counts")
                    for attempt in reversed(coverage_report)
                    if attempt.get("coverage_repair",{}).get("candidate_counts") is not None
                ),None)
                result["pipeline_debug"]["coverage_candidate"]=attempted_counts or self._workflow_counts(candidate_workflow)
                result["repair_report"].extend(coverage_report)
                candidate_accepted=any(
                    attempt.get("coverage_repair",{}).get("accepted_candidate") is True
                    for attempt in coverage_report
                )
                candidate_schema_errors=self._blocking_errors(candidate_prepared["schema_errors"])
                if (candidate_accepted and not candidate_schema_errors and
                        not self.is_catastrophic_regression(base_workflow,candidate_workflow,candidate_prepared["errors"])):
                    current_workflow=copy.deepcopy(candidate_workflow)
                else:
                    current_workflow=base_workflow
                prepared=preparer.validate_prepared(current_workflow)
                current_workflow=copy.deepcopy(prepared["workflow"])
                result["normalized_workflow"]=copy.deepcopy(current_workflow)
                result["schema_errors"]=prepared["schema_errors"];result["semantic_errors"]=prepared["semantic_errors"]
                result["coverage_errors"],result["pipeline_debug"]["coverage_matching_report"]=CoverageValidator().validate_with_report(requirements,current_workflow)
                blocking=self._blocking_errors(result["schema_errors"],result["semantic_errors"],result["coverage_errors"])
            except Exception as exc:
                result["normalized_workflow"]=copy.deepcopy(current_workflow)
                result["pipeline_debug"]["final_current_workflow"]=self._workflow_counts(current_workflow)
                return self._fail(result,"coverage_repair",exc)
            if blocking:
                result["normalized_workflow"]=copy.deepcopy(current_workflow)
                result["pipeline_debug"]["final_current_workflow"]=self._workflow_counts(current_workflow)
                result["error"]={"stage":"coverage_repair","message":"Le workflow ne couvre pas encore toutes les exigences."}
                return result

        result["pipeline_debug"]["final_current_workflow"] = self._workflow_counts(current_workflow)
        try:
            result["analysis"] = self._analyzer.analyze(current_workflow)
        except Exception as exc:
            return self._fail(result, "analysis", exc)

        try:
            result["layout"] = self._layout_engine.layout(
                current_workflow, result["analysis"]
            )
        except Exception as exc:
            return self._fail(result, "layout", exc)

        result["warnings"].extend(
            issue
            for issue in [*result["semantic_errors"],*result["coverage_errors"]]
            if issue.get("severity") == "warning"
        )
        result["warnings"].extend(result["analysis"].get("warnings", []))
        result["warnings"].extend(
            result["layout"].get("metadata", {}).get("warnings", [])
        )

        try:
            bpmn_xml = self._bpmn_generator.generate(current_workflow, result["layout"])
            if not isinstance(bpmn_xml, str) or not bpmn_xml.strip():
                raise ValueError("Le gÃ©nÃ©rateur BPMN a retournÃ© un document vide.")
            result["bpmn_xml"] = bpmn_xml
        except Exception as exc:
            return self._fail(result, "bpmn", exc)

        result["normalized_workflow"] = copy.deepcopy(current_workflow)
        result["pipeline_debug"]["final_current_workflow"] = self._workflow_counts(current_workflow)
        result["success"] = True
        return result

    def compile_and_save(self, description: str, output_dir: str) -> CompilationResult:
        """Compile and persist every available artifact in *output_dir*."""
        result = self.compile(description)
        try:
            destination = Path(output_dir)
            destination.mkdir(parents=True, exist_ok=True)
            artifacts = {
                "raw_workflow.json": result["raw_workflow"],
                "normalized_workflow.json": result["normalized_workflow"],
                "analysis.json": result["analysis"],
                "layout.json": result["layout"],
                "repair_report.json": result["repair_report"],
            }
            for filename, value in artifacts.items():
                (destination / filename).write_text(
                    json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            if isinstance(result.get("bpmn_xml"), str):
                (destination / "workflow.bpmn").write_text(
                    result["bpmn_xml"], encoding="utf-8"
                )
            report_copy = copy.deepcopy(result)
            report_copy["bpmn_xml"] = None if result.get("bpmn_xml") else result.get("bpmn_xml")
            (destination / "compilation_report.json").write_text(
                json.dumps(report_copy, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            return self._fail(result, "bpmn", f"Ã‰chec de sauvegarde : {exc}")
        return result

    @staticmethod
    def _empty_result(description: str) -> CompilationResult:
        """Build the stable public result structure."""
        return {
            "success": False,
            "description": description,
            "raw_workflow": {},
            "normalized_workflow": {},
            "schema_errors": [],
            "semantic_errors": [],
            "semantic_requirements": {},
            "coverage_errors": [],
            "repair_report": [],
            "analysis": {},
            "layout": {},
            "bpmn_xml": None,
            "warnings": [],
            "error": None,
            "generation_stats": {
                "initial_generation_calls": 0,
                "structural_repair_calls": 0,
                "coverage_repair_calls": 0,
                "semantic_requirement_calls": 0,
                "total_qwen_calls": 0,
            },
            "prompt_stats": {
                "initial_prompt_chars": None,
                "last_structural_repair_prompt_chars": None,
                "last_coverage_repair_prompt_chars": None,
            },
            "pipeline_debug": {
                "generation": {"raw_text_length": None,"json_extracted": False,"parse_success": False},
                "after_qwen_parse": None,
                "after_normalization": None,
                "after_initial_prepare": None,
                "after_structural_repair": None,
                "before_coverage_validation": None,
                "coverage_candidate": None,
                "final_current_workflow": None,
                "semantic_requirements_raw": None,
                "semantic_requirements_grounded": None,
                "requirement_grounding_report": None,
                "pre_canonical_workflow": None,
                "post_canonical_workflow": None,
                "canonicalization_report": None,
                "pre_canonical_entities": None,
                "post_canonical_entities": None,
                "coverage_matching_report": None,
            },
        }

    def _call_generator(
        self, result: CompilationResult, phase: str, prompt: str
    ) -> dict[str, Any]:
        """Apply the global budget, phase token limit and non-sensitive metrics."""
        stats = result["generation_stats"]
        if stats["total_qwen_calls"] >= self.max_total_generation_calls:
            raise _GenerationBudgetExceeded(
                f"Limite globale de {self.max_total_generation_calls} appels Qwen atteinte."
            )
        mapping = {
            "initial_generation": ("initial_generation_calls", self.initial_max_new_tokens,
                                   "initial_prompt_chars"),
            "structural_repair": ("structural_repair_calls", self.repair_max_new_tokens,
                                  "last_structural_repair_prompt_chars"),
            "coverage_repair": ("coverage_repair_calls", self.repair_max_new_tokens,
                                "last_coverage_repair_prompt_chars"),
            "semantic_requirement": ("semantic_requirement_calls", self.requirements_max_new_tokens,
                                     None),
        }
        counter, token_limit, prompt_stat = mapping[phase]
        stats[counter] += 1
        stats["total_qwen_calls"] += 1
        if prompt_stat is not None:
            result["prompt_stats"][prompt_stat] = len(prompt)
        generate_json = getattr(self.generator, "generate_json", None)
        if not callable(generate_json):
            raise TypeError("Le gÃ©nÃ©rateur doit exposer generate_json(prompt: str) -> dict.")
        try:
            supports_limit = "max_new_tokens" in inspect.signature(generate_json).parameters
        except (TypeError, ValueError):
            supports_limit = False
        if supports_limit:
            return cast(dict[str, Any], generate_json(prompt, max_new_tokens=token_limit))
        return cast(dict[str, Any], generate_json(prompt))

    @classmethod
    def _align_semantic_roles_transactionally(cls,workflow: dict[str,Any],requirements: Mapping[str,Any])->dict[str,Any]:
        """Accept a node-local role alignment only when collaboration and validation are preserved."""
        original=copy.deepcopy(workflow)
        candidate=cls._build_semantic_role_candidate(original,requirements)
        return cls._accept_semantic_alignment_candidate(original,candidate)

    @classmethod
    def _accept_semantic_alignment_candidate(cls,original: dict[str,Any],candidate: dict[str,Any])->dict[str,Any]:
        if not cls._semantic_alignment_preserves_structure(original,candidate):return copy.deepcopy(original)
        before=cls._blocking_error_markers(SemanticValidator().validate(original))
        after=cls._blocking_error_markers(SemanticValidator().validate(candidate))
        return copy.deepcopy(candidate if after<=before else original)

    @classmethod
    def _build_semantic_role_candidate(cls,workflow: dict[str,Any],requirements: Mapping[str,Any])->dict[str,Any]:
        candidate=copy.deepcopy(workflow)
        nodes=candidate.get("nodes",[]);flows=candidate.get("flows",[]);messages=candidate.get("messageFlows",[])
        activities=requirements.get("activities",[]) if isinstance(requirements,Mapping) else []
        if not all(isinstance(value,list) for value in (nodes,flows,messages,activities)):return candidate
        message_sources={item.get("source") for item in messages if isinstance(item,Mapping)}
        message_targets={item.get("target") for item in messages if isinstance(item,Mapping)}
        used_nodes={item.get("id") for item in nodes if isinstance(item,Mapping)}
        used_flows={item.get("id") for item in flows if isinstance(item,Mapping)}
        task_types={"userTask","serviceTask","manualTask","scriptTask","businessRuleTask","sendTask","receiveTask","subProcess"}
        for requirement in activities:
            name=requirement.get("name") if isinstance(requirement,Mapping) else None
            if not isinstance(name,str):continue
            matches=[node for node in nodes if isinstance(node,dict) and semantic_label_match(name,node.get("name"))]
            if any(node.get("type") in task_types for node in matches):continue
            events=[node for node in matches if node.get("type") in {"startEvent","endEvent"}]
            if len(events)!=1:continue
            node=events[0];node_id=node.get("id");event_type=node.get("type")
            node["type"]="sendTask" if node_id in message_sources else "receiveTask" if node_id in message_targets else "serviceTask"
            base=f"{'start' if event_type=='startEvent' else 'end'}_{node.get('processRef') or 'process'}"
            event_id=cls._unique_semantic_id(base,used_nodes);used_nodes.add(event_id)
            nodes.append({"id":event_id,"type":event_type,"name":"Start" if event_type=="startEvent" else "End",
                          "processRef":node.get("processRef"),"participantRef":node.get("participantRef"),
                          "laneRef":node.get("laneRef"),"actor":None,"properties":{"synthetic":True}})
            source,target=(event_id,node_id) if event_type=="startEvent" else (node_id,event_id)
            flow_id=cls._unique_semantic_id(f"flow_{source}_{target}",used_flows);used_flows.add(flow_id)
            flows.append({"id":flow_id,"source":source,"target":target,"processRef":node.get("processRef"),
                          "condition":None,"isDefault":False,"properties":{"synthetic":True}})
        return candidate

    @staticmethod
    def _semantic_alignment_preserves_structure(before: Mapping[str,Any],after: Mapping[str,Any])->bool:
        def by_id(workflow,key):
            value=workflow.get(key,[]) if isinstance(workflow,Mapping) else []
            return {item.get("id"):item for item in value if isinstance(item,Mapping) and item.get("id")}
        for key in ("processes","participants","lanes"):
            left,right=by_id(before,key),by_id(after,key)
            if set(left)!=set(right):return False
        for identifier,item in by_id(before,"participants").items():
            if by_id(after,"participants")[identifier].get("processRef")!=item.get("processRef"):return False
        for identifier,item in by_id(before,"lanes").items():
            other=by_id(after,"lanes")[identifier]
            if (other.get("processRef"),other.get("participantRef"))!=(item.get("processRef"),item.get("participantRef")):return False
        for identifier,item in by_id(before,"nodes").items():
            other=by_id(after,"nodes").get(identifier)
            if other is None:return False
            if tuple(other.get(key) for key in ("processRef","participantRef","laneRef"))!=tuple(item.get(key) for key in ("processRef","participantRef","laneRef")):return False
        for identifier,item in by_id(before,"flows").items():
            other=by_id(after,"flows").get(identifier)
            if other is None or other.get("processRef")!=item.get("processRef"):return False
        return by_id(before,"messageFlows")==by_id(after,"messageFlows")

    @staticmethod
    def _blocking_error_markers(errors: list[dict[str,Any]])->set[tuple[Any,...]]:
        return {(error.get("code"),error.get("process_id"),error.get("participant_id"),error.get("lane_id"),
                 error.get("node_id"),error.get("flow_id"),error.get("message"))
                for error in errors if error.get("severity","error")=="error"}

    @staticmethod
    def _unique_semantic_id(base: str,used: set[Any])->str:
        candidate=base;suffix=2
        while candidate in used:candidate=f"{base}_{suffix}";suffix+=1
        return candidate

    @staticmethod
    def _workflow_counts(workflow: Mapping[str, Any]) -> dict[str, int]:
        return {
            key: len(workflow.get(key, [])) if isinstance(workflow.get(key), list) else 0
            for key in ("processes", "participants", "lanes", "nodes", "flows", "messageFlows")
        }

    @staticmethod
    def _canonical_entities(workflow: Mapping[str,Any])->dict[str,list[dict[str,Any]]]:
        def select(key,fields):
            values=workflow.get(key,[]) if isinstance(workflow,Mapping) else []
            return [{field:item.get(field) for field in fields} for item in values if isinstance(item,Mapping)]
        nodes=select("nodes",("id","type","name","processRef","participantRef"))
        return {
            "participants":select("participants",("id","name","processRef")),
            "processes":select("processes",("id","name")),
            "interaction_nodes":[node for node in nodes if node.get("type") in {"sendTask","receiveTask"}],
        }

    @staticmethod
    def is_meaningful_workflow(workflow: Mapping[str,Any]) -> bool:
        """Return whether generation contains at least one real BPMN node."""
        if not isinstance(workflow,Mapping):return False
        nodes=workflow.get("nodes")
        return (isinstance(nodes,list) and bool(nodes) and
                any(isinstance(node,Mapping) and isinstance(node.get("type"),str) and node["type"].strip()
                    for node in nodes))

    def _generation_metadata(self,success:bool,cause:Exception|None=None)->dict[str,Any]:
        metadata=getattr(self.generator,"last_generation_metadata",None)
        result=copy.deepcopy(dict(metadata)) if isinstance(metadata,Mapping) else {}
        result.setdefault("raw_text_length",None)
        result["json_extracted"]=bool(result.get("json_extracted",success))
        result["parse_success"]=bool(result.get("parse_success",success))
        if cause is not None:
            result["json_extracted"]=False;result["parse_success"]=False
            result["cause"]=str(cause)
        return result

    @classmethod
    def _assert_initial_prepare_preserved(cls,before:Mapping[str,Any],after:Mapping[str,Any])->None:
        """Fail if initial preparation silently erases existing structure."""
        previous=cls._workflow_counts(before);current=cls._workflow_counts(after)
        for key in ("nodes","participants"):
            if previous[key] and not current[key]:
                raise ValueError(f"La prÃ©paration initiale a supprimÃ© tous les Ã©lÃ©ments de '{key}'.")
        previous_edges=previous["flows"]+previous["messageFlows"]
        current_edges=current["flows"]+current["messageFlows"]
        if previous_edges and not current_edges:
            raise ValueError("La prÃ©paration initiale a supprimÃ© tous les flux.")

    @classmethod
    def is_catastrophic_regression(
        cls, before: Mapping[str, Any], candidate: Mapping[str, Any],
        candidate_errors: list[dict[str, Any]] | None = None,
    ) -> bool:
        before_counts=cls._workflow_counts(before);candidate_counts=cls._workflow_counts(candidate)
        if before_counts["nodes"] and not candidate_counts["nodes"]:return True
        if before_counts["participants"] and not candidate_counts["participants"]:return True
        if before_counts["processes"]>1 and candidate_counts["processes"]<=1:return True
        codes=[str(error.get("code","")) for error in candidate_errors or [] if error.get("severity","error")=="error"]
        if "JSON_SCHEMA_ERROR" in codes:return True
        unknown_codes={"UNKNOWN_NODE_PROCESS","UNKNOWN_MESSAGE_FLOW_SOURCE","UNKNOWN_MESSAGE_FLOW_TARGET"}
        return sum(code in unknown_codes for code in codes)>=3

    @staticmethod
    def _blocking_errors(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(error)
            for group in groups
            for error in group
            if error.get("severity", "error") == "error"
        ]

    @staticmethod
    def _requirements_for_errors(
        requirements: dict[str, Any], errors: list[dict[str, Any]]
    ) -> dict[str, list[Any]]:
        """Keep only requirements referenced by blocking coverage errors."""
        code_to_kind = {
            "MISSING_PARTICIPANT": "participants", "MISSING_LANE": "lanes",
            "MISSING_TIMER": "timers", "MISSING_PARALLELISM": "parallelisms",
            "MISSING_DECISION": "decisions", "MISSING_INTERACTION": "interactions",
            "MISSING_ACTIVITY": "activities",
        }
        selected = {kind: [] for kind in (
            "participants", "lanes", "timers", "parallelisms", "decisions",
            "interactions", "activities", "ambiguities",
        )}
        for error in errors:
            code = error.get("code")
            kind = code_to_kind.get(code) if isinstance(code, str) else None
            requirement = error.get("requirement")
            if kind and isinstance(requirement, Mapping) and requirement not in selected[kind]:
                selected[kind].append(copy.deepcopy(dict(requirement)))
        return selected

    def _apply_safe_repairs(
        self,
        workflow: dict[str, Any],
        errors: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Apply canonical v2 safe repairs through the shared preparation policy."""
        del errors
        return CanonicalWorkflowPreparer.apply_safe_repairs(workflow)

    @staticmethod
    def _fail(
        result: CompilationResult, stage: str, error: Exception | str
    ) -> CompilationResult:
        """Convert an exception into a notebook-safe structured failure."""
        result["success"] = False
        result["error"] = {"stage": stage, "message": str(error)}
        return result
