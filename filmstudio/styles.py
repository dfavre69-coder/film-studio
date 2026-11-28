"""Styles visuels prédéfinis : un nom en français, la description en anglais (répétée dans tous les prompts).

Les descriptions restent génériques (techniques, époques, matières) : aucun nom de studio, d'artiste ou de
marque, pour des images originales et sans problème de droits.
"""
from __future__ import annotations

STYLES: list[tuple[str, str, str]] = [
    # (catégorie, nom affiché, description pour les modèles)
    ("Animation", "Film d'animation 3D",
     "3D animated feature film look, soft cinematic lighting, expressive characters, rich colors, shallow depth of field"),
    ("Animation", "Pâte à modeler (stop motion)",
     "claymation stop-motion style, handmade plasticine characters with visible fingerprints, miniature handcrafted sets, "
     "warm practical lighting"),
    ("Animation", "Dessin animé 2D classique",
     "classic hand-drawn 2D cel animation, clean ink outlines, flat vibrant colors, painted backgrounds"),
    ("Animation", "Anime japonais",
     "Japanese anime style, detailed painted backgrounds, expressive eyes, crisp line art, dramatic lighting and sky"),
    ("Animation", "Papier découpé",
     "paper cutout animation, layered colored paper with visible texture and soft drop shadows, handmade collage look"),
    ("Animation", "Origami",
     "origami world, everything folded from crisp colored paper, sharp creases, soft studio lighting, miniature scale"),
    ("Animation", "Feutrine et laine",
     "needle-felted wool and felt characters, cozy knitted textures, soft fuzzy materials, miniature handmade sets"),
    ("Animation", "Jouets en bois",
     "wooden toy world, painted wooden figures with simple shapes, visible wood grain, bright primary colors, tabletop scale"),
    ("Animation", "Ombres chinoises",
     "shadow puppet theater, black paper silhouettes against a glowing warm backlit screen, intricate cutout details"),
    ("Illustration", "Aquarelle",
     "soft watercolor illustration, delicate washes and color bleeds, visible paper texture, gentle pastel light"),
    ("Illustration", "Peinture à l'huile",
     "classical oil painting style, rich impasto brushstrokes, dramatic chiaroscuro lighting, deep warm colors"),
    ("Illustration", "Livre pour enfants",
     "children's picture book illustration, gouache and colored pencil textures, friendly rounded shapes, warm cozy palette"),
    ("Illustration", "Croquis au crayon",
     "pencil sketch style, graphite lines and cross-hatching on off-white sketchbook paper, subtle smudges"),
    ("Illustration", "Craie sur tableau noir",
     "chalk drawing on a dark green chalkboard, dusty chalk texture, hand-drawn lines, soft colored chalk accents"),
    ("Illustration", "Encre et estampe japonaise",
     "Japanese woodblock print style, bold ink outlines, flat color areas, textured rice paper, stylized waves and clouds"),
    ("Illustration", "Pastel doux et cosy",
     "soft pastel illustration, dreamy muted colors, gentle glow, cozy and calm atmosphere, rounded shapes"),
    ("Bande dessinée", "BD ligne claire",
     "European clear-line comic style, uniform black outlines, flat bright colors, clean detailed backgrounds"),
    ("Bande dessinée", "Comics américains",
     "American comic book style, bold ink lines, halftone dot shading, dynamic angles, saturated primary colors"),
    ("Bande dessinée", "Manga noir et blanc",
     "black and white manga style, expressive ink linework, screentone shading, speed lines, high contrast"),
    ("Bande dessinée", "Pop art",
     "pop art style, bold flat colors, thick black outlines, Ben-Day dots, high contrast graphic look"),
    ("Jeu vidéo", "Pixel art rétro",
     "retro 16-bit pixel art, limited color palette, crisp pixels, side-scrolling game look, parallax backgrounds"),
    ("Jeu vidéo", "Low poly",
     "low poly 3D style, faceted geometric shapes, flat shading, clean pastel colors, minimalist scenery"),
    ("Jeu vidéo", "Cel-shading",
     "cel-shaded 3D game style, bold outlines, flat toon shading, vivid colors, stylized characters"),
    ("Jeu vidéo", "Diorama miniature",
     "tilt-shift miniature diorama, tiny handcrafted world seen from above, shallow depth of field, toy-like scale"),
    ("Cinéma", "Cinéma réaliste",
     "photorealistic cinematic film still, natural lighting, 35mm lens, subtle film grain, realistic textures"),
    ("Cinéma", "Film noir",
     "black and white film noir, hard shadows, venetian blind light, rain-soaked streets, high contrast, 1940s mood"),
    ("Cinéma", "Film muet des années 1920",
     "1920s silent film look, sepia and black and white, heavy film grain, vignette, slightly flickering old footage"),
    ("Cinéma", "Western sépia",
     "western film look, sepia and dusty orange tones, wide desert landscapes, low golden sun, film grain"),
    ("Univers", "Cyberpunk néon",
     "cyberpunk neon city, rain reflections, magenta and cyan neon lights, dense futuristic streets, cinematic night"),
    ("Univers", "Science-fiction",
     "sleek science fiction look, clean futuristic architecture, glowing interfaces, cool blue lighting, epic scale"),
    ("Univers", "Steampunk",
     "steampunk world, brass gears, copper pipes, Victorian machinery, steam and warm lamplight, rich sepia palette"),
    ("Univers", "Fantasy épique",
     "epic fantasy art, majestic landscapes, magical glowing light, detailed armor and cloaks, dramatic skies"),
    ("Univers", "Conte gothique",
     "dark gothic fairy tale, twisted trees, moonlit fog, muted purples and greys, whimsical yet eerie mood"),
    ("Univers", "Art déco",
     "art deco style, geometric patterns, gold and black palette, elegant 1920s architecture, glamorous lighting"),
    ("Univers", "Vitrail",
     "stained glass style, bold lead lines, glowing jewel-toned glass, light shining through colored panes"),
]

PERSONNALISE = "— Style personnalisé —"


def libelles() -> list[str]:
    return [f"{cat} · {nom}" for cat, nom, _ in STYLES]


def description(libelle: str) -> str | None:
    for (cat, nom, desc), lib in zip(STYLES, libelles()):
        if libelle in (lib, nom):
            return desc
    return None


def libelle_de(description_style: str) -> str:
    """Le style prédéfini qui correspond à ce texte, sinon « personnalisé »."""
    texte = (description_style or "").strip()
    for (cat, nom, desc), lib in zip(STYLES, libelles()):
        if texte == desc:
            return lib
    return PERSONNALISE
