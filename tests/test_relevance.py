from datetime import date

from services.relevance import keyword_hits, make_excerpt, triage

TODAY = date(2026, 10, 29)


def test_keyword_hits_word_boundaries_and_accents():
    assert keyword_hits("De huurder en de verhuurder; enquêteprocedure", ["huur", "huurder", "enquêteprocedure"]) == {"huurder": 1, "enquêteprocedure": 1}


def test_triage_relevant_without_llm(settings):
    doc = {"title": "Wijziging Wet betaalbare huur: huurprijs en huurverhoging", "published_at": "2026-10-28",
           "jurisdiction": "NL", "content": "De verhuurder mag de huurprijs ... huurcommissie ... huurder " * 5}
    t = triage(doc, settings, TODAY)
    assert t.status == "relevant" and "rental" in t.topics and t.excerpt


def test_triage_rejected_outside_window_and_no_signal(settings):
    assert triage({"title": "huur", "published_at": "2026-01-01", "content": ""}, settings, TODAY).reason == "outside window"
    assert triage({"title": "Visserijbesluit", "published_at": "2026-10-28", "content": "vis vis"}, settings, TODAY).status == "rejected"
    assert triage({"title": "huur BES-eilanden", "published_at": "2026-10-28", "content": ""}, settings, TODAY).reason == "excluded topic"


def test_triage_ambiguous_goes_to_llm(settings):
    doc = {"title": "Besluit wijziging Kadasterbesluit", "published_at": "2026-10-28",
           "content": "de bestuurder van de vennootschap; de aandeelhouder; het handelsregister; de jaarrekening"}
    t = triage(doc, settings, TODAY)
    assert t.status == "ambiguous" and t.topics == ["corporate"]
    weak = {"title": "Besluit wijziging Kadasterbesluit", "published_at": "2026-10-28", "content": "de bestuurder rijdt"}
    assert triage(weak, settings, TODAY).status == "rejected"


def test_excerpt_windows_only():
    text = ("x " * 400) + "hier staat huurprijs." + (" y" * 400)
    ex = make_excerpt(text, ["huurprijs"], 2500)
    assert "huurprijs" in ex and len(ex) < 800


def test_triage_excludes_notices_by_type_and_title(settings):
    base = {"published_at": "2026-10-28", "content": "vennootschap aandelen bestuurder " * 10}
    assert triage({**base, "title": "Openbaar exploot", "doc_type": "overige overheidsinformatie"}, settings, TODAY).reason == "excluded doc type"
    assert triage({**base, "title": "Ontbindingen Kamer van Koophandel", "doc_type": "AMvB"}, settings, TODAY).reason == "excluded title pattern"
    assert triage({**base, "title": "Wet wijziging Boek 2 vennootschap", "doc_type": "Wet"}, settings, TODAY).status == "relevant"
