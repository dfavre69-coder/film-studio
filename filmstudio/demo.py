"""Un film d'exemple tout prêt (la luciole qui a peur du noir), pour essayer sans passer par une IA."""
from __future__ import annotations

from pathlib import Path

from .projet import Film, Lieu, Personnage, Plan, Replique

STYLE = ("3D animated feature film look, soft cinematic lighting, expressive characters, "
         "rich colors, shallow depth of field")


def film_exemple() -> Film:
    lumi = Personnage(
        id="lumi", nom="Lumi", description="une jeune luciole timide qui a peur du noir",
        apparence=("a tiny young firefly with big round amber eyes, soft yellow glowing abdomen, "
                   "translucent wings, short antennae curled at the tips"),
        voix="petite voix de fillette, douce et hésitante")
    grand_pere = Personnage(
        id="papi_lueur", nom="Papi Lueur", description="la vieille luciole sage qui guide les autres",
        apparence=("an old firefly with a white bushy moustache, tiny round glasses, warm orange glow, "
                   "slightly worn wings"),
        voix="voix grave et chaleureuse de grand-père")
    foret = Lieu(id="foret_nuit", nom="La forêt la nuit", description="une forêt profonde et inquiétante",
                 apparence=("a dense old forest at night, giant ferns and mossy roots, deep blue moonlight, "
                            "light mist between the trunks"))
    clairiere = Lieu(id="clairiere", nom="La clairière des lucioles", description="une clairière pleine de lumières",
                     apparence=("a small forest clearing at night filled with hundreds of glowing fireflies, "
                                "tall grass, a calm pond reflecting the lights"))
    plans = [
        Plan(titre="Perdue", action="Lumi se retrouve seule sous une fougère géante.", lieu="foret_nuit",
             personnages=["lumi"], duree=6,
             image_prompt=("wide shot, Lumi alone sitting on a mossy root under a giant fern, small and scared, "
                           "her glow faint, mist around"),
             mouvement="Lumi looks left and right nervously, her glow flickers; slow push-in toward her",
             camera="plan large, travelling avant lent",
             repliques=[Replique("Lumi", "Il y a quelqu'un ? J'ai peur du noir…")]),
        Plan(titre="Le courage", action="Lumi prend son élan et s'envole entre les troncs.", lieu="foret_nuit",
             personnages=["lumi"], duree=6, enchainer=True,
             image_prompt="Lumi taking off from the mossy root",
             mouvement=("Lumi takes a deep breath, her glow grows brighter, she flies off between the tree trunks; "
                        "the camera follows her"),
             camera="plan moyen, caméra qui suit",
             repliques=[Replique("Narrateur", "Alors, tout doucement, sa lumière grandit.")]),
        Plan(titre="Les autres", action="Lumi arrive dans la clairière où Papi Lueur l'accueille.",
             lieu="clairiere", personnages=["lumi", "papi_lueur"], duree=7,
             image_prompt=("Lumi arriving in the glowing clearing, Papi Lueur smiling and opening his arms to her, "
                           "hundreds of fireflies in the background"),
             mouvement="Lumi lands next to Papi Lueur, they hug, the fireflies around them glow brighter; slow crane up",
             camera="plan d'ensemble, grue qui s'élève",
             repliques=[Replique("Papi Lueur", "Tu vois, Lumi ? Dans le noir, c'est toi qui brilles.")]),
    ]
    return Film(titre="La luciole qui avait peur du noir",
                pitch=("Une toute petite luciole qui a peur du noir se perd dans la forêt la nuit "
                       "et retrouve son chemin jusqu'aux autres."),
                style=STYLE, personnages=[lumi, grand_pere], lieux=[foret, clairiere], plans=plans)


def creer_exemple(dossier: str | Path) -> Film:
    film = film_exemple()
    film.enregistrer(dossier)
    return film
