from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from filmstudio import comfy, forfait, ia, video
from filmstudio.demo import film_exemple
from filmstudio.production import Production, prompt_image_plan, prompt_video
from filmstudio.projet import Film, Replique, ecrire_repliques, identifiant, lire_repliques
from tests.faux_comfy import WORKFLOW_IMAGE, WORKFLOW_VIDEO

REPONSE_IA = """Voici le découpage :
```json
{
  "titre": "Le phare",
  "personnages": [{"nom": "Mila", "description": "une petite gardienne", "apparence": "a small girl, red raincoat", "voix": "douce"},
                  {"nom": "Gaspard", "description": "un goéland", "apparence": "a grumpy seagull, yellow beak"}],
  "lieux": [{"nom": "Le phare", "apparence": "an old lighthouse on a cliff at dusk"}],
  "plans": [
    {"titre": "Arrivée", "lieu": "Le phare", "personnages": ["Mila"], "image": "Mila at the door", "mouvement": "Mila opens the door",
     "camera": "plan large", "duree": "5 s", "enchainer": true, "repliques": [{"personnage": "Mila", "texte": "Bonsoir !"}]},
    {"titre": "Rencontre", "lieu": "La plage", "personnages": ["Mila", "Gaspard", "Bob"], "image": "x", "mouvement": "y",
     "duree": 40, "enchainer": true, "repliques": [{"personnage": "Gaspard", "texte": "Kia !"}, {"personnage": "Inconnu", "texte": "Hé"}, "Le vent souffle."]},
  ],
}
```"""


def _workflow(nom="ltx", genre="video", graphe=None):
    g = graphe or (WORKFLOW_VIDEO if genre == "video" else WORKFLOW_IMAGE)
    wf = comfy.Workflow(nom, genre, g, comfy.deviner_carte(g, genre))
    comfy.enregistrer_workflow(wf)
    return wf


# ---------------------------------------------------------------------------
def test_projet_aller_retour(tmp_path):
    film = film_exemple()
    film.enregistrer(tmp_path / "f")
    relu = Film.ouvrir(tmp_path / "f")
    assert relu.vers_dict() == film.vers_dict()
    assert relu.personnage("Papi Lueur").id == "papi_lueur"
    assert relu.lieu("clairiere").nom == "La clairière des lucioles"
    assert (tmp_path / "f" / "clips").is_dir()


def test_repliques_texte():
    reps = lire_repliques("Lumi : J'ai peur.\nLa nuit tombe.\n\nPapi Lueur: Viens ! Voir https://x.fr")
    assert [r.personnage for r in reps] == ["Lumi", "Narrateur", "Papi Lueur"]
    assert lire_repliques(ecrire_repliques(reps)) == reps
    assert identifiant("La Forêt d'été", {"la_foret_d_ete"}) == "la_foret_d_ete_2"


def test_film_depuis_ia_corrige_les_ecarts():
    base = film_exemple()
    base.regler_format("9:16")
    film, remarques = ia.film_depuis_ia(REPONSE_IA, base, duree_plan=6, duree_max=12)
    assert film.titre == "Le phare" and film.format == "9:16" and film.largeur == 720
    assert [p.nom for p in film.personnages] == ["Mila", "Gaspard", "Bob"]
    assert film.plans[0].enchainer is False          # le premier plan ne peut pas s'enchaîner
    assert film.plans[0].duree == 5 and film.plans[1].duree == 12
    assert film.lieu("La plage") is not None
    assert [r.personnage for r in film.plans[1].repliques] == ["Gaspard", "Narrateur", "Narrateur"]
    assert any("Bob" in r for r in remarques) and any("plage" in r for r in remarques)


def test_prompt_construit():
    film = film_exemple()
    p = ia.construire_prompt(film, "Une histoire de phare", 4, 5, 10)
    assert "Une histoire de phare" in p and "4 plans" in p and "{{" not in p


