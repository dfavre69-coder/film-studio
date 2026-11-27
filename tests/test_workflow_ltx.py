"""Le vrai modèle officiel « LTX-2.5 : Image to Video » (dépôt Comfy-Org/workflow_templates)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from filmstudio import comfy, comfy_local, video
from filmstudio.config import Reglages
from filmstudio.demo import film_exemple
from filmstudio.production import Production

DONNEES = Path(__file__).parent / "donnees"
UI = json.loads((DONNEES / "video_ltx2_5_i2v.json").read_text(encoding="utf-8"))
OBJECT_INFO = json.loads((DONNEES / "object_info_ltx25.json").read_text(encoding="utf-8"))
API = json.loads((DONNEES / "ltx25_api.json").read_text(encoding="utf-8"))   # converti par comfy-cli


def test_modeles_declares_par_le_template():
    noms = {m.nom: m.sous_dossier for m in comfy_local.modeles_du_template(UI)}
    assert noms["ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"] == "diffusion_models"
    assert noms["gemma4_e2b_it_int8_convrot.safetensors"] == "text_encoders"
    # La liste de secours de Film Studio contient bien les mêmes fichiers
    assert set(noms) == {m.nom for m in comfy_local.MODELES_LTX}


def test_reperage_sur_le_workflow_converti():
    carte = comfy.deviner_carte(API)
    titres = {r: [API[c[0]]["_meta"]["title"] for c in v] for r, v in carte.items()}
    assert titres["prompt"] == ["Prompt"] and titres["secondes"] == ["Duration"]
    assert titres["largeur"] == ["Width"] and titres["hauteur"] == ["Height"] and titres["fps"] == ["Frame Rate"]
    assert titres["image"] == ["Load First Frame"] and len(carte["graine"]) == 2
    assert API[carte["negatif"][0][0]]["inputs"]["text"].startswith("pc game")
    assert "images" not in carte   # la durée en secondes pilote déjà le nombre d'images
    # Largeur et hauteur étaient reliées au sélecteur de format : on impose quand même les nôtres
    g = comfy.appliquer(API, carte, {"largeur": 704, "hauteur": 1280, "secondes": 6.4})
    assert g["398:372"]["inputs"]["value"] == 704 and g["398:360"]["inputs"]["value"] == 1280
    assert g["398:362"]["inputs"]["value"] == 6
    assert comfy.multiple_conseille(API) == 64 and comfy.arrondir(720, 64) == 704


def test_conversion_par_comfy_cli():
    pytest.importorskip("comfy_cli.workflow_to_api")
    api = comfy_local.convertir_en_api(UI, OBJECT_INFO)
    assert api["398:376"]["class_type"] == "PrimitiveStringMultiline" and "395" in api
    assert comfy.deviner_carte(api) == comfy.deviner_carte(API)


def test_installation_complete_du_workflow_puis_rendu(faux, tmp_path, monkeypatch):
    pytest.importorskip("comfy_cli.workflow_to_api")
    faux.comfy.object_info = OBJECT_INFO
    monkeypatch.setattr(comfy_local, "telecharger_template", lambda nom=comfy_local.TEMPLATE_LTX: UI)
    r = faux.reglages.copie(comfy_dossier=str(tmp_path / "comfyui"), moteur_images="manuel")
    etats = []
    wf, remarques = comfy_local.installer_workflow_ltx(r, etats.append, modeles=False)
    assert wf.nom == comfy_local.NOM_WORKFLOW_LTX and wf.multiple == 64 and not wf.manquants()
    relu = Reglages.charger()
    assert relu.comfy_workflow_video == wf.nom
    # Le workflow installé sert à rendre un vrai plan (format portrait pour vérifier les tailles)
    film = film_exemple()
    film.regler_format("9:16")
    film.enregistrer(tmp_path / "film")
    img = film.sous_dossier("images") / "plan_01.png"
    Image.new("RGB", (720, 1280), (90, 40, 10)).save(img)
    film.plans[0].image = film.relatif(img)
    b = Production(film, relu).videos([0])
    assert not b.problemes, b.texte()
    envoye = faux.comfy.recus[-1]
    assert envoye["398:372"]["inputs"]["value"] == 704 and envoye["398:360"]["inputs"]["value"] == 1280
    assert envoye["398:376"]["inputs"]["value"].startswith("Lumi looks left and right")
    assert envoye["395"]["inputs"]["image"].startswith("filmstudio_")
    # Le montage remet le format exact du film (recadrage, pas de bandes noires)
    chemin, _ = Production(film, relu).exporter()
    assert video.infos(chemin)["taille"] == (720, 1280)


def test_modeles_absents_signales():
    oi = json.loads(json.dumps(OBJECT_INFO))
    oi["UNETLoader"]["input"]["required"]["unet_name"][0] = ["autre.safetensors"]
    assert "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors" in comfy_local.modeles_absents(API, oi)


def test_conversion_avec_le_python_de_comfyui(monkeypatch):
    """Sur le PC, comfy-cli est installé dans le Python de ComfyUI, pas dans celui de Film Studio."""
    import sys
    pytest.importorskip("comfy_cli.workflow_to_api")
    monkeypatch.setitem(sys.modules, "comfy_cli.workflow_to_api", None)   # import impossible ici
    api = comfy_local.convertir_en_api(UI, OBJECT_INFO, sys.executable)
    assert comfy.deviner_carte(api) == comfy.deviner_carte(API)
    with pytest.raises(comfy_local.ErreurComfyLocal, match="comfy-cli"):
        comfy_local.convertir_en_api(UI, OBJECT_INFO, None)
