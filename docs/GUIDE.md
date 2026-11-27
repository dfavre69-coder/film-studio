# Guide de Film Studio

## Sommaire

1. [Préparer ComfyUI](#1-préparer-comfyui)
2. [Les réglages](#2-les-réglages)
3. [Écrire l'histoire](#3-écrire-lhistoire)
4. [Personnages et lieux](#4-personnages-et-lieux)
5. [Les plans](#5-les-plans)
6. [Produire et monter](#6-produire-et-monter)
7. [Conseils pour de bons résultats](#7-conseils-pour-de-bons-résultats)
8. [Faire un Short](#8-faire-un-short)
8. [Licences des modèles](#licences-des-modèles)
9. [En cas de problème](#en-cas-de-problème)

---

## 1. Préparer ComfyUI

### Installation automatique

`install.bat` propose d'installer ComfyUI tout seul. Il passe par **comfy-cli**, l'outil officiel, et installe ComfyUI avec PyTorch pour carte NVIDIA dans `D:\ai\ComfyUI` (ou `%USERPROFILE%\ComfyUI` s'il n'y a pas de disque D:). Comptez environ 10 Go et 10 à 20 minutes. Il propose ensuite de télécharger les **modèles LTX-2.5**, qui pèsent plusieurs dizaines de Go. Si la connexion coupe, le téléchargement reprend là où il s'était arrêté.

**Les modèles LTX-2.5 sont protégés sur Hugging Face.** Avant de les télécharger :

1. Connectez-vous sur [huggingface.co](https://huggingface.co), ou créez un compte gratuit.
2. Ouvrez [la page du modèle](https://huggingface.co/Lightricks/LTX-2.5) et acceptez ses conditions d'utilisation.
3. Créez un jeton de type « Read » sur [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).

`install.bat` vous demande alors ce jeton et l'enregistre. Vous pouvez aussi le saisir dans **Réglages → ComfyUI → Jeton Hugging Face**. Si vous vous êtes déjà connecté avec `huggingface-cli login`, Film Studio réutilise ce jeton tout seul.

Il prépare enfin le **workflow vidéo**. Il récupère le modèle officiel « LTX-2.5 : Image to Video » de la bibliothèque de ComfyUI et le convertit au format API avec le convertisseur officiel de comfy-cli, le même que le bouton « Exporter (API) ». Il repère ensuite les réglages à remplir à chaque plan (prompt, image de départ, durée, taille, cadence, graine) et le choisit pour les vidéos. ComfyUI démarre pour l'occasion pendant une minute, puis s'arrête.

Les mêmes boutons existent dans **Réglages → ComfyUI → ComfyUI sur ce PC** :

- **Installer ComfyUI…**
- **Modèles et workflow LTX-2.5…**
- **Démarrer maintenant**

Si vous lancez une production sans workflow vidéo, Film Studio propose de le préparer tout de suite.

Vous avez déjà un ComfyUI (portable, manuel…) ? Indiquez simplement son dossier.

**Vous n'avez plus à lancer ComfyUI.** Quand un rendu en a besoin, Film Studio le démarre en arrière-plan, sans fenêtre, et l'arrête en quittant. Un ComfyUI que vous avez lancé vous-même n'est jamais arrêté. Son journal se trouve dans `%APPDATA%\FilmStudio\comfyui.log`.

### Utiliser un autre workflow (facultatif)

Le workflow LTX-2.5 est installé automatiquement. Vous pouvez aussi utiliser **un autre workflow testé dans ComfyUI** (Wan, une autre version de LTX…) : Film Studio se contente de remplir, à chaque plan, le prompt, l'image de départ, la durée et la taille.

1. Double-cliquez sur **`demarrer_comfyui.bat`** : ComfyUI s'ouvre dans le navigateur.
2. Ouvrez la **bibliothèque de modèles** (Template Library), rubrique **Video**, et choisissez **LTX-2.5 Image to Video**. Les modèles sont déjà téléchargés.
3. Lancez-le une fois avec une image de votre choix. **Il doit fonctionner dans ComfyUI avant de fonctionner dans Film Studio.**
4. Menu **Workflow → Exporter (API)**. Enregistrez le fichier `.json`.
5. Dans Film Studio, allez dans **Réglages → ComfyUI**, puis **Workflow vidéo → Importer…**, et choisissez ce fichier.
6. Fermez la fenêtre de `demarrer_comfyui.bat`. Désormais, Film Studio s'en charge.

Film Studio repère tout seul les champs à remplir et vous les montre. Vérifiez au moins ces trois-là :

- **Texte du prompt (positif)** : le nœud de texte relié à l'entrée *positive*.
- **Image de départ** : le nœud *Load Image*.
- **Nombre d'images** : la durée du plan. LTX demande un nombre d'images de la forme 8 × n + 1, et Wan de la forme 4 × n + 1. Le « pas » se règle en bas de la fenêtre.

Les champs marqués d'une étoile (graine, cadence, largeur, hauteur) sont aussi remplis dans les autres nœuds qui portent le même nom.

### Mémoire vidéo (Shadow à 20 Go)

- Préférez les versions **distillées** ou **fp8** de LTX-2.5 : elles sont plus rapides et plus légères.
- Gardez **Durée maximale d'un plan** autour de 8 à 12 secondes, et commencez en 1280 × 720.
- Si ComfyUI manque de mémoire, lancez-le avec l'option `--reserve-vram 4`.
- Film Studio vide la mémoire de ComfyUI avant de générer les voix, et décharge Qwen3-TTS avant de rendre les vidéos. Les deux ne se disputent pas la carte.

### Workflow d'images (facultatif)

Si vous ne voulez pas passer par ChatGPT, importez aussi un workflow **texte → image** (Flux, Z-Image…) dans **Workflow d'images**, puis choisissez **Images : ComfyUI (local)** dans l'onglet Production. Tout reste gratuit et hors ligne. En contrepartie, la ressemblance des personnages d'un plan à l'autre est moins bonne, car ce workflow ne reçoit pas les fiches de référence.

## 2. Les réglages

| Onglet | À remplir |
|---|---|
| ComfyUI | adresse (par défaut `http://127.0.0.1:8188`), workflow vidéo, workflow d'images facultatif, dossier de ComfyUI et démarrage automatique |
| Claude et ChatGPT | rien en général : install.bat a tout installé et connecté. Utilisez **Vérifier** en cas de doute. |
| Ollama | le modèle local qui écrit le découpage, si vous n'utilisez pas Claude |
| Voix | le `python.exe` de l'environnement Qwen3-TTS (par exemple `D:\ai\qwen3-tts\qwen_env\Scripts\python.exe`), le modèle, et la voix du narrateur (un extrait de 10 à 20 secondes) |

**Claude et ChatGPT sans clé API.** Film Studio lance Claude Code (`claude -p`) et Codex (`codex exec`), qui sont connectés à vos comptes. Les variables `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` et `CODEX_API_KEY` sont retirées pour ces deux programmes. Même si une clé API traîne sur votre PC, c'est donc bien l'abonnement qui sert. Attention, la génération d'images consomme vite le quota de Codex. Pour un long film, dessinez les images en plusieurs fois.

## 3. Écrire l'histoire

Onglet **1. Histoire** :

- **Votre idée** : quelques phrases suffisent. Indiquez qui, ce qui arrive et la fin.
- **Style visuel** : de préférence en anglais. Il est répété dans tous les prompts. Par exemple : *3D animated feature film look, soft cinematic lighting*.
- **Format** : 16:9 pour YouTube, 9:16 pour un Short.
- **Plans** et **secondes par plan** : 6 plans de 6 secondes donnent un film d'environ 40 secondes.

Cliquez ensuite sur **Écrire le découpage**. Avec **Copier-coller**, le prompt part dans le presse-papiers : collez-le dans l'IA de votre choix, puis cliquez sur **Coller la réponse…**.

**Du texte jusqu'au film en un clic.** Cochez **Puis produire tout le film automatiquement** : dès que le découpage est écrit, Film Studio enchaîne les fiches, les premières images, les voix, les vidéos et le montage, sans attendre. Pour un premier essai, il vaut mieux laisser la case décochée et relire le découpage et les images avant de lancer les vidéos.

Film Studio vérifie la réponse : il ajoute un lieu oublié et rattache au narrateur une réplique sans personnage. Il vous signale tout ce qu'il a corrigé.

## 4. Personnages et lieux

Pour chacun :

- **Apparence** (en anglais) : une phrase dense et précise. Elle est recopiée dans chaque prompt, c'est donc la clé de la ressemblance.
- **Fiche** : **Dessiner cette fiche** demande l'image à ChatGPT. **Importer une image…** prend la vôtre, par exemple un dessin ou une photo de jouet.
- **Voix** (personnages seulement) : un extrait audio à cloner, avec son texte. Laissez vide pour utiliser la voix du narrateur. Avec un modèle *CustomVoice*, indiquez plutôt une voix prédéfinie et une consigne.

## 5. Les plans

Chaque plan comprend :

- **Première image** (anglais) : la composition de départ.
- **Mouvement** (anglais) : ce qui bouge pendant le plan, puis la caméra.
- **Répliques** : une par ligne, sous la forme `Nom : texte`. Une ligne sans nom est lue par le narrateur.
- **Enchaîner** : le plan part de la **dernière image du plan précédent**. C'est idéal pour prolonger une action sans coupure. Le plan précédent doit alors être rendu avant celui-ci.
- **Durée** : allongée automatiquement si les répliques durent plus longtemps.

Les boutons agissent sur **les plans sélectionnés** (Ctrl+clic pour en choisir plusieurs) :

- **Dessiner** et **Importer…** préparent la première image.
- **Générer** et **Écouter** s'occupent des voix.
- **Rendre (nouvelle prise)** ajoute une prise. **Prise retenue** choisit celle qui ira dans le film.

## 6. Produire et monter

Onglet **4. Production** :

- **Images** : ChatGPT, ComfyUI ou vos propres images.
- **Voix** :
  - Qwen3-TTS (voix françaises, recommandé) ;
  - « Le modèle vidéo fait parler », pour les modèles qui génèrent la parole, comme LTX ou MiniMax H3 ;
  - pas de voix.
- **Son du modèle vidéo** : en ambiance sous les voix (volume réglable), complet, ou coupé.
- **Musique de fond** : en boucle, avec un fondu à la fin.

**▶ Tout produire** enchaîne les étapes : fiches, premières images, voix, vidéos, montage. Seul ce qui manque est fait. Cochez **Tout refaire** pour repartir de zéro (nouvelles images et nouvelles prises).

Le film arrive dans `export\`, avec ses sous-titres `.srt`, prêts pour YouTube. Vous pouvez arrêter à tout moment : ce qui est fait est enregistré.

## 7. Conseils pour de bons résultats

- **Une action simple par plan.** Les modèles vidéo ratent les foules, le texte écrit et les mains qui manipulent de petits objets.
- **Peu de personnages par plan** (un ou deux), et des apparences très différentes les unes des autres.
- **Relisez la première image avant de rendre la vidéo.** Une image ratée donne une vidéo ratée, et un rendu coûte bien plus cher qu'une image.
- **Les voix ne sont pas synchronisées sur les lèvres.** Préférez des plans où le personnage est de dos, de loin, ou réagit pendant que la voix parle.
- Si un plan est raté, faites une **nouvelle prise** : la graine change à chaque fois.

## 8. Faire un Short

Dans l'onglet **Histoire**, cochez **Short YouTube**. Film Studio règle alors pour vous :

- le format vertical 9:16 ;
- une dizaine de plans de 4 secondes, soit environ 40 secondes ;
- les sous-titres incrustés.

Le découpage suit des consignes propres aux Shorts :

- **Accroche dès la première seconde** : une image forte et une réplique qui pose une question, sans introduction.
- **Une seule idée**, un rythme rapide, une chute.
- **Une fin qui boucle sur le début**, pour que la vidéo soit revue.
- **Un cadrage vertical** : le sujet au centre, en gros plan, et le tiers bas de l'image dégagé.

**Sous-titres incrustés** (onglet Production). Les répliques s'affichent mot à mot, en gros, et le mot prononcé passe en jaune. Ils sont placés dans la zone sûre des Shorts, au-dessus du titre et des boutons de YouTube. Vous pouvez aussi les activer pour un film normal. Le fichier `.srt` est toujours produit à côté.

**Fiche de publication** (bouton dans l'onglet Production). Claude écrit avec le découpage un titre court, une description qui finit par une question au public, et des hashtags (dont #Shorts). La fiche est aussi enregistrée à côté du film dans `export\`, sous le nom `…_publication.txt`. Ajoutez-y vos liens.

Au-delà de 60 secondes, Film Studio vous prévient : YouTube pourrait ne pas traiter la vidéo comme un Short.

## Licences des modèles

Film Studio est sous licence MIT, mais chaque modèle a ses propres règles. Vérifiez-les sur la page officielle du modèle avant tout usage commercial (par exemple une chaîne YouTube monétisée). En novembre 2026, les principaux cas sont les suivants :

| Modèle | Licence |
|---|---|
| Wan 2.2 | Apache 2.0, sans restriction |
| LTX-2 / LTX-2.5 | gratuit en usage commercial sous 10 M$ de revenus annuels |
| MiniMax H3 | demande d'autorisation séparée pour l'Union européenne, les États-Unis, le Royaume-Uni et la Corée du Sud |
| HunyuanVideo | exclut l'Union européenne |

## En cas de problème

| Message | Solution |
|---|---|
| « ComfyUI ne répond pas » | Vérifiez le dossier de ComfyUI et la case **Démarrer ComfyUI automatiquement** dans les Réglages, ou lancez `demarrer_comfyui.bat` pour voir ses messages. |
| « Ce modèle est protégé sur Hugging Face » (erreur 401) | Suivez les 3 étapes ci-dessus (compte, conditions acceptées, jeton), puis relancez `install.bat`. |
| Erreur 403 malgré le jeton | Les conditions du modèle ne sont pas encore acceptées sur sa page Hugging Face, ou le jeton est expiré. |
| « ComfyUI s'est arrêté au démarrage » | Lisez la fin du message (et `%APPDATA%\FilmStudio\comfyui.log`). Il s'agit souvent d'un pilote NVIDIA trop ancien. |
| « format interface » | Réexportez le workflow avec **Exporter (API)**. |
| « Value not in list » | Un modèle choisi dans le workflow manque dans votre ComfyUI. Ouvrez le workflow dans ComfyUI et corrigez-le. |
| « Limite d'utilisation… ChatGPT » | Le quota de Codex est atteint. Réessayez plus tard, ou passez les images sur ComfyUI. |
| « n'est pas connecté » | Relancez `install.bat` : il propose la connexion. |
| Qwen3-TTS échoue | Utilisez **Réglages → Voix → Diagnostiquer**. |
| Mémoire vidéo insuffisante | Réduisez la durée maximale d'un plan et la résolution, ou prenez une version fp8 ou distillée du modèle. |
