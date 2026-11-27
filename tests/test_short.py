from __future__ import annotations

from pathlib import Path

from PIL import Image

from filmstudio import comfy, ia, sous_titres, video
from filmstudio.demo import film_exemple
from filmstudio.production import Production, fiche_publication
from tests.faux_comfy import WORKFLOW_VIDEO

REPONSE = """{"titre": "La luciole", "personnages": [{"nom": "Lumi", "apparence": "a tiny firefly"}],
 "lieux": [{"nom": "Forêt", "apparence": "night forest"}],
 "plans": [{"titre": "A", "lieu": "Forêt", "personnages": ["Lumi"], "image": "x", "mouvement": "y", "duree": 40,
            "repliques": [{"personnage": "Lumi", "texte": "Il y a quelqu'un ?"}]},
           {"titre": "B", "lieu": "Forêt", "personnages": ["Lumi"], "image": "x", "mouvement": "y", "duree": 30}],
 "publication": {"titre": "Elle avait peur du noir…", "description": "Une luciole se perd. Et vous ?",
                 "hashtags": "#animation, conte #Shorts"}}"""


def test_prompt_short():
    film = film_exemple()
    film.regler_short()
    assert film.format == "9:16" and film.largeur == 720 and film.sous_titres_incrustes
    p = ia.construire_prompt(film, "idée", 10, 4)
    assert "C'est un Short YouTube" in p and "40 secondes au plus" in p and "boucle" in p
    assert "{{SHORT}}" not in p
    film.short = False
    assert "C'est un Short" not in ia.construire_prompt(film, "idée", 10, 4)


def test_publication_et_short_trop_long():
    base = film_exemple()
    base.regler_short()
    film, remarques = ia.film_depuis_ia(REPONSE, base, duree_max=40)
    assert film.publication == {"titre": "Elle avait peur du noir…", "description": "Une luciole se perd. Et vous ?",
                                "hashtags": ["animation", "conte", "Shorts"]}
    assert any("Short trop long" in r for r in remarques)
    texte = fiche_publication(film)
    assert texte.startswith("TITRE\nElle avait peur du noir…") and "#animation #conte #Shorts" in texte
    # Sans publication écrite par l'IA : une fiche quand même, avec #Shorts
    assert "#Shorts" in fiche_publication(base)


def test_minutage_mot_a_mot():
    mots = sous_titres.minuter("Il y a quelqu'un ? Je suis là.", 1.0, 3.0)
    assert [m.texte for m in mots][0] == "Il" and abs(mots[0].debut - 1.0) < 1e-9 and abs(mots[-1].fin - 3.0) < 1e-6
    assert all(a.fin <= b.debut + 1e-9 for a, b in zip(mots, mots[1:]))
    groupes = sous_titres.grouper(mots)
    assert [len(g) for g in groupes] == [3, 2, 3]          # coupé après « ? »
    evts = sous_titres.evenements([(1.0, 3.0, "Il y a quelqu'un ? Je suis là.")])
    assert len(evts) == 8 and evts[0][2] == ["Il", "y", "a"] and evts[0][3] == 0


def _jaune(img: Image.Image, y0: int, y1: int) -> int:
    o = img.convert("RGB").crop((0, y0, img.width, y1)).tobytes()
    return sum(1 for i in range(0, len(o), 3) if o[i] > 220 and o[i + 1] > 180 and o[i + 2] < 80)


def test_short_complet_avec_sous_titres(faux, tmp_path):
    comfy.enregistrer_workflow(comfy.Workflow("ltx", "video", WORKFLOW_VIDEO, comfy.deviner_carte(WORKFLOW_VIDEO)))
    r = faux.reglages.copie(comfy_workflow_video="ltx", moteur_images="manuel")
    film = film_exemple()
    film.regler_short()
    film.publication = {"titre": "Elle avait peur du noir…", "description": "…", "hashtags": ["conte"]}
    film.enregistrer(tmp_path / "short")
    for i in (0, 2):
        img = film.sous_dossier("images") / f"plan_{i + 1:02d}.png"
        Image.new("RGB", (720, 1280), (20, 30, 70)).save(img)
        film.plans[i].image = film.relatif(img)
    prod = Production(film, r)
    prod.voix()
    assert not prod.videos().problemes
    chemin, b = prod.exporter()
    assert video.infos(chemin)["taille"] == (720, 1280) and video.infos(chemin)["audio"]
    fiche = chemin.with_name(chemin.stem + "_publication.txt").read_text(encoding="utf-8")
    assert "Elle avait peur du noir" in fiche and "#Shorts" in fiche
    # Une image prise pendant la première réplique montre des mots en jaune dans la zone sûre
    debut, fin, _ = film.plans[0].sous_titres[0]
    vue = tmp_path / "vue.png"
    video.ffmpeg("-ss", f"{debut + 0.05:.2f}", "-i", str(chemin), "-frames:v", "1", str(vue))
    with Image.open(vue) as img:
        zone = (int(1280 * 0.62) - 150, int(1280 * 0.62) + 150)
        assert _jaune(img, *zone) > 200
        assert _jaune(img, 0, 400) == 0                       # rien en haut de l'image
