from src.workflow.semantic_requirements import normalize_semantic_requirements


def extracted(**values):
    result={key:[] for key in ("participants","lanes","timers","parallelisms","decisions","interactions","activities","ambiguities")}
    result.update(values)
    return result


def normalize(description,**values):
    return normalize_semantic_requirements(extracted(**values),description)[0]


def test_one_way_message_does_not_imply_a_reply():
    result=normalize("A sends a request to B. B reviews the request.",interactions=[{"source":"A","target":"B"},{"source":"B","target":"A"}])
    assert result["interactions"]==[{"source":"A","target":"B"}]


def test_explicit_reply_is_preserved():
    result=normalize("A sends a request to B. B replies to A.",interactions=[{"source":"A","target":"B"},{"source":"B","target":"A"}])
    assert result["interactions"]==[{"source":"A","target":"B"},{"source":"B","target":"A"}]


def test_state_change_does_not_imply_communication():
    result=normalize("B approves the request.",interactions=[{"source":"B","target":"A"}])
    assert result["interactions"]==[]


def test_explicit_notification_is_preserved():
    result=normalize("B notifies A that the request was approved.",interactions=[{"source":"B","target":"A"}])
    assert result["interactions"]==[{"source":"B","target":"A"}]


def test_description_without_time_discards_timer_and_artificial_ambiguity():
    result=normalize("B checks the request.",timers=[{"kind":"delay","value":"P1D","activity":"Check request"}])
    assert result["timers"]==[]
    assert not any(item.get("message")=="Malformed timer requirement." for item in result["ambiguities"])
    assert "P1D" not in repr(result)


def test_explicit_one_day_duration_preserves_timer():
    result=normalize("B waits one day before checking the request.",timers=[{"kind":"duration","value":"P1D","activity":"Check request"}])
    assert result["timers"]==[{"kind":"duration","value":"P1D","activity":"Check request"}]


def test_real_french_case_discards_invented_reply_and_timer():
    description=("Un client envoie une demande à une entreprise. L'entreprise vérifie la demande. "
                 "Si la demande est valide, elle est acceptée. Sinon, elle est rejetée.")
    result=normalize(description,
        participants=["Client","Entreprise"],
        activities=["Reception de la demande","Vérification de la demande","Acceptation de la demande","Rejet de la demande"],
        interactions=[{"source":"Client","target":"Entreprise"},{"source":"Entreprise","target":"Client"}],
        timers=[{"kind":"delay","value":"P1D","activity":"Vérification de la demande"}])
    assert result["interactions"]==[{"source":"Client","target":"Entreprise"}]
    assert result["timers"]==[]
    assert not any(item.get("message")=="Malformed timer requirement." for item in result["ambiguities"])