def test_deviner_carte_ltx():
    carte = comfy.deviner_carte(WORKFLOW_VIDEO, "video")
    assert carte["prompt"] == [["2", "text"]] and carte["negatif"] == [["3", "text"]]
    assert carte["image"] == [["4", "image"]] and carte["graine"] == [["7", "seed"]]
    assert carte["images"] == [["5", "length"]]
    assert sorted(carte["fps"]) == [["6", "frame_rate"], ["9", "fps"]]
    assert carte["largeur"] == [["5", "width"]]
    g = comfy.appliquer(WORKFLOW_VIDEO, carte, {"prompt": "hello", "fps": 24, "images": 145, "graine": 7})
    assert g["2"]["inputs"]["text"] == "hello" and g["6"]["inputs"]["frame_rate"] == 24.0
    assert isinstance(g["9"]["inputs"]["fps"], float) and g["5"]["inputs"]["length"] == 145
    assert WORKFLOW_VIDEO["2"]["inputs"]["text"] == "a cat"   # l'original n'est pas modifié
    assert comfy.nombre_images(6, 24, 8) == 145 and comfy.nombre_images(5, 16, 4) == 81


def test_format_interface_refuse(tmp_path):
    f = tmp_path / "ui.json"
    f.write_text(json.dumps({"nodes": [], "links": []}))
    with pytest.raises(comfy.ErreurComfy, match="Exporter \\(API\\)"):
        comfy.lire_graphe(f)


def test_codex_recoit_les_references(faux, tmp_path):
    film = film_exemple()
    film.enregistrer(tmp_path / "film")
    prod = Production(film, faux.reglages)
    b = prod.references()
    assert len(b.faits) == 4 and not b.problemes
    assert all(film.chemin(p.image).exists() for p in film.personnages + film.lieux)
    b = prod.images()
    # Le plan 2 s'enchaîne au plan 1 : pas d'image à dessiner
    assert film.plans[1].image == "" and film.plans[2].image == "images/plan_03.png"
    j = [x for x in faux.lire_journal() if x["outil"] == "codex"]
    assert not any(x["cle_api"] for x in j)                 # l'abonnement, jamais la clé API
    dernier = j[-1]
    assert [Path(x).name for x in dernier["images"]] == ["perso_lumi.png", "perso_papi_lueur.png", "lieu_clairiere.png"]
    assert "Respecte EXACTEMENT" in dernier["prompt"]
    # « --image » est suivi de « -C » : la liste des fichiers est bien close
    assert dernier["args"][dernier["args"].index("--image") + 2] == "-C"


def test_codex_limite_bloquante(faux, tmp_path, monkeypatch):
    monkeypatch.setenv("FAUX_CODEX_LIMITE", "1")
    film = film_exemple()
    film.enregistrer(tmp_path / "film")
    b = Production(film, faux.reglages).references()
    assert len(b.problemes) == 1 and "Limite" in b.problemes[0]   # arrêt dès la première


def test_claude_ecrit_le_decoupage(faux, tmp_path, monkeypatch):
    rep = tmp_path / "rep.txt"
    rep.write_text(REPONSE_IA, encoding="utf-8")
    monkeypatch.setenv("FAUX_REPONSE", str(rep))
    film = film_exemple()
    texte = ia.generer(ia.construire_prompt(film, "phare"), faux.reglages)
    nouveau, _ = ia.film_depuis_ia(texte, film)
    assert nouveau.titre == "Le phare"
    j = faux.lire_journal()[-1]
    assert j["outil"] == "claude" and not j["cle_api"] and "-p" in j["args"]
    assert "phare" not in forfait.verifier_claude(faux.reglages)  # petit essai « OK »
    monkeypatch.setenv("FAUX_CLAUDE_DECONNECTE", "1")
    with pytest.raises(forfait.ErreurForfait, match="connecté"):
        ia.generer("x", faux.reglages)


def test_verifier_codex(faux):
    assert "codex-cli" in forfait.verifier_codex(faux.reglages)


