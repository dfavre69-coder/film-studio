"""Reprise automatique : un incident passager n'arrête pas la production."""
from __future__ import annotations

from filmstudio import comfy, production
from filmstudio.demo import film_exemple
from filmstudio.production import Production
from filmstudio.projet import Film
from tests.faux_comfy import WORKFLOW_VIDEO


def _prod(faux, tmp_path, monkeypatch, **reglages):
    comfy.enregistrer_workflow(comfy.Workflow("ltx", "video", WORKFLOW_VIDEO, comfy.deviner_carte(WORKFLOW_VIDEO)))
    film = film_exemple()
    film.enregistrer(tmp_path / "film")
    attentes = []
    monkeypatch.setattr(Production, "_patienter", lambda self, s, raison: attentes.append((s, raison)))
    return film, Production(film, faux.reglages.copie(comfy_workflow_video="ltx", **reglages)), attentes


def test_quota_chatgpt_atteint_puis_reprise(faux, tmp_path, monkeypatch):
    compteur = tmp_path / "echecs"
    compteur.write_text("2")              # les 2 premières images échouent (quota), puis ça repart
    monkeypatch.setenv("FAUX_CODEX_ECHECS", str(compteur))
    film, prod, attentes = _prod(faux, tmp_path, monkeypatch)
    chemin, b = prod.tout()
    assert chemin and chemin.exists() and not b.problemes, b.texte()
    assert [s for s, _ in attentes] == [900, 900]              # 15 min d'attente à chaque fois
    assert "Limite" in attentes[0][1]
    assert sum("Reprise automatique" in f for f in b.faits) == 2
    assert all(p.prise for p in film.plans)


def test_comfyui_a_court_de_memoire_puis_reprise(faux, tmp_path, monkeypatch):
    faux.comfy.pannes = 1
    film, prod, attentes = _prod(faux, tmp_path, monkeypatch, moteur_images="manuel")
    for i in (0, 2):
        img = film.sous_dossier("images") / f"plan_{i + 1:02d}.png"
        from PIL import Image
        Image.new("RGB", (1280, 720)).save(img)
        film.plans[i].image = film.relatif(img)
    chemin, b = prod.tout()
    assert chemin and not b.problemes, b.texte()
    assert attentes == [(production.DELAIS_REPRISE["comfy"], attentes[0][1])] and "OutOfMem" in attentes[0][1]
    assert [len(p.prises) for p in film.plans] == [1, 1, 1]


def test_incident_qui_se_repete(faux, tmp_path, monkeypatch):
    faux.comfy.pannes = 99
    film, prod, attentes = _prod(faux, tmp_path, monkeypatch, moteur_images="manuel")
    from PIL import Image
    img = film.sous_dossier("images") / "plan_01.png"
    Image.new("RGB", (1280, 720)).save(img)
    film.plans[0].image = film.relatif(img)
    film.plans[2].image = film.relatif(img)
    chemin, b = prod.tout(exporter=False)
    assert len(attentes) == production.REPRISES_SANS_PROGRES
    assert any("abandonnée" in p for p in b.problemes)


def test_drapeau_production_en_cours(tmp_path):
    film = film_exemple()
    film.production_en_cours = True
    film.enregistrer(tmp_path / "f")
    assert Film.ouvrir(tmp_path / "f").production_en_cours


def test_relance_au_demarrage_de_windows(tmp_path, monkeypatch):
    from filmstudio import gui
    monkeypatch.setattr(gui.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    dossier = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    dossier.mkdir(parents=True)
    gui.relance_au_demarrage(True)
    lanceur = dossier / "Film Studio (reprise).cmd"
    assert "-m filmstudio" in lanceur.read_text(encoding="ascii") and "pythonw.exe" in lanceur.read_text()
    gui.relance_au_demarrage(False)
    assert not lanceur.exists()
