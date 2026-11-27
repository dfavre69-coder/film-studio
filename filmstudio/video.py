"""Tout ce qui passe par ffmpeg : durées, dernière image d'un clip, bande-son des plans, montage final."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

_CREATION = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


class ErreurVideo(RuntimeError):
    pass


def chemin_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        trouve = shutil.which("ffmpeg")
        if trouve:
            return trouve
        raise ErreurVideo("ffmpeg introuvable : relancez install.bat (paquet imageio-ffmpeg).")


def ffmpeg(*args: str, delai: float = 1800) -> str:
    cmd = [chemin_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=delai, **_CREATION)
    if res.returncode != 0:
        derniere = "\n".join(res.stderr.strip().splitlines()[-6:])
        raise ErreurVideo(f"ffmpeg a échoué : {derniere or res.returncode}")
    return res.stderr


def infos(chemin: str | Path) -> dict:
    """Durée, présence d'une piste audio et d'une piste vidéo (lues par ffmpeg)."""
    res = subprocess.run([chemin_ffmpeg(), "-hide_banner", "-i", str(chemin)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", **_CREATION)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if not m:
        raise ErreurVideo(f"Impossible de lire « {Path(chemin).name} ».")
    h, mn, s = m.groups()
    taille = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", res.stderr)
    return {"duree": int(h) * 3600 + int(mn) * 60 + float(s),
            "audio": bool(re.search(r"Stream #.*Audio:", res.stderr)),
            "video": bool(re.search(r"Stream #.*Video:", res.stderr)),
            "taille": (int(taille.group(1)), int(taille.group(2))) if taille else None}


def duree(chemin: str | Path) -> float:
    return infos(chemin)["duree"]


def derniere_image(clip: str | Path, destination: str | Path) -> Path:
    """Extrait la dernière image d'un clip (pour enchaîner le plan suivant)."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    ffmpeg("-sseof", "-0.5", "-i", str(clip), "-update", "1", "-q:v", "2", str(destination))
    if not destination.exists():  # clip très court
        ffmpeg("-i", str(clip), "-vf", "reverse", "-frames:v", "1", str(destination))
    return destination


def ajuster_image(source: str | Path, destination: str | Path, largeur: int, hauteur: int) -> Path:
    """Recadre (au centre) et redimensionne une image au format du film."""
    destination = Path(destination)
    with Image.open(source) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img = ImageOps.fit(img, (int(largeur), int(hauteur)), Image.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        img.save(destination)
    return destination


# ---------------------------------------------------------------------------
# Bande-son des plans
# ---------------------------------------------------------------------------
@dataclass
class Segment:
    fichier: Path
    texte: str


def assembler_voix(segments: list[Segment], destination: str | Path, avant: float = 0.3, entre: float = 0.35,
                   apres: float = 0.4) -> tuple[float, list[list]]:
    """Met les répliques bout à bout (avec de courts silences). Renvoie (durée, sous-titres du plan)."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    entrees, filtres, sous_titres = [], [], []
    t = avant
    for i, s in enumerate(segments):
        d = duree(s.fichier)
        entrees += ["-i", str(s.fichier)]
        ms = int(round(t * 1000))
        filtres.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms}[a{i}]")
        sous_titres.append([round(t, 2), round(t + d, 2), s.texte])
        t += d + entre
    total = t - entre + apres
    melange = "".join(f"[a{i}]" for i in range(len(segments)))
    filtres.append(f"{melange}amix=inputs={len(segments)}:duration=longest:normalize=0,"
                   f"apad,atrim=0:{total:.3f}[out]")
    ffmpeg(*entrees, "-filter_complex", ";".join(filtres), "-map", "[out]", "-c:a", "pcm_s16le", str(destination))
    return round(total, 2), sous_titres


# ---------------------------------------------------------------------------
# Montage
# ---------------------------------------------------------------------------
def habiller_clip(clip: str | Path, destination: str | Path, largeur: int, hauteur: int, fps: int,
                  voix: str | Path | None = None, son_modele: str = "ambiance", volume_modele: float = 0.35,
                  duree_min: float = 0.0) -> float:
    """Met un clip au format du film et lui donne sa bande-son (voix + son du modèle).

    Si les répliques durent plus longtemps que le clip, la dernière image est prolongée.
    Renvoie la durée du clip obtenu."""
    i = infos(clip)
    d_clip = i["duree"]
    d_voix = duree(voix) if voix else 0.0
    d = max(d_clip, d_voix, duree_min)
    prolonge = max(0.0, d - d_clip)
    v = (f"[0:v]scale={largeur}:{hauteur}:force_original_aspect_ratio=increase,"
         f"crop={largeur}:{hauteur},setsar=1,fps={fps}")
    if prolonge > 0.01:
        v += f",tpad=stop_mode=clone:stop_duration={prolonge:.3f}"
    v += f",trim=0:{d:.3f},setpts=PTS-STARTPTS,format=yuv420p[v]"
    filtres = [v, f"anullsrc=r=48000:cl=stereo,atrim=0:{d:.3f}[sil]"]
    pistes = ["[sil]"]
    entrees = ["-i", str(clip)]
    if i["audio"] and son_modele != "aucun":
        vol = 1.0 if (son_modele == "complet" or not voix) else volume_modele
        filtres.append(f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={vol:.3f},"
                       f"apad,atrim=0:{d:.3f}[m]")
        pistes.append("[m]")
    if voix:
        entrees += ["-i", str(voix)]
        filtres.append(f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=0:{d:.3f}[vx]")
        pistes.append("[vx]")
    filtres.append(f"{''.join(pistes)}amix=inputs={len(pistes)}:duration=first:normalize=0[a]")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(*entrees, "-filter_complex", ";".join(filtres), "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(fps),
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{d:.3f}", str(destination))
    return d


def assembler(clips: list[Path], destination: str | Path, musique: str | Path | None = None,
              volume_musique: float = 0.15, normaliser: bool = True) -> Path:
    """Colle les clips (déjà au même format) et ajoute la musique de fond et la normalisation du son."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="filmstudio_montage_") as tmp:
        liste = Path(tmp) / "liste.txt"
        liste.write_text("".join(f"file '{Path(c).resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
        colle = Path(tmp) / "colle.mp4"
        ffmpeg("-f", "concat", "-safe", "0", "-i", str(liste), "-c", "copy", str(colle))
        if not musique and not normaliser:
            shutil.copyfile(colle, destination)
            return destination
        entrees = ["-i", str(colle)]
        if musique:
            total = duree(colle)
            entrees += ["-stream_loop", "-1", "-i", str(musique)]
            fondu = max(0.0, total - 2.0)
            filtre = (f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={volume_musique:.3f},"
                      f"atrim=0:{total:.3f},afade=t=out:st={fondu:.3f}:d=2[mu];"
                      f"[0:a][mu]amix=inputs=2:duration=first:normalize=0")
        else:
            filtre = "[0:a]anull"
        if normaliser:
            filtre += ",loudnorm=I=-16:TP=-1.5:LRA=11"
        filtre += ",aresample=48000[a]"
        ffmpeg(*entrees, "-filter_complex", filtre, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination))
    return destination


def _horodatage(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ecrire_srt(entrees: list[tuple[float, float, str]], destination: str | Path) -> Path:
    destination = Path(destination)
    lignes = []
    for n, (debut, fin, texte) in enumerate(entrees, 1):
        lignes.append(f"{n}\n{_horodatage(debut)} --> {_horodatage(fin)}\n{texte}\n")
    destination.write_text("\n".join(lignes), encoding="utf-8")
    return destination