def test_film_complet_de_bout_en_bout(faux, tmp_path):
    _workflow("ltx")
    r = faux.reglages.copie(comfy_workflow_video="ltx")
    film = film_exemple()
    film.musique = ""
    film.enregistrer(tmp_path / "film")
    etats = []
    prod = Production(film, r, etat=etats.append)
    chemin, b = prod.tout()
    assert not b.problemes, b.texte()
    assert chemin.exists() and chemin.with_suffix(".srt").exists()
    # Voix : une par personnage (ici toutes avec la voix du narrateur), répliques assemblées par plan
    assert all(p.voix and film.chemin(p.voix).exists() for p in film.plans)
    # Vidéos : une prise par plan, durée allongée si les répliques sont longues
    assert [len(p.prises) for p in film.plans] == [1, 1, 1]
    graphes = faux.comfy.recus
    assert len(graphes) == 3
    assert graphes[0]["2"]["inputs"]["text"].startswith("Lumi looks left and right")
    assert graphes[0]["3"]["inputs"]["text"] == film.negatif
    assert graphes[0]["5"]["inputs"]["width"] == 1280 and graphes[0]["5"]["inputs"]["length"] % 8 == 1
    # Le plan 2 part de la dernière image du plan 1
    assert (film.dossier / "images" / "plan_02_suite.png").exists()
    # Le film monté : durée ≈ somme des plans, son présent
    i = video.infos(chemin)
    assert i["audio"] and i["video"] and i["taille"] == (1280, 720)
    assert abs(i["duree"] - film.duree_totale()) < 1.5
    srt = chemin.with_suffix(".srt").read_text(encoding="utf-8")
    assert "Dans le noir, c'est toi qui brilles." in srt and srt.count("-->") == 3
    # Rien n'est refait la deuxième fois
    n = len(faux.comfy.recus)
    _, b2 = Production(film, r).tout()
    assert len(faux.comfy.recus) == n
    # Nouvelle prise d'un plan : la prise choisie devient la nouvelle
    Production(film, r).videos([2], nouvelle_prise=True)
    assert len(film.plans[2].prises) == 2 and film.plans[2].prise_choisie == 1
    relu = Film.ouvrir(film.dossier)
    assert relu.plans[2].prise.fichier.endswith("prise_2.mp4")


def test_voix_refaites_seulement_si_changees(faux, tmp_path):
    film = film_exemple()
    film.enregistrer(tmp_path / "film")
    prod = Production(film, faux.reglages)
    prod.voix()
    n = len([x for x in faux.lire_journal() if x["outil"] == "tts"])
    prod.voix()
    assert len([x for x in faux.lire_journal() if x["outil"] == "tts"]) == n
    film.plans[0].repliques = [Replique("Lumi", "Autre texte.")]
    prod.voix()
    derniers = [x for x in faux.lire_journal() if x["outil"] == "tts"][n:]
    assert sum(len(x["textes"]) for x in derniers) == 1


def test_voix_propre_a_un_personnage(faux, tmp_path):
    film = film_exemple()
    film.enregistrer(tmp_path / "film")
    voix = film.dossier / "papi.wav"
    voix.write_bytes(b"RIFF")
    film.personnage("papi_lueur").voix_reference = film.relatif(voix)
    Production(film, faux.reglages).voix()
    tts = [x for x in faux.lire_journal() if x["outil"] == "tts"]
    assert {Path(x["voix"]).name for x in tts} == {"narrateur.wav", "papi.wav"}


def test_images_par_comfyui(faux, tmp_path):
    _workflow("flux", "image")
    r = faux.reglages.copie(moteur_images="comfyui", comfy_workflow_image="flux")
    film = film_exemple()
    film.enregistrer(tmp_path / "film")
    prod = Production(film, r)
    assert not prod.references().problemes
    with Image.open(film.chemin(film.personnages[0].image)) as img:
        assert img.size == (1024, 1024)
    assert not prod.images().problemes
    with Image.open(film.chemin(film.plans[0].image)) as img:
        assert img.size == (1280, 720)


def test_plan_enchaine_sans_precedent(faux, tmp_path):
    _workflow("ltx")
    r = faux.reglages.copie(comfy_workflow_video="ltx", moteur_images="manuel")
    film = film_exemple()
    film.enregistrer(tmp_path / "film")
    b = Production(film, r).videos([1])
    assert "Rendez d'abord le plan 1" in b.problemes[0]


def test_workflow_incomplet(faux, tmp_path):
    g = {k: v for k, v in WORKFLOW_VIDEO.items() if k != "4"}
    wf = comfy.Workflow("sans_image", "video", g, comfy.deviner_carte(g))
    assert wf.manquants() == ["image"]
    with pytest.raises(comfy.ErreurComfy, match="Image de départ"):
        comfy.ClientComfy(faux.comfy.url).rendre(wf, tmp_path / "x.mp4", "p", image=tmp_path / "x.png")


