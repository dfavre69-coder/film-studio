"""Réglages de l'utilisateur, communs à tous les films (ComfyUI, Claude, ChatGPT, Ollama, Qwen3-TTS)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def dossier_config() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "FilmStudio"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "film-studio"


def fichier_config() -> Path:
    return dossier_config() / "config.json"


def dossier_workflows() -> Path:
    return dossier_config() / "workflows"


MODELES_TTS = [
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
]
LANGUES_TTS = ["French", "Auto", "English", "German", "Spanish", "Italian", "Portuguese",
               "Chinese", "Japanese", "Korean", "Russian"]

MOTEURS_SCENARIO = {"claude": "Claude (abonnement)", "ollama": "Ollama (local)", "manuel": "Copier-coller"}
MOTEURS_IMAGES = {"chatgpt": "ChatGPT (abonnement, via Codex)", "comfyui": "ComfyUI (local)",
                  "manuel": "Mes propres images"}


@dataclass
class Reglages:
    # --- Choix des moteurs ----------------------------------------------------
    moteur_scenario: str = "claude"        # claude | ollama | manuel
    moteur_images: str = "chatgpt"         # chatgpt | comfyui | manuel
    # --- ComfyUI (rendu des vidéos sur la carte graphique) --------------------
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_workflow_video: str = ""         # nom du workflow importé (dossier workflows)
    comfy_workflow_image: str = ""         # facultatif : workflow texte → image, pour se passer de ChatGPT
    comfy_delai_minutes: int = 40          # abandon d'un rendu au-delà
    comfy_liberer_vram: bool = True        # vide la mémoire de ComfyUI avant les voix ou Ollama
    comfy_dossier: str = ""                # ComfyUI à démarrer soi-même (vide = dossier par défaut)
    comfy_demarrage_auto: bool = True      # démarre ComfyUI quand un rendu en a besoin
    comfy_options: str = ""                # options de lancement, ex. « --reserve-vram 4 »
    hf_jeton: str = ""                     # jeton Hugging Face (modèles protégés comme LTX-2.5)
    duree_max_plan: float = 12.0           # secondes ; au-delà, le plan est raccourci (mémoire vidéo)
    # --- Claude et ChatGPT avec l'abonnement (sans clé API) ----------------------
    claude_commande: str = ""              # chemin de claude.exe (vide = recherche automatique)
    claude_modele: str = ""                # « sonnet », « opus »… (vide = modèle par défaut de Claude Code)
    codex_commande: str = ""               # chemin de codex (vide = recherche automatique)
    codex_options: str = ""                # options de « codex exec » (vide = forfait.OPTIONS_CODEX_DEFAUT)
    # --- Ollama ---------------------------------------------------------------
    ollama_url: str = "http://localhost:11434"
    ollama_modele: str = ""
    ollama_contexte: int = 32768
    ollama_temperature: float = 0.7
    ollama_sans_reflexion: bool = True
    ollama_liberer_vram: bool = True
    # --- Qwen3-TTS (voix du narrateur ; chaque personnage peut avoir la sienne) --
    tts_python: str = ""                   # python.exe de l'environnement où « qwen-tts » est installé
    tts_modele: str = MODELES_TTS[0]
    tts_appareil: str = "cuda:0"
    tts_precision: str = "bfloat16"
    tts_langue: str = "French"
    tts_voix_reference: str = ""           # extrait audio de la voix à cloner (modèles « Base »)
    tts_texte_reference: str = ""          # ce qui est dit dans l'extrait (vide = mode « timbre seul »)
    tts_locuteur: str = ""                 # voix prédéfinie (modèles « CustomVoice »)
    tts_consigne: str = ""                 # consigne de style (modèles « CustomVoice »)
    tts_temperature: float = 0.9
    tts_lot: int = 4
    tts_garder_minutes: int = 5
    # --- Interface --------------------------------------------------------------
    dernier_film: str = ""
    nb_plans: int = 6
    duree_plan: float = 6.0

    @classmethod
    def charger(cls, chemin: Path | None = None) -> "Reglages":
        par_defaut = chemin is None
        chemin = chemin or fichier_config()
        try:
            d = json.loads(Path(chemin).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if par_defaut and not Path(chemin).exists():
                return cls.depuis_whiteboard()
            return cls()
        noms = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in noms})

    @classmethod
    def depuis_whiteboard(cls) -> "Reglages":
        """Premier lancement : reprend les réglages communs de Whiteboard Studio s'il est installé
        (Qwen3-TTS, voix clonée, Ollama, Claude Code, Codex)."""
        r = cls()
        if sys.platform == "win32":
            wb = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "WhiteboardStudio"
        else:
            wb = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "whiteboard-studio"
        try:
            d = json.loads((wb / "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return r
        communs = [f.name for f in fields(cls) if f.name.startswith(("tts_", "ollama_", "claude_", "codex_"))]
        for cle in communs:
            if cle in d and isinstance(d[cle], type(getattr(r, cle))):
                setattr(r, cle, d[cle])
        r.reprise_whiteboard = True
        return r

    def enregistrer(self, chemin: Path | None = None) -> None:
        chemin = Path(chemin or fichier_config())
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    def copie(self, **changements) -> "Reglages":
        return Reglages(**{**asdict(self), **changements})
