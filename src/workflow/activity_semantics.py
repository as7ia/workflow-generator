"""Shared, deterministic semantic analysis for workflow activities."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any


SEMANTIC_STOPWORDS = {
    "le", "la", "les", "un", "une", "de", "du", "des", "au", "aux", "et",
    "pour", "sur", "dans", "par", "the", "to", "of", "and", "for", "service",
    "about", "effectuer", "effectue", "effectuent", "effectuera", "faire", "fait",
    "perform", "performs", "performed", "performing", "do", "does", "done",
}

SEMANTIC_ALIASES = {
    "entreprise": "company", "enterprise": "company", "company": "company",
    "client": "client", "customer": "client",
    "commercial": "sales", "sales": "sales", "sale": "sales",
    "financier": "finance", "financiere": "finance", "finance": "finance", "financial": "finance",
    "demande": "request", "request": "request", "requests": "request",
    "reception": "receive", "recevoir": "receive", "recoit": "receive", "recu": "receive",
    "receive": "receive", "receives": "receive", "received": "receive", "receiving": "receive",
    "verification": "verify", "verifier": "verify", "verifie": "verify", "verifient": "verify", "verifiera": "verify", "verify": "verify", "verifies": "verify",
    "verified": "verify", "checking": "verify", "check": "verify", "checked": "verify",
    "validate": "verify", "validates": "verify", "validated": "verify", "validation": "verify",
    "evaluate": "verify", "evaluates": "verify", "evaluated": "verify", "assessment": "verify",
    "assess": "verify", "assesses": "verify", "assessed": "verify",
    "acceptation": "accept", "accept": "accept", "accepts": "accept", "accepted": "accept",
    "approval": "accept", "approved": "accept", "approve": "accept", "approuver": "accept", "approuve": "accept",
    "rejet": "reject", "rejeter": "reject", "reject": "reject", "rejects": "reject", "rejection": "reject",
    "rejected": "reject", "refus": "reject", "refusal": "reject", "refuse": "reject",
    "envoyer": "send", "envoie": "send", "envoient": "send", "enverra": "send", "envoye": "send", "envoyee": "send", "envoi": "send", "send": "send", "sends": "send", "sent": "send",
    "submit": "send", "submits": "send", "submitted": "send",
    "transmettre": "forward", "transmission": "forward", "transmet": "forward", "transmettra": "forward", "transmis": "forward",
    "forward": "forward", "forwards": "forward", "forwarded": "forward", "transfer": "forward", "transfers": "forward",
    "informer": "notify", "informe": "notify", "informee": "notify", "informent": "notify", "informera": "notify", "inform": "notify", "informs": "notify",
    "notify": "notify", "notifies": "notify", "notified": "notify",
    "demander": "request", "demandera": "request", "ask": "request", "asks": "request", "asked": "request",
    "attendre": "wait", "attend": "wait", "attendra": "wait", "attente": "wait", "wait": "wait", "waits": "wait", "waiting": "wait",
    "reponse": "response", "response": "response",
    "document": "document", "documents": "document", "dossier": "document", "dossiers": "document", "file": "document", "files": "document",
    "commande": "order", "commandes": "order", "order": "order", "orders": "order",
    "information": "information", "informations": "information", "info": "information",
    "completude": "completeness", "completeness": "completeness", "complete": "completeness",
    "payer": "pay", "paie": "pay", "paiera": "pay", "paye": "pay", "paiement": "pay", "pay": "pay", "pays": "pay", "paid": "pay", "payment": "pay",
    "cloture": "close", "cloturer": "close", "close": "close", "closed": "close", "closing": "close",
    "again": "again", "repeat": "again", "repeated": "again", "recheck": "again", "nouveau": "again",
    "missing": "missing", "manquant": "missing", "manquante": "missing", "manquants": "missing", "manquantes": "missing",
    "confirmed": "confirmed", "confirmation": "confirmed", "confirme": "confirmed", "confirmee": "confirmed",
    "initial": "initial", "status": "status", "statut": "status",
}

ACTION_FAMILIES = {"send", "forward", "notify", "request", "receive", "verify", "accept", "reject", "pay", "wait", "close"}
REPETITION_MARKERS = {"again", "missing", "confirmed", "initial"}


def _normalized_sequence(text: Any) -> list[str]:
    normalized = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    result = []
    for raw in re.findall(r"[a-z0-9]+", normalized):
        if raw in SEMANTIC_STOPWORDS:
            continue
        token = SEMANTIC_ALIASES.get(raw, raw)
        if token not in SEMANTIC_ALIASES.values() and len(token) > 4 and token.endswith("s"):
            token = token[:-1]
        result.append(SEMANTIC_ALIASES.get(token, token))
    return result


def normalize_semantic_tokens(text: Any) -> set[str]:
    return set(_normalized_sequence(text))


def analyze_activity_semantics(value: Any) -> dict[str, Any]:
    """Split an activity label into action, business object, target and qualifiers."""
    text = value.get("name") if isinstance(value, Mapping) else value
    sequence = _normalized_sequence(text)
    action_index = next((i for i, token in enumerate(sequence) if token in ACTION_FAMILIES), None)
    action = sequence[action_index] if action_index is not None else None
    qualifiers = {token for token in sequence if token in REPETITION_MARKERS}
    remaining = [token for i, token in enumerate(sequence) if i != action_index and token not in qualifiers]

    # A second action word commonly acts as the content ("receive request").
    objects = set(remaining)
    target_actor = set()
    raw = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    target_match = re.search(r"\b(?:to|vers|a|au|aux)\s+([a-z0-9_-]+)", raw)
    if target_match:
        target_actor = normalize_semantic_tokens(target_match.group(1))
        objects -= target_actor
    elif action == "notify" and remaining:
        target_actor = {remaining[0]}
        objects.discard(remaining[0])

    return {
        "action_family": action,
        "actions": {action} if action else set(),
        "objects": objects,
        "target_actor": target_actor,
        "qualifiers": qualifiers,
        "repetition_marker": bool(qualifiers),
        "tokens": set(sequence),
    }


def semantic_analysis_for_report(analysis: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action_family": analysis.get("action_family"),
        "objects": sorted(analysis.get("objects", set())),
        "target_actor": sorted(analysis.get("target_actor", set())),
        "qualifiers": sorted(analysis.get("qualifiers", set())),
    }