def test_comfy_eteint(tmp_path):
    with pytest.raises(comfy.ErreurComfy, match="Est-il lancé"):
        comfy.ClientComfy("http://127.0.0.1:9").description()


def test_prompts(tmp_path):
    film = film_exemple()
    film.mode_voix = "modele"
    p = prompt_video(film, film.plans[2])
    assert "Papi Lueur says in français" in p and film.style in p
    film.enregistrer(tmp_path / "f")
    texte, refs = prompt_image_plan(film, 0)
    assert refs == [] and "« plan_01.png »" in texte


def test_reprise_des_reglages_de_whiteboard(tmp_path, monkeypatch):
    from filmstudio.config import Reglages
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "whiteboard-studio").mkdir()
    (tmp_path / "whiteboard-studio" / "config.json").write_text(json.dumps({
        "tts_python": "D:/ai/qwen3-tts/qwen_env/Scripts/python.exe", "tts_voix_reference": "D:/voix/denis.wav",
        "ollama_modele": "qwen3.8:27b", "claude_modele": "sonnet", "style_illustrations": "x", "tts_lot": "abc"}))
    r = Reglages.charger()
    assert r.tts_python.endswith("python.exe") and r.ollama_modele == "qwen3.8:27b" and r.claude_modele == "sonnet"
    assert r.tts_lot == 4 and r.reprise_whiteboard
    r.enregistrer()
    assert not getattr(Reglages.charger(), "reprise_whiteboard", False)


CODEX_CMD = r'''@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@openai\codex\bin\codex.js" %*
'''


def test_codex_installe_par_npm_sous_windows(tmp_path, monkeypatch):
    """Erreur WinError 193 : « codex » sans extension (script Linux) ne doit jamais être lancé."""
    npm = tmp_path / "npm"
    (npm / "node_modules" / "@openai" / "codex" / "bin").mkdir(parents=True)
    (npm / "node_modules" / "@openai" / "codex" / "bin" / "codex.js").write_text("")
    (npm / "codex").write_text("#!/bin/sh\n")
    (npm / "codex.ps1").write_text("")
    (npm / "codex.cmd").write_text(CODEX_CMD.replace("\n", "\r\n"))
    (npm / "node.exe").write_text("")
    # Le chemin trouvé (par le PATH ou indiqué à la main) sans extension est remplacé par le .cmd
    monkeypatch.setattr(forfait.shutil, "which", lambda n: str(npm / "codex") if n == "codex" else None)
    assert forfait.trouver("codex", windows=True) == str(npm / "codex.cmd")
    assert forfait.trouver("codex", str(npm / "codex"), windows=True) == str(npm / "codex.cmd")
    # Et le .cmd est contourné : node + le script JavaScript
    assert forfait.lanceur(str(npm / "codex.cmd")) == [str(npm / "node.exe"),
                                                        str(npm / "node_modules/@openai/codex/bin/codex.js")]
    # Un vrai .exe est lancé tel quel
    assert forfait.lanceur(str(tmp_path / "claude.exe")) == [str(tmp_path / "claude.exe")]
    # Le .exe est préféré par la recherche dans le PATH
    monkeypatch.setattr(forfait.shutil, "which", lambda n: {"codex.cmd": str(npm / "codex.cmd"),
                                                            "codex": str(npm / "codex")}.get(n))
    assert forfait.trouver("codex", windows=True) == str(npm / "codex.cmd")


def test_styles_predefinis():
    from filmstudio import styles
    assert len(styles.STYLES) >= 30
    noms = [n for _, n, _ in styles.STYLES]
    assert len(set(noms)) == len(noms)
    film = Film()
    assert styles.libelle_de(film.style) == "Animation · Film d'animation 3D"   # le style par défaut est dans la liste
    for lib in styles.libelles():
        assert styles.libelle_de(styles.description(lib)) == lib
    interdits = ("pixar", "disney", "ghibli", "dreamworks", "lego", "marvel", "miyazaki", "burton", "anderson")
    assert not any(m in d.lower() for _, _, d in styles.STYLES for m in interdits)
    assert styles.libelle_de("mon style à moi") == styles.PERSONNALISE
