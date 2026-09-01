<p align="center">
  <img src="../assets/Recording%202026-08-29%20103658.gif" width="600">
</p>

<p align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh.md">中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <b>Français</b> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.ru.md">Русский</a>
</p>

## « Too Many Tools » — un agent de codage en ligne de commande. Il modifie des fichiers dans un espace de travail cloisonné, exécute du code dans une douzaine de langages, et commite et pousse automatiquement sur n'importe quel dépôt.

>**Nécessite Python 3.8+.**

## La commande est `tmtcode`

L'installation place une seule commande dans votre PATH :

```bash
tmtcode
```

Lancez-la depuis n'importe quel répertoire du système. **Le répertoire dans lequel vous
la lancez devient le projet sur lequel TMT travaille.**

```bash
cd ~/Projects/MyWebsite && tmtcode      # TMT travaille sur ~/Projects/MyWebsite
cd ~/Documents/MyProject && tmtcode     # TMT travaille sur ~/Documents/MyProject
```

Installez TMT une seule fois, où vous voulez. Vous ne le copiez jamais dans un projet, et
un projet n'a jamais besoin de contenir des fichiers de TMT.

## Installation

```bash
git clone https://github.com/lllons/TMT.git
cd TMT
pip install -e .                 # place `tmtcode` dans le PATH
pip install -e ".[live]"         # facultatif : ajoute requests et rich pour le flux et la couleur
```

L'agent lui-même n'a besoin de rien d'autre que la bibliothèque standard ; `requests` et
`rich` ajoutent seulement le flux en direct et la couleur, et TMT se rabat sur un mode
dégradé sans eux.

Après l'installation, laissez le clone là où il est et lancez `tmtcode` depuis l'endroit
où se trouve votre travail. Le clone est le domicile de TMT, pas votre projet.

Sans installation, un clone s'exécute quand même directement, et depuis n'importe où :

```bash
python /path/to/TMT/TMT.py                    # le répertoire courant est le projet
python /path/to/TMT/TMT.py ~/Projects/MyWebsite
```

Windows : `py`. macOS/Linux : `python3`.

## Les deux répertoires

TMT maintient sa propre installation et votre projet strictement séparés. Ce sont deux
choses distinctes et elles sont faites pour le rester.

| | Ce qui s'y trouve | Où il se trouve |
|---|---|---|
| **Répertoire d'installation** | le code source de TMT, votre clé API enregistrée, l'identité de co-auteur git de TMT, ses journaux | là où vous l'avez cloné — `~/tools/TMT`, `C:\Coding\TMT` — défini une fois, il ne bouge jamais |
| **Répertoire du projet** (l'espace de travail) | les fichiers que TMT lit, modifie, exécute et commite | là où vous avez lancé `tmtcode` |

Seul le répertoire du projet est modifié. Les fichiers propres à TMT restent dans le
répertoire d'installation quel que soit le projet dans lequel vous vous trouvez : c'est
donc le même agent — même clé, même adresse de co-auteur — partout.

Pour le dire clairement une fois de plus : **vous ne copiez pas TMT dans un projet pour
l'utiliser sur ce projet.** Un clone, une installation, puis `cd` vers n'importe quel
projet et tapez `tmtcode`.

## Choisir le répertoire du projet

| Commande | Répertoire du projet |
|---|---|
| `tmtcode` | le répertoire courant |
| `tmtcode ../other-repo` | résolu par rapport au répertoire courant, puis rendu absolu |
| `tmtcode /abs/path/to/project` | ce chemin |
| `tmtcode --dir PATH` | la même chose que le `PATH` positionnel, conservé pour l'usage existant |

Un chemin relatif est résolu par rapport au répertoire depuis lequel vous avez lancé la
commande, puis rendu absolu. Fournir à la fois un `PATH` positionnel et un `--dir` qui
désignent des répertoires différents est une erreur, et TMT s'arrête sans démarrer.

TMT utilise exactement le répertoire qu'on lui a donné. Il ne remonte pas l'arborescence
à la recherche d'une racine de projet : lancez-le dans `MyWebsite/src` et `MyWebsite/src`
est l'espace de travail.

Le chemin résolu est affiché au démarrage, de sorte qu'une exécution depuis le mauvais
endroit saute aux yeux :

```
Workspace: C:\Projects\my-repo
```

Tout ce qui se trouve hors de ce répertoire est interdit — un chemin qui sort de l'espace
de travail est refusé, pas suivi.

## Permissions et limites

- TMT peut créer, écraser et supprimer des fichiers n'importe où dans le répertoire du
  projet, et rien de ce qu'il y fait n'est récupérable si le répertoire n'est pas un
  dépôt git. Il a besoin des droits ordinaires de lecture et d'écriture dessus.
- Le répertoire d'installation doit être accessible en écriture : `.tmt_key` et `logs/` y
  sont écrits. `.tmt_git` et `.tmt_git.local` y sont seulement lus.
- Un répertoire est choisi, jamais créé. TMT refuse un chemin qui n'existe pas, un
  fichier, une racine de système de fichiers et votre répertoire personnel.
- Si le répertoire contient déjà des fichiers et n'est pas dans une copie de travail git,
  TMT décrit ce sur quoi il est sur le point d'être pointé et demande confirmation avant
  de démarrer. Un dépôt git est son propre mécanisme d'annulation, donc il démarre sans
  poser de question.
- TMT n'exécute jamais de commandes shell. Il n'exécute du code que via `run_file`, et ne
  lance que les deux applications listées ci-dessous.
- Le push utilise les identifiants git dont vous disposez déjà. TMT n'en stocke aucun et
  n'implémente aucune authentification.

## L'écran de démarrage

Chaque lancement de `tmtcode` s'ouvre sur le même écran : le logo TMT occupant le
terminal, et en dessous

```
                              Press Enter to Continue
```

La ligne pulse pendant l'attente — le dégradé la parcourant sur un terminal en couleur,
une lente pulsation de graisse sur un terminal sans couleur, et rien du tout là où les
séquences d'échappement ne peuvent pas être utilisées, parce qu'ici non plus la couleur
n'est jamais le message. Entrée est la seule touche qui poursuit ; Ctrl-C ferme TMT.
Tout le reste est ignoré, si bien qu'une première tâche tapée avant que l'écran ne se
soit stabilisé ne peut rien déclencher.

**L'écran de démarrage apparaît toujours.** Ce n'est pas un réglage et il n'y a rien à
désactiver. Ce qui *est* un réglage, c'est ce qui se passe après Entrée.

### Après Entrée : la vérification des mises à jour

Avec `Auto Update on Launch` **activé** (le comportement par défaut), TMT examine sa
propre copie git pour voir s'il existe une version plus récente, et le dit sur ce même
écran :

```
                              Searching for updates...
```

puis l'un de ces messages

```
                                    Up to date.            nothing was pulled, nothing restarted
                          Update complete. Restarting...   a fast-forward was applied
                            Continuing without updating.   an update could not be taken safely
                Update check failed. Continuing without update.
```

Avec le réglage **désactivé**, rien de tout cela n'a lieu et TMT ne fait pas semblant du
contraire — aucune ligne « searching » n'est affichée pour une recherche qui n'a jamais
eu lieu. Il passe directement à la suite.

Ensuite, TMT continue exactement comme il l'a toujours fait : la configuration de la clé
API si cette installation n'a pas encore été configurée, et sinon l'écran de démarrage
habituel.

### Quand TMT se met à jour et quand il ne le fait pas

Il ne se met à jour que lorsque la mise à jour est sans ambiguïté sûre, et il ne touche
jamais à votre travail.

| Ce qu'il trouve | Ce qu'il fait |
|---|---|
| déjà à jour | rien. Aucun pull, aucun redémarrage |
| dépôt distant en avance, arbre propre, fast-forward possible | fait un fast-forward, puis redémarre |
| **modifications locales non commitées** | refuse, et le dit. Vos modifications ne sont pas touchées |
| **la branche a divergé** — local et distant ont tous deux avancé | refuse. Les commits locaux ne sont jamais abandonnés |
| aucun upstream configuré, ou HEAD détachée | dit qu'il ne peut pas savoir, et continue |
| pas du tout une copie git | continue |
| pas de réseau, pas de git, un mauvais dépôt distant, une fusion échouée | signale l'échec et continue |

Il travaille sur la branche que vous avez déjà extraite et n'en crée, n'en change et n'en
force jamais aucune. Il utilise `git fetch` et `git merge --ff-only` et rien d'autre :
**il n'exécute jamais `git reset --hard`, `git clean`, une extraction forcée ou un simple
`git pull`** — un pull peut fusionner, et une fusion pendant le démarrage est exactement
ce qui ne doit pas arriver. Un test lit le code source de l'outil de mise à jour et
vérifie que ces commandes n'y figurent pas.

TMT reste utilisable sans connexion internet. Une vérification de mise à jour échouée est
une ligne sur l'écran de démarrage et rien de plus.

### Le redémarrage

Une mise à jour réussie remplace le processus par un nouveau, afin que le nouveau code
s'exécute réellement plutôt que de laisser les anciens modules chargés. Votre ligne de
commande est préservée — `tmtcode --dir ~/project` revient sous la forme
`tmtcode --dir ~/project`.

Vous revoyez alors l'écran de démarrage, ce qui est normal : l'écran de démarrage fait
partie de chaque lancement. Le processus redémarré se découvre à jour et continue.
**Il ne peut pas boucler** — au plus un redémarrage automatique a lieu par lancement, et
le second processus sait qu'il est le second.

### Le désactiver

Settings → `Auto Update on Launch`, Entrée pour basculer :

```
  AI Provider            Which service answers a request
  API Key                The credential that service is given
  Model                  Which model TMT runs on
> Auto Update on Launch  Check for a newer TMT after the launch screen  ON
  Back                   Return to the menu
```

Le réglage est stocké dans `.tmt_autoupdate` dans le répertoire d'installation, à côté des
réglages de modèle et d'effort ; il appartient donc à l'installation plutôt qu'à un projet
et survit aux redémarrages. Un fichier absent signifie activé ; un fichier illisible, ou
modifié en n'importe quoi, signifie également activé plutôt qu'une erreur au démarrage.

**Le désactiver ne désactive pas l'écran de démarrage.** L'écran est affiché dans les deux
cas.

## Premier lancement

Le premier lancement demande une [clé OpenRouter](https://openrouter.ai/keys) et
l'enregistre dans `.tmt_key` dans le répertoire d'installation (ignoré par git).
Définissez `OPENROUTER_API_KEY` pour sauter cette étape. Elle est demandée une fois pour
l'installation, pas une fois par projet.

Tapez une tâche à l'invite `Task>`. `quit` ou `exit` pour sortir. Ctrl-C annule la tâche
en cours sans fermer TMT.

Les fichiers de moins de 8 Ko sont montrés automatiquement au modèle, jusqu'à un nombre et
une taille totale fixés ; la liste le signale lorsqu'elle s'arrête prématurément. Les
fichiers plus gros sont lus à la demande.

## Ce que vous pouvez demander

En français courant. TMT choisit lui-même les actions.

```
Task> write a python script that fetches a URL and prints the status code
Task> what does report.py do?
Task> find every TODO in src and list them
Task> change the timeout in net.py from 5 to 30 seconds
Task> run hello.py
Task> open notes.txt in notepad
Task> commit the changes and push to main
```

## Vous parler : `send_message` et `end_conversation`

TMT dispose d'exactement deux façons d'afficher du texte à l'écran, et toute la
différence entre les deux tient à la question de savoir si la tâche se poursuit
ensuite.

| Action | Vous montre du texte | Termine la tâche |
|---|---|---|
| `send_message` | oui | **non** — la main revient à l'agent, à chaque fois |
| `end_conversation` | oui | **oui** — et c'est la seule action qui en termine une |

**`send_message` sert à dire des choses en chemin.** « Je vais d'abord lire
l'analyseur », « les tests sont au vert, place à la documentation », « ce fichier
est plus gros que prévu ». Le message est affiché dans la session, où vous pouvez
y revenir en remontant, puis TMT reprend exactement là où il en était. Il peut
être utilisé autant de fois dans une tâche qu'il est utile de le faire ; il n'y a
pas de limite et rien en lui n'est définitif.

**`end_conversation` est la fin.** Son message est le résumé que vous avez sous les
yeux à la fin, ce qui explique pourquoi il est dit à l'agent qu'un travail non
décrit là-dedans pourrait tout aussi bien ne pas avoir eu lieu. Il n'existe pas de
seconde façon de s'arrêter : pas de `done` séparé, et pas d'indicateur sur un
message qui en ferait discrètement le dernier. TMT termine une tâche avec cette
action, ou il ne la termine pas.

**Vouloir terminer n'est pas la même chose qu'y être autorisé.** `end_conversation`
est ce que retiennent les verrous d'achèvement, et toute capacité que vous avez
activée pour cette invite peut le refuser : un plan dont des étapes restent
inachevées, une revue qui n'est pas passée, une vérification qui n'a pas été
exécutée ou qui a trouvé un contrôle en échec. Un refus n'est pas une erreur et il
ne termine pas le tour — la raison est transmise à TMT, qui se remet au travail, et
la réponse reste à dire. Voir
[Capacités](#capacités--plan-review-verify).

Les agents d'arrière-plan n'ont ni l'un ni l'autre de ces canaux dans un sens utile :
personne ne les lit, donc un message coûte une étape et n'atteint personne, et leur
fin est un rapport adressé à l'agent principal. Voir
[Agents en arrière-plan](#agents-en-arrière-plan).

## Capacités : `/plan`, `/review`, `/verify`

Trois des choses que TMT sait faire ne relèvent pas du travail ordinaire avec les
outils. Écrire un plan et devoir s'y tenir, faire auditer le diff par un second
agent, et exécuter les contrôles propres au dépôt coûtent chacun une exécution de
modèle supplémentaire complète, occupent une colonne à l'écran, et peuvent refuser
la réponse finale de TMT lui-même. Ce sont vos ressources à dépenser : elles sont
donc désactivées tant que vous ne les demandez pas, et vous les demandez en écrivant
la commande dans votre invite.

| Commande | Active |
|---|---|
| `/plan` | le processus de planification — TMT écrit les étapes et ne peut pas terminer avant qu'elles ne soient faites |
| `/review` | la revue de code indépendante — un agent distinct, en lecture seule, audite le travail |
| `/verify` | la vérification intelligente — les contrôles que ce dépôt possède réellement, exécutés pour de vrai |

```
Task> build me an authentication system
        nothing gated. Ordinary tools, ordinary answer.

Task> build me an authentication system /plan
        plans the work and is held to the plan. No review, no verification.

Task> fix this implementation /review
        an independent reviewer audits it before the answer goes out.

Task> add the endpoint /plan /verify /review
        the whole pipeline: plan, implement, verify, review, fix, answer.
```

**La barre oblique fait toute la différence, et `verify` seul ne suffit pas.**
« verify this code », « please verify this », « verified » et « verification » sont
des choses que l'on dit en demandant du travail ordinaire, et aucune d'elles
n'active le moteur. Seul `/verify` le fait. Il en va de même pour `plan` et
`review` : « review my code please » est une demande d'avis, `/review` est une
demande adressée au relecteur indépendant, soumis à verrou et à un nombre de cycles
limité.

Un mot plus long qui commence par l'un d'eux n'a pas cet effet non plus.
`/planning`, `/planner`, `/plan123`, `/reviewing` et `/verification` sont du texte
ordinaire, tout comme une commande à l'intérieur d'un chemin — `src/review` et
`abc/verify` sont des chemins, pas des commandes.

Les autres règles sont celles que vous devineriez :

- **N'importe où dans l'invite.** Au début, au milieu, à la fin, ou sur leurs propres
  lignes dans un bloc collé. `/plan Build it`, `Build it /plan` et `Build the /plan
  feature` sont la même demande.
- **Autant de fois que vous voulez.** `/plan ... /plan` active la planification une
  fois. Il n'existe pas deux plans.
- **N'importe quelle casse.** `/PLAN` fonctionne, et reste `/PLAN` à l'écran — TMT
  met votre texte en forme et ne le réécrit jamais.
- **Indépendantes.** `/plan` n'active ni la revue ni la vérification, et aucune de
  ces deux-là n'active les autres. C'est vous qui choisissez le processus.
- **Une invite à la fois.** Une capacité est autorisée pour la requête qui l'a
  demandée. La question suivante repart de rien, à moins qu'elle ne la demande aussi.

**Vous seul pouvez les activer.** Ni TMT, ni un agent d'arrière-plan, ni un
relecteur, ni un fichier qu'il a lu. Un modèle qui décide que la tâche paraît assez
grosse pour mériter un plan, écrit `/plan` dans son propre raisonnement et appelle
l'action se voit refuser par le runtime — l'autorisation est lue sur la ligne que
vous avez tapée et sur rien d'autre. C'est appliqué deux fois : les verbes non
autorisés sont purement et simplement absents de l'invite, et le répartiteur les
refuse à nouveau si l'un d'eux est malgré tout invoqué.

**Elles sont mises en évidence à la frappe.** Une commande valide dans la zone de
saisie porte le dégradé rouge → orange → vert, si bien que vous voyez ce que vous
avez activé avant d'appuyer sur Entrée, et vous le voyez disparaître si vous faites
une faute de frappe. Seule la commande exacte est colorée : `verify` reste neutre et
`/verification` reste neutre. Sur un terminal sans couleur, la commande est
distinguée en gras et souligné à la place, et dans une exécution redirigée il n'y a
aucune mise en forme — la ligne se lit toujours `/plan`, c'est-à-dire la commande
écrite en toutes lettres.

Pendant que le tour s'exécute, tout ce que vous avez autorisé est listé en haut de la
colonne de droite :

```
                                                        CAPABILITIES 2
                                                        ● /plan
                                                        ● /verify

                                                        PLAN 2/5
```

**`/plan`, `/review` et `/verify` employés seuls restent les rapports** qu'ils ont
toujours été — voir [Commandes slash](#commandes-slash). Une ligne qui ne contient
rien d'autre que la commande vous montre ce que TMT est en train de faire ; une ligne
qui contient une tâche autorise la capacité pour cette tâche.

### Fichiers

| Action | Rôle |
|---|---|
| `write_file` / `write_files` | Créer un fichier, ou plusieurs d'un coup |
| `patch_file` | Rechercher-remplacer — l'option par défaut pour les modifications |
| `replace_lines` | Remplacer une plage de lignes exacte |
| `append_file` | Ajouter à la fin d'un fichier |
| `read_file` / `read_lines` | Lire un fichier entier, ou une plage de lignes |
| `glob` | Trouver des fichiers et des répertoires par motif de chemin |
| `grep` | Chercher dans le contenu des fichiers et indiquer le chemin, le numéro de ligne et la ligne |
| `copy_file` / `rename_file` / `delete_file` | Déplacer, renommer, supprimer |
| `create_folder` / `delete_folder` | Dossiers (la suppression récursive est explicite) |
| `list_files` | Lister l'espace de travail |

Les chemins sont interprétés relativement au répertoire du projet, et tout ce qui se
résout à l'extérieur est refusé. Seul ce répertoire est listé, lu ou écrit.

La modification d'un fichier existant passe par `patch_file`, pas par une réécriture,
afin que les lignes non touchées le restent. Les fichiers Python sont contrôlés
syntaxiquement avant d'être écrits ; une modification qui les casse est rejetée
plutôt qu'enregistrée.

### Le plan

**Demandez-le avec `/plan` dans votre invite.** Sans cette commande, TMT n'écrit
aucun plan et rien ici ne conditionne la réponse. Voir
[Capacités](#capacités--plan-review-verify).

Avec `/plan`, pour tout travail conséquent — ajouter une fonctionnalité, corriger un
bug qui traverse le dépôt, remanier un sous-système, mettre à jour la documentation
d'un projet entier — TMT écrit un plan avant de commencer et le déroule sous vos
yeux. Il apparaît dans une colonne à droite de la zone vivante pendant le travail, et
il y reste, terminé, à côté de l'invite suivante.

```
                                                        PLAN 2/5
                                                        ─────────────────────────
                                                        S1 ✓ Inspect repository
 09:14 · OpenRouter · MiniMax M3                        S2 ✓ Find and…erminology
 ───────────────────────────────────────────────────    S3 ● Update documentation
 > Describe your next task                              S4 ○ Run tests and verify
 ───────────────────────────────────────────────────    S5 ○ Explain changes
```

| Marque | Statut | Couleur | Signification |
|---|---|---|---|
| `✓` | completed | vert | le travail de cette étape est réellement fait |
| `●` | in progress | orange | l'unique étape en cours de traitement |
| `○` | pending | rouge | encore à venir |
| `!` | blocked | ambre | elle ne peut pas avancer, et elle compte toujours comme inachevée |

Exactement une étape est en cours à la fois. En terminer une promeut la suivante
d'elle-même. La couleur est une confirmation, jamais le message : chaque statut porte
aussi une marque, et toute la colonne se dégrade en `+ > - !` et en filets ASCII sur
un terminal incapable de dessiner le reste.

**Le plan est un contrat, pas une barre de progression.** TMT n'a pas le droit de
terminer une tâche tant qu'une étape reste en suspens. Une réponse finale envoyée
alors qu'il reste du travail ne vous est pas montrée du tout — le runtime la refuse,
rend au modèle la liste des étapes qu'il doit encore, et le tour continue. C'est le
programme qui l'impose plutôt que l'invite qui le demande : un modèle qui décide
qu'il a fini n'a donc pas fini pour autant :

```
Task> add the feature
 · Planning the work in two steps.
 ◆ Plan created with 2 steps.
 ▲ Plan not finished - 2 steps outstanding, next is S1 Implement the feature. Continuing.
 · The feature is in; running the tests next.
 ◆ S1 (Implement the feature) in_progress -> completed.
 · Suite is green.
 ◆ S2 (Run the tests) in_progress -> completed.
 ┌──────────────────────────────────────────────────────────────┐
 │ Added the feature and the suite is green: 12 tests, 0 failures.│
 └──────────────────────────────────────────────────────────────┘
```

Le plan peut être révisé dès que le travail se révèle différent de ce qui était
prévu — étapes renommées, ajoutées, supprimées, ou plan entier remplacé. Deux choses
ne peuvent pas arriver. Une étape terminée n'est jamais rouverte : une étape finie
reste finie, et un plan dont la forme était mauvaise est remplacé purement et
simplement plutôt que déroulé à l'envers. Et un plan sur lequel du travail a été fait
ne peut pas être abandonné — c'était le seul moyen de contourner le verrou, si bien
que l'effacement est refusé dès qu'une étape est terminée. Le mener à bout et le
remodeler sont l'un comme l'autre visibles à l'écran ; l'abandonner discrètement ne le
serait pas.

**Tout ne donne pas lieu à un plan.** « Que fait cette fonction ? » appelle une seule
réponse, et un plan pour cela serait du bruit à l'écran et un verrou sur la réponse de
TMT lui-même. Les plans sont faits pour le travail qui comporte des étapes.

**Le plan appartient à la tâche, pas à la session.** Il est retiré dès que vous posez
la question suivante, si bien qu'un plan inachevé ne peut jamais retenir la réponse à
quelque chose qui n'a rien à voir. Rien n'est écrit sur le disque. Les agents
d'arrière-plan ne peuvent ni le voir ni le modifier — c'est le contrat de l'agent
principal avec vous, et une étape terminée par un agent secondaire permettrait à TMT
de conclure sur un travail que celui-ci n'aurait fait que revendiquer.

**Sur un terminal de moins de 45 colonnes**, la colonne n'est pas dessinée — la zone
de saisie a plus besoin de la place — et `/plan` affiche la même chose sous forme de
texte ordinaire, quelle que soit la largeur.

### Comprendre un dépôt

Neuf actions pour se repérer dans une base de code sans la lire en entier. Chacune
répond à une question, et il est demandé à TMT de choisir la plus étroite qui convienne.

| Action | Rôle | Y recourir quand |
|---|---|---|
| `tree` | Répertoires, fichiers, tailles, imbrication. Ne lit aucun contenu | Vous avez besoin de la forme du projet |
| `glob` | Fichiers et répertoires correspondant à un motif de chemin. `*` s'arrête à un `/`, `**/` signifie n'importe quelle profondeur, et un motif sans `/` correspond à un nom où qu'il se trouve | Vous devez savoir quels fichiers existent, ou où se trouve l'un d'eux |
| `grep` | Chercher à l'intérieur des fichiers, en indiquant le chemin, le numéro de ligne et la ligne. Exact et sensible à la casse par défaut ; la requête peut couvrir plusieurs lignes | Vous connaissez le texte que vous cherchez |
| `find_symbol` | Où une fonction, une classe, une méthode, une constante ou un type est *défini* | Vous voulez une définition, pas une mention |
| `code_map` | Ce qui définit ceci, ce qui l'importe, ce qu'il importe, où il est référencé | Vous devez savoir ce qu'un changement affecterait |
| `replace_across` | La même modification exacte dans de nombreux fichiers | Renommer quelque chose que tout le projet utilise |
| `related_tests` | Lit le diff git et nomme les tests qu'il vaut la peine de lancer | Vous avez changé une chose et ne voulez pas tout exécuter |
| `remember` / `recall` | Notes durables sur ce projet, conservées entre les sessions | Quelque chose vous a coûté du temps à élucider |

```
Task> show me the project structure
Task> find every place that calls self.workspace_root
Task> where is calculate_total defined?
Task> what imports agent_file_ops?
Task> rename old_function_name to new_function_name across src
Task> which tests should I run for what I just changed?
```

**`glob` trouve les fichiers par chemin ou par nom ; `grep` trouve le texte à l'intérieur
des fichiers.** C'est là toute la distinction, et c'est celle qu'il vaut la peine de bien
saisir : l'ordre qui fonctionne, c'est `glob` pour trouver les fichiers candidats, `grep`
pour trouver les lignes qu'ils contiennent, `read_lines` pour lire la région, puis
modifier, puis tester — plutôt que de lire tout un dépôt pour trouver une seule ligne.

```json
{"action": "glob", "pattern": "agent_*.py"}
{"action": "glob", "pattern": "testing/**/*.py"}
{"action": "grep", "query": "end_conversation"}
{"action": "grep", "query": "def run_file", "glob": "agent_*.py"}
{"action": "grep", "query": "timeout", "path": "src", "ignore_case": true}
```

`grep` est exact et sensible à la casse par défaut, comme l'outil dont il porte le nom.
`"ignore_case": true` le rend souple, `"regex": true` lit la requête comme une expression
régulière, `"context"` ajoute des lignes de part et d'autre de chaque correspondance, et
`"path"` ou `"glob"` restreint les fichiers qui sont lus. Il ne renvoie jamais un fichier
entier : vous obtenez le chemin, le numéro de ligne et la ligne, et `read_lines` vous
donne le reste.

**`replace_across` fait une prévisualisation par défaut.** Il indique combien de fichiers
et d'occurrences il *changerait* et n'écrit rien. Renvoyer la même action avec
`"apply": true` l'exécute. Les fins de ligne et l'encodage sont préservés, les fichiers
binaires sont ignorés, et un remplacement qui rendrait un fichier Python impossible à
analyser est refusé plutôt qu'écrit.

**Les faits et les suppositions sont étiquetés différemment.** Les symboles Python sont
trouvés en analysant le fichier, donc ces réponses sont exactes ; les autres langages sont
reconnus lexicalement et le disent. `related_tests` sépare ce que le diff prouve de ce
qu'il ne fait que supposer. Rien ne présente une heuristique comme une mesure.

**La mémoire de projet** est stockée à côté des réglages propres à TMT, indexée par
projet, jamais dans votre dépôt — la même règle que pour tout autre état de TMT. Les
notes sont analysées avant d'être écrites, et tout ce qui a la forme d'une clé, d'un
jeton ou d'un mot de passe est refusé.

### Exécuter du code

`run_file` exécute et renvoie la sortie. Python, JavaScript, TypeScript, Ruby, PHP, Lua,
Perl, R, Go, C, C++, Java. Délai d'expiration de 10 secondes. La chaîne d'outils doit se
trouver dans votre PATH. Le code s'exécute avec le répertoire du projet comme répertoire
de travail.

### Applications

`open_app` lance le Bloc-notes, ou l'Explorateur avec un fichier sélectionné. Rien
d'autre — TMT n'exécute jamais de commandes shell.

## Git

TMT commite dans le dépôt qui contient le répertoire du projet — pas dans le dépôt propre
à TMT. Vous restez l'auteur et le committer de chaque commit qu'il crée ; TMT est crédité
à vos côtés par une ligne `Co-authored-by`.

```
Task> commit this                        commits, does not push
Task> commit these changes and push       commits and pushes
Task> push this to main                   targets main
Task> fix the bug                         edits only, no commit, no push
```

Commit et push sont séparés. TMT ne pousse que lorsque vos propres mots en ont demandé
un — « fix the bug » ne déclenche jamais un push, et terminer une modification non plus.

Actions : `git_status`, `git_diff`, `git_identity`, `git_commit`, `git_push`.

Il n'indexe que les fichiers qu'il a modifiés, si bien que votre travail sans rapport
reste non commité. Il ne crée jamais de branche, n'invente jamais de dépôt distant et ne
force jamais un push. Si un push échoue, le commit reste local et vous obtenez l'erreur
réelle.

### La co-signature de TMT

TMT ne commite pas à votre place, et il ne commite pas à votre insu. L'identité git du
dépôt est l'auteur et le committer. TMT ajoute une ligne au message et rien d'autre. Un
seul commit, vous deux crédités dessus.

Un commit créé par TMT, tel que git le rapporte :

```
$ git log -1 --format=fuller
commit f1977e70a471011bf9b5ab643aecdf5e18a8e8fa
Author:     Liam <liam@example.com>
AuthorDate: Sat Aug 29 13:10:01 2026 +1200
Commit:     Liam <liam@example.com>
CommitDate: Sat Aug 29 13:10:01 2026 +1200

    Add a greeting file

    Co-authored-by: TMT code <TMT.tmt.code@gmail.com>
```

C'est un trailer git, pas une ligne de prose qui contient par hasard un deux-points, si
bien que git lui-même vous le restituera :

```
$ git log -1 --format=%(trailers:key=Co-authored-by)
Co-authored-by: TMT code <TMT.tmt.code@gmail.com>
```

Ce que cela signifie en pratique :

- L'auteur et le committer sont ceux que la configuration git du dépôt désigne. TMT ne se
  met jamais dans l'un ou l'autre champ, et n'écrit jamais dans votre configuration git,
  ni globale ni par dépôt.
- Si vous n'avez aucune identité git définie, git refuse le commit. TMT le signale, vous
  dit de définir vous-même `user.name` et `user.email`, et ne se substitue pas à l'auteur.
  Rien n'est commité.
- TMT n'ajoute la ligne qu'aux commits qu'il crée. Un `git commit` que vous lancez
  vous-même n'est pas touché.
- Un message qui crédite déjà l'adresse de TMT reçoit une ligne, pas deux. La
  correspondance porte sur l'adresse, donc la même adresse sous un autre nom d'affichage
  compte quand même comme déjà créditée.
- Les trailers existants survivent. Un `Co-authored-by:` pour quelqu'un d'autre est
  conservé et TMT est ajouté à côté ; un bloc `Signed-off-by:` est rejoint plutôt que
  repoussé dans un nouveau paragraphe.
- L'historique n'est jamais réécrit. Si le commit final se retrouve malgré tout sans la
  ligne, TMT le signale et laisse le commit tranquille plutôt que de le modifier.
- Sans adresse TMT configurée, ou avec l'adresse fictive livrée par défaut toujours en
  place, TMT refuse de commiter et n'indexe rien.

Le crédit sur GitHub est une question distincte, et TMT ne décide pas de la réponse :

- TMT décide des métadonnées du commit — la ligne, et rien que la ligne.
- GitHub décide de créditer ou non le co-auteur. Il compare l'adresse de la ligne aux
  adresses vérifiées sur un compte. Une adresse vérifiée sur aucun compte n'est créditée à
  personne, et le nom d'affichage seul ne fait rien.
- Même lorsque l'adresse correspond, les données de contributeur et de profil de GitHub
  peuvent avoir du retard. Un push ne modifie pas nécessairement le graphe des
  contributeurs tout de suite.
- Obtenir le crédit n'est pas la même chose qu'avoir le droit de pousser.
  L'authentification est séparée et reste la vôtre.

### Identité de co-auteur

```
TMT_GIT_NAME=TMT code
TMT_GIT_EMAIL=someone@example.com
```

L'adresse sous laquelle TMT est crédité. Elle n'est jamais écrite dans un commit comme
auteur. Lue dans cet ordre : les variables d'environnement `TMT_GIT_*`, puis
`.tmt_git.local` (ignoré par git, propre à la machine), puis `.tmt_git` (suivi par git,
livré avec le projet). Le nom vaut `TMT code` par défaut. L'adresse n'a pas de valeur par
défaut, et n'est jamais prise dans votre configuration git — votre configuration git
fournit l'auteur, pas le co-auteur.

Les deux fichiers se trouvent dans le répertoire d'installation, si bien que TMT est
crédité sous la même adresse dans tous les projets sur lesquels vous le pointez.

`.tmt_git` est suivi par git à dessein : une adresse de commit est une métadonnée
publique, pas un identifiant secret, si bien que chaque clone obtient le même co-auteur
TMT sans configuration. Il contient un nom et une adresse, et rien d'autre. N'y mettez
aucun jeton, mot de passe ou clé, pas plus que dans `.tmt_git.local`.

Lancez `git_identity` pour voir quelle source l'a emporté, quels fichiers ont été
consultés, et si l'adresse est utilisable.

### Configurer l'attribution GitHub

Le `.tmt_git` suivi par git nomme l'adresse vérifiée sur le compte GitHub qui représente
TMT, si bien qu'un clone tout neuf le crédite correctement sans configuration. Si cette
adresse est un jour une valeur fictive, TMT refuse de commiter plutôt que de créditer un
co-auteur qui n'identifie personne. Pour créditer un compte à vous à la place :

1. Créez un compte GitHub pour TMT.
2. Ajoutez-y une adresse et vérifiez-la.
3. Mettez cette adresse dans `.tmt_git.local`, ou dans `.tmt_git` et commitez-la une fois.

Quatre choses distinctes, dont TMT ne décide que d'une seule :

- **La paternité** — l'auteur et le committer écrits dans le commit. Les vôtres. TMT les
  relit pour les rapporter et ne les définit ni ne les modifie jamais, et vos `user.name`
  et `user.email` ne sont pas modifiés, ni globalement ni par dépôt.
- **Le crédit de co-auteur** — la ligne `Co-authored-by`. La seule partie du commit dont
  TMT décide.
- **L'attribution GitHub** — GitHub faisant correspondre l'adresse de cette ligne à un
  compte vérifié. Hors du contrôle de TMT, et un nom d'affichage seul ne fait rien.
- **L'authentification** — qui a le droit de pousser. Reste la vôtre : votre clé SSH,
  votre gestionnaire d'identifiants ou votre connexion `gh`. TMT ne stocke aucun
  identifiant et n'implémente aucune authentification.

## Vérification

**Demandez-la avec `/verify` dans votre invite.** Sans cette commande, rien de tout ceci
n'a lieu et rien ici ne conditionne la réponse ; le simple mot `verify` ne suffit pas.
Voir [Capacités](#capacités--plan-review-verify).

Avec `/verify`, avant que TMT ne soit autorisé à dire qu'un travail est terminé, il
exécute les contrôles que ce dépôt possède réellement, et le runtime ne le laissera pas
répondre tant qu'ils ne passent pas.

Tout l'intérêt tient à la distinction entre une preuve et une opinion :

> « Ça devrait marcher » n'est pas une vérification. `43 passed, 0 failed` en est une.

TMT ne demande pas au modèle si le code fonctionne. Il lit le dépôt, détermine avec quoi
ce projet se teste, se contrôle et se compile lui-même, lit le diff pour voir ce qui a
changé, choisit les contrôles qui valent la peine d'être exécutés pour *ce*
changement-là, les exécute, et rapporte les codes de sortie. Rien de ce que le modèle
écrit ne peut déplacer ce résultat — aucune clé d'aucune action ne définit un statut, et
un contrôle passe quand un processus se termine avec le code zéro et à aucun autre
moment.

### Ce qu'il décide d'exécuter

**Il préfère vos commandes à ses propres suppositions.** Dans l'ordre :

1. une commande que ce dépôt définit nommément — un script `package.json`, une cible de
   `Makefile`, un `run_tests.py` à la racine
2. un outillage que ce dépôt configure — `[tool.ruff]`, `[tool.mypy]`, `tsconfig.json`
3. le gestionnaire de paquets du projet — `npm`, `pnpm`, `yarn`, `bun`, `uv`, `poetry`
4. la commande standard de l'écosystème — `cargo test`, `go vet`, `pytest`
5. une supposition, étiquetée comme telle

Si votre `package.json` dit `"test": "vitest run"`, TMT exécute `npm run test`. Il ne
décide pas que les projets node utilisent jest. Si votre dépôt contient un
`run_tests.py`, c'est cela la commande de test — même là où `pytest` fonctionnerait
aussi, parce qu'exécuter autre chose que ce que vous exécutez et appeler cela votre
vérification serait faux même en cas de succès.

La configuration d'intégration continue est lue comme une *preuve* des outils que vous
utilisez réellement, et jamais comme une source de commandes. Rien de ce que TMT exécute
n'est une chaîne extraite d'un fichier du projet : ce qui est extrait, c'est un nom, et
la commande est construite autour de lui à partir d'une table fixe. Il n'y a de shell
nulle part sur ce chemin.

**Il exécute les contrôles bon marché avant les coûteux**, et s'arrête au premier qui
échoue :

| Niveau | Quoi |
|---|---|
| 1 | syntaxe et formatage de ce qui a changé |
| 2 | lint, vérification de types, contrôles du compilateur |
| 3 | les tests qui nomment ce que vous avez changé |
| 4 | les tests qui les entourent |
| 5 | la compilation du projet |
| 6 | la suite complète |

Une fois que le vérificateur de types a échoué, les dix minutes que prendrait la suite
d'intégration sont dix minutes passées à mesurer un arbre déjà connu pour être faux.
Tout ce qui suit un échec est rapporté comme ignoré, avec cet échec pour raison — de
sorte que ce qui n'a *pas* été contrôlé est visible plutôt que sous-entendu.

**Il va plus loin quand le changement est plus risqué.** Authentification, migrations,
schéma de base de données, contrats d'API, concurrence, frontières du système de
fichiers, exécution shell, configuration de dépendances ou de compilation — ou
simplement un grand nombre de fichiers d'un coup — donnent droit à la suite complète. Un
changement purement documentaire reçoit les contrôles statiques et aucune exécution de
tests.

### Les quatre issues, tenues séparées

| | Signification |
|---|---|
| **PASSED** | la commande s'est exécutée et s'est terminée avec 0. La seule preuve qui existe |
| **FAILED** | la commande s'est exécutée et s'est terminée avec un code non nul. Quelque chose ne va pas, et la sortie dit quoi |
| **SKIPPED** | elle n'a pas été exécutée — l'outil n'est pas installé, ou un contrôle précédent avait déjà échoué |
| **ERROR** | elle n'a pas pu s'exécuter ou n'a pas abouti. On ne sait rien, et ce n'est *pas* un échec de votre code |

Elles ne sont jamais réduites à un booléen. Un délai dépassé n'est pas un échec ; un
linter manquant n'est pas un lint réussi. TMT n'installera rien pour combler l'un de ces
trous — une dépendance manquante est signalée, jamais réparée en douce.

### À quoi cela ressemble

```
VERIFY 1/3
✓ Syntax          passed
✓ Lint            passed
✗ Targeted tests  2 failed, 41 passed
– Full suite      not run: Targeted tests did not pass
```

`/verify` affiche l'exécution complète, y compris la sortie de tout ce qui a échoué.

### Quels tests il choisit

Pour un projet dont la commande de test accepte des chemins, TMT détermine quels tests le
changement atteint et exécute ceux-là en premier. Un fichier de test compte comme
**ciblé** lorsqu'il y a une preuve en ce sens — il nomme un module modifié, importe un
symbole modifié, ou se trouve là où la convention de nommage du projet dit qu'il devrait
être — et comme **apparenté** lorsqu'il s'agit d'une supposition d'accessibilité. Les
deux sont tenus séparés et classés à des niveaux différents, parce qu'une supposition
présentée comme une mesure est pire que pas de sélection du tout.

Là où la commande de test du projet *ne peut pas* être restreinte à des chemins —
`npm test`, ou un `run_tests.py` qui exécute tout —, TMT le dit et exécute la suite
complète comme preuve de test. Il n'exécute pas tout en l'étiquetant comme ciblé.

### Quand cela se produit

La vérification est requise lorsque le runtime a constaté les **deux** moitiés d'un
travail conséquent : un plan d'au moins trois étapes, et au moins un fichier réellement
écrit — les deux mêmes faits sur lesquels une revue est décidée, observés plutôt que
revendiqués. Une question, une lecture ou un petit correctif sans plan n'est pas
conditionné du tout.

Vous passez outre dans les deux sens avec vos propres mots. « …and run the tests »
l'active ; « no verification needed » la désactive. Ne rien dire laisse la décision aux
preuves.

Elle a aussi sa place dans le plan. Une étape du plan nommée pour la vérification ne peut
pas être marquée comme terminée tant que la vérification est en suspens — ce refus est
dans le code, pas dans une invite. Et la réponse finale exige les trois : **le plan
complet, la vérification réussie et la revue passée.** Aucun des trois n'en dispense un
autre.

### Le cycle, et ses limites

Un contrôle en échec est un retour d'information, pas la fin de la tâche : TMT lit la
sortie, corrige ce qu'elle signale et vérifie à nouveau. Trois tours au maximum — au-delà,
la réponse est libérée plutôt que retenue indéfiniment, accompagnée d'une ligne disant que
la vérification n'est jamais passée. Le silence serait le pire des échecs.

**Un succès devient obsolète dès que le code bouge sous lui.** Modifier quoi que ce soit
après une vérification réussie signifie que ce qui est passé n'est pas ce qui serait
livré, et la réponse suivante est retenue jusqu'à ce que tout ait été vérifié de nouveau.
C'est ce qui fait que la boucle corriger-vérifier se referme au lieu d'être une simple
suggestion.

Si un dépôt ne contient absolument rien d'exécutable — pas de commande de test, pas de
linter, rien d'installé —, la vérification le dit et la réponse est libérée, et TMT doit
vous dire clairement que le travail n'est pas vérifié. « Je n'ai pas pu vérifier ceci »
est utile ; « vérifié » alors que rien n'a été exécuté ne l'est pas.

## Revue indépendante

**Demandez-la avec `/review` dans votre invite.** Sans cette commande, aucun relecteur
n'est lancé et rien ici ne conditionne la réponse. Voir
[Capacités](#capacités--plan-review-verify).

La vérification et la revue répondent à des questions différentes, et un changement
conséquent a besoin des deux. La vérification demande *est-ce que cela passe des
contrôles exécutables* ; la revue demande *est-ce le bon changement, et est-il sûr*. Une
suite de tests au vert dit que le code fait ce que ses tests disent — elle ne dit pas que
les tests sont les bons tests, et elle ne remarque pas que vous avez construit la
mauvaise fonctionnalité.

TMT relit son propre travail avant d'être autorisé à le déclarer terminé — et pas en se
le demandant à lui-même. Un **agent distinct** lit le dépôt, le diff et votre demande
d'origine, sans en avoir écrit une ligne, et rapporte ce qu'il a trouvé. L'agent principal
doit agir sur les constats bloquants, et le runtime ne le laissera pas répondre tant
qu'une revue n'est pas réellement passée.

Tout l'intérêt tient à l'échec qu'une suite de tests au vert ne détecte pas :

> Vous avez demandé une authentification avec prise en charge des jetons de
> rafraîchissement. Les tests passent. Le relecteur lit le diff et découvre que
> l'expiration des jetons de rafraîchissement n'est jamais vérifiée, et que `/health` est
> discrètement passé derrière l'authentification. Ni l'un ni l'autre n'est testé, parce
> que c'est le même agent qui a écrit le code et les tests.

### Le cycle

```
      your request
           |
      plan --> implement --> tests
           |
      independent review
           |
   +-------+--------+
   |                |
 PASS            FINDINGS
   |                |
   |          main agent fixes
   |                |
   |            tests again
   |                |
   +-------<---- review again
           |
     plan complete
           |
      final answer
```

### Ce que le relecteur voit, et ce qu'il ne voit pas

Il reçoit la **demande d'origine de l'utilisateur** — traitée comme la source de vérité —,
le plan écrit par l'agent qui a implémenté, l'état d'achèvement de ce plan, `git status`,
`git diff`, `git diff --stat`, le commit courant, les chemins qui ont réellement été
écrits, et une note sur ce qui a réellement été exécuté dans la session. Tout le reste, il
va le chercher lui-même : les fichiers modifiés, le code qui les appelle, les tests, les
conventions propres au projet. Le diff vient en premier et le reste n'est exploré que là
où le diff ne peut pas répondre à la question.

Il est en **lecture seule**, ce qui est appliqué par le code plutôt que demandé dans son
invite : tout verbe d'écriture est refusé avant d'être exécuté. Il rapporte ; c'est
l'agent principal qui effectue chaque changement. Il ne peut pas non plus exécuter quoi
que ce soit, donc il ne peut pas relire le résultat de sa propre exécution — ce que la
session a exécuté lui est présenté comme un fait observé, et juger de ce que cela prouve
est son travail.

Il lui est dit, en toutes lettres, de ne pas se fier aux tests, de ne pas se fier au plan,
et de ne pas se fier au compte rendu que l'agent implémenteur fait de son propre travail.

### Les constats

Chaque constat porte une sévérité, un fichier, une ligne lorsqu'une ligne a réellement été
lue, la preuve retenue, la raison pour laquelle cela compte, et une direction pour la
correction.

| Sévérité | Bloque l'achèvement |
|---|---|
| `CRITICAL` | oui — exactitude, sécurité, perte de données, comportement destructeur |
| `MAJOR` | oui — un vrai bug, un comportement requis manquant, une régression probable, un test important manquant |
| `MINOR` | non — un défaut mineur qu'il vaut la peine de corriger |
| `SUGGESTION` | non — facultatif |

À côté des constats, le relecteur renvoie une **liste de contrôle des exigences**
construite à partir de votre demande, chacune marquée satisfaite, partielle ou non
satisfaite. C'est la partie qui distingue « ce code a-t-il l'air bon » de « avons-nous
construit ce qui était demandé », et c'est le contrôle qui rattrape une implémentation
impeccable de la mauvaise fonctionnalité.

Le verdict est `PASS`, `PASS_WITH_WARNINGS` ou `FAIL`. Une réponse qui revendique un
succès tout en listant un constat bloquant est enregistrée comme un **FAIL** — ce sont les
constats qui décident, et la revendication du relecteur est affichée à côté pour que la
contradiction soit visible.

### Ce qui empêche de le contourner

- **Seule une vraie revue peut produire un succès.** Aucune clé d'aucune action ne définit
  un verdict. La seule chose qui fait bouger l'état de la revue est la sortie de l'agent
  relecteur lui-même, analysée et validée champ par champ. Un modèle qui écrit « review
  passed » écrit une phrase, et les phrases ne font pas bouger la machine à états.
- **Une revue qui n'est pas allée au bout n'est pas un succès.** Un relecteur qui a
  planté, dépassé son délai ou renvoyé quelque chose d'illisible laisse la tâche en
  `ERROR`, ce qui bloque la réponse finale exactement comme un échec.
- **Une revue réussie devient obsolète quand le code bouge sous elle.** Modifiez un
  fichier après une revue passée et la réponse suivante en exige une nouvelle — ce qui
  est passé n'est pas ce qui serait livré.
- **L'étape de revue du plan ne peut pas être cochée à l'avance.** Marquer comme terminée
  une étape dont le titre nomme la revue est refusé tant que la revue n'est pas passée.
- **Un relecteur ne peut pas relire son propre travail.** `review` est refusé à tout agent
  d'arrière-plan, relecteur compris, et le verbe n'est documenté que dans l'invite de
  l'agent principal.
- **Le relecteur ne peut pas être lancé pendant que des agents secondaires travaillent.**
  Ils écriraient dans l'arbre qu'il est en train de lire, et la revue porterait sur un
  état qui n'a jamais existé.

### Quand cela se produit

Une revue est requise lorsque le runtime a constaté les **deux** moitiés d'un travail
conséquent : un plan d'au moins trois étapes, et au moins un fichier réellement écrit. Ni
l'un ni l'autre ne compte seul — un long plan qui n'a rien changé était une étude, et un
correctif d'une ligne sans plan était un service rendu. Ce sont deux faits observés par
TMT plutôt que des affirmations du modèle : un modèle ne peut donc pas décrire son travail
comme petit pour éviter une revue.

Vous passez outre dans les deux sens avec vos propres mots. « …and review the changes »
en déclenche une ; « no review needed » la désactive. Ne rien dire laisse la décision aux
preuves, ce qui est le cas habituel.

### Limites

Il y a **trois cycles de revue par tâche**. Si le troisième signale encore des problèmes
bloquants, la réponse est libérée plutôt que retenue indéfiniment — la retenir davantage
consommerait le tour et se terminerait sans aucune réponse. Elle part accompagnée d'une
ligne disant clairement que la revue n'est pas passée et combien de constats restent
ouverts, et l'agent principal est tenu de dire la même chose avec ses propres mots. Le
silence serait le pire des échecs.

### À l'écran

La revue se place sous le plan dans la même colonne de droite, sur trois lignes au plus :

```
PLAN 2/5                       PLAN 5/5
S1 + Inspect repository        S1 + Inspect repository
S2 + Implement feature         S2 + Implement feature
S3 > Add tests                 S3 + Add tests
S4 - Independent review        S4 + Independent review
S5 - Final verification        S5 + Final verification

REVIEW 1/3                     REVIEW 2/3
> Running independent review   + Review passed
```

Chaque état porte une marque en plus d'une couleur, si bien que la colonne se lit avec les
séquences d'échappement supprimées et sur un terminal totalement dépourvu de couleur.
`/review` affiche les constats en entier, et c'est la voie d'accès sur une fenêtre trop
étroite pour deux colonnes.

## Contexte de projet : `TMT_Context/`

Autrefois, une session partait de rien. Tout ce qui avait été élucidé à propos d'un
projet — où se trouve le point d'entrée, quelle commande lance les tests, ce qui a été
implémenté la semaine dernière et ce qui a été laissé à moitié fait — était reconstruit de
zéro à chaque lancement, ou perdu à la fin du processus.

TMT conserve désormais deux fichiers markdown dans le projet sur lequel il travaille :

```text
my-project/
├── src/
├── tests/
├── package.json
└── TMT_Context/
    ├── notes.md      how this project works
    └── progress.md   what has been done, what is being done, what remains
```

Ce sont des fichiers ordinaires. Ouvrez-les, lisez-les, modifiez-les, commitez-les.

### Quand il est créé

À la **première vraie tâche** dans un projet — pas au lancement de TMT, et pas quand vous
tapez une commande slash. Lancer TMT puis changer d'avis ne laisse rien derrière.

```text
tmtcode
   ↓
"Add a dark mode to this application."
   ↓
no TMT_Context → create it, inspect the project, record what was found
   ↓
start working
```

Si `TMT_Context/` est déjà là, il n'est **pas** recréé. Si l'un des fichiers est déjà là,
il n'est **pas** écrasé — ni fusionné, ni régénéré, ni touché d'aucune façon.

### `notes.md` — comment le projet fonctionne

Écrit d'abord à partir d'une inspection réelle du dépôt, puis corrigé et complété à mesure
que TMT en apprend davantage. Il comporte des titres stables afin que vous comme TMT
puissiez y retrouver les choses :

```text
# TMT Project Notes
## Project Overview      ## Dependencies
## Architecture          ## Constraints
## Important Files       ## Known Issues
## Build                 ## TMT Notes
## Testing
## Configuration
```

L'inspection initiale est délibérément superficielle — manifestes, fichiers marqueurs,
scripts de paquet, cibles de `Makefile`, sections `[tool.X]`, configuration d'intégration
continue, premier paragraphe du README, répertoires de premier niveau, et tout point
d'entrée qu'un manifeste *déclare*. Elle lit des fichiers et n'exécute rien, si bien
qu'elle coûte un instant plutôt qu'un parcours complet d'un gros dépôt.

**Rien n'y est inventé.** Là où un fait n'a pas pu être établi, le fichier le dit :

```text
## Build

Build command has not yet been confirmed. Nothing in this repository
names one that TMT recognised.
```

C'est toute la règle. Une commande de compilation devinée est pire qu'une section vide,
parce qu'elle vous coûte une commande ratée et votre confiance dans toutes les autres
lignes.

### `progress.md` — ce qui s'est passé

```text
# TMT Project Progress
## Current Status        ## Verification
## Completed             ## Important Decisions
## Currently Working On  ## Known Issues
## Remaining             ## Next Steps
## Tests
```

**Une tâche n'est enregistrée comme terminée que lorsqu'il y a une preuve** — un plan dont
toutes les étapes sont achevées, ou des fichiers réellement écrits. Une tâche qui s'est
terminée sans rien faire reste sous *Currently Working On* comme un point ouvert.
L'intention de TMT d'implémenter quelque chose ne devient jamais une coche.

**Les résultats de tests ne sont jamais que réels.** La section Tests est écrite à partir
d'un résultat de vérification construit sur des codes de sortie : elle dit donc
`39 passed, 3 failed` quand c'est ce qui s'est produit, et ne dit rien du tout quand rien
n'a été exécuté.

### Comment il est utilisé

Le contexte est placé dans l'invite du modèle **avant** l'instantané de l'espace de
travail, si bien que la première chose que TMT lit est ce qu'il avait déjà élucidé et la
seconde est le dépôt lui-même. C'est tout l'objectif : la deuxième tâche d'un projet
devrait être plus rapide que la première, parce que l'organisation n'a pas à être
redécouverte.

```text
"Now add keyboard shortcuts."
   ↓
load TMT_Context → already knows the architecture, the test command,
                   what dark mode did, what is outstanding
   ↓
search only where it still needs to
```

Il est budgété plutôt que collé en entier. Les petits fichiers y entrent complets ; les
gros gardent les sections qui comptent le plus et **disent lesquelles ont été laissées de
côté**, afin qu'on n'enseigne jamais au modèle qu'une section qu'il ne voit pas n'existe
pas.

### Le contexte ne prime jamais sur le code

`TMT_Context/` est une mémoire, pas une vérité. Si `notes.md` dit que l'authentification
se trouve dans `auth.py` et que le dépôt l'a déplacée vers `authentication/service.py`,
**c'est le dépôt qui a raison**.

TMT confronte les chemins nommés dans les notes aux chemins qui existent, et place ce
qu'il constate dans l'invite, à côté des notes elles-mêmes :

```text
STALE: these paths are named in the notes but are not in the workspace now:
src/main.py. Do not act on them without checking.
```

Il ne corrige pas la note à votre place — un fichier peut manquer parce que la note est
périmée ou parce que vous êtes en plein remaniement, et seule la lecture du code permet de
distinguer les deux. Ce qu'il fait, c'est empêcher qu'on croie la note, et la corriger une
fois qu'il en sait davantage.

### Vos modifications sont protégées

Vous pouvez réécrire l'un ou l'autre fichier à la main à tout moment. TMT change **une
section à la fois** et réécrit tout le reste des octets exactement comme il les a lus — y
compris des titres dont il n'a jamais entendu parler, votre prose et votre mise en page.
Il n'existe aucune opération qui remette un fichier entier, et aucun moyen d'en demander
une.

Si vous modifiez un fichier pendant que TMT travaille, votre modification survit : le
fichier est relu au moment du changement plutôt que repris d'une copie lue plus tôt.

### Aucun secret n'est jamais écrit

Pas de clés, pas de jetons, pas de mots de passe, aucune valeur issue d'un `.env`. TMT
enregistre l'exigence et non la valeur :

```text
## Configuration

Environment variables this project declares (names only -- values are
never recorded here), from `.env.example`:

- `API_KEY`
- `DATABASE_URL`
```

Le vrai `.env` n'est jamais ouvert — sa présence est notée à partir d'un `stat` et son
contenu n'est pas lu. Tout ce qui est écrit dans l'un ou l'autre fichier passe également
par le même filtre d'identifiants que `remember`, et tout ce qui a la forme d'un
identifiant est masqué et signalé.

### Avec `/plan`, `/review` et `/verify`

Le contexte ne fait pas doublon avec ces systèmes ; il retient ce qu'ils ont conclu.

| | va dans |
|---|---|
| les étapes du plan et leur statut réel | `progress.md`, *Currently Working On* |
| ce que la vérification a réellement exécuté, et ses chiffres | `progress.md`, *Tests* et *Verification* |
| les constats de revue bloquants encore ouverts | `progress.md`, *Known Issues* |

L'état final est persisté **après** que les verrous d'achèvement ont donné leur accord et
avant que la réponse ne parte, de sorte que l'ordre reste :

```text
work → verify → review → persist → end
```

La persistance ne peut jamais libérer une réponse que le plan, la vérification ou la revue
retenait — elle n'est sollicitée qu'une fois que les trois se sont déjà mis d'accord. Et
`send_message` ne finalise jamais rien : dire « j'ai implémenté la fonctionnalité » est
une phrase, pas une preuve.

### Par projet, toujours

Le chemin du contexte est déterminé à partir de l'espace de travail actif chaque fois
qu'on le demande, si bien que deux projets ne peuvent jamais en partager un et
qu'aucune information de l'un ne peut apparaître dans l'autre.

### Le désactiver

Settings → **Project Context** → Entrée. La valeur par défaut est **ON**.

Une fois désactivé, TMT ne crée, ne lit ni ne met à jour aucun `TMT_Context`. **Les
fichiers déjà écrits sont laissés exactement tels quels** — ils appartiennent au projet et
à celui qui les a écrits, et un réglage n'est pas un accord pour supprimer les notes de
quelqu'un.

L'interrupteur est stocké dans `.tmt_context` à côté des autres réglages par installation
de TMT : c'est donc l'état propre à TMT et il ne suit pas l'espace de travail. Les
*fichiers* de contexte sont la seule chose que TMT écrit délibérément dans votre projet.

### S'il ne peut pas être créé

Une copie en lecture seule, un problème de permissions, un disque plein : TMT le dit une
fois et poursuit votre tâche.

```text
Persistent project context could not be created (PermissionError: ...).
Continuing without TMT_Context.
```

Rien dans cette fonctionnalité ne mérite de faire échouer une tâche.

### Faut-il le commiter ?

C'est à vous de voir. TMT ne touche pas à votre `.gitignore`. Par défaut, les deux
fichiers sont traités comme de la documentation de projet ordinaire — ils sont lisibles,
comparables par diff et utiles à tous ceux qui travaillent sur le dépôt —, si bien que les
commiter partage avec le reste de l'équipe ce que TMT a appris. Ignorez-les si vous
préférez qu'ils restent locaux.

## Agents en arrière-plan

TMT sait déléguer. L'agent principal lance des agents secondaires en arrière-plan, ceux-ci
font du vrai travail au moyen des mêmes actions et des mêmes modèles que lui, et il les
attend et rapporte ce qu'ils ont fait.

```
Task> spawn three agents to write multiply.py, divide.py and power.py, then wait for them
```

| | S'exécute dans | Peut modifier | Peut pousser | Vous parle | Se termine par |
|---|---|---|---|---|---|
| agent principal | la boucle de session | oui | oui | oui | `end_conversation` |
| agent secondaire | un fil d'arrière-plan | oui | **non** | non | `internal_response` |
| agent de note | un fil d'arrière-plan | **non** | non | la réponse seulement | `internal_response` |

**Dix agents secondaires à la fois.** L'agent principal ne compte pas dans ce total, et
l'agent de note non plus. Une onzième demande est refusée par une phrase qui le dit, et
non ignorée.

`/agents` affiche ce qu'ils font. Dans un vrai terminal, la flèche droite en fin de ligne
vide ouvre la même chose sous forme de panneau vivant, et la flèche gauche le ferme.

### Le contrat de délégation

Une délégation est un contrat, pas un souhait. `spawn_agent` accepte un objet
`constraints` facultatif qui dit ce que cet agent a le droit de faire, combien de temps il
peut s'exécuter et ce qu'il doit rapporter — et **TMT fait respecter les trois lui-même.**
Son contrat est communiqué à l'agent, et il lui est aussi refusé au niveau du répartiteur,
de sorte qu'il ne peut contourner aucune de ces clauses en choisissant un autre outil.

```json
{"action": "spawn_agent",
 "task": "Investigate how authentication is put together in this repository.",
 "constraints": {
     "read_only": true,
     "timeout_seconds": 600,
     "report": {"file_list": true, "diff": true, "summary": true}
 }}
```

Chaque partie en est facultative, et **un `spawn_agent` sans `constraints` se comporte
exactement comme avant** — même invite, octet pour octet, mêmes permissions, même rapport.
Rien n'a changé pour une délégation existante.

Les contraintes sont **propres à chaque délégation**. L'agent secondaire nº 1 peut être en
lecture seule avec cinq minutes pendant que le nº 2 écrit librement avec quinze ; aucun
des deux ne peut voir ni affecter le contrat de l'autre, parce qu'un contrat est un objet
immuable rattaché à un seul enregistrement et qu'il n'y a aucune variable globale sur ce
chemin.

#### `read_only`

`read_only: true` signifie que l'agent peut inspecter cet espace de travail et n'a pas le
droit de le modifier. Il conserve tous les verbes de lecture — `read_file`, `read_lines`,
`list_files`, `glob`, `grep`, `find_symbol`, `tree`, `code_map`,
`related_tests`, `recall`, `git_status`, `git_diff`, `git_identity` — et tout le reste lui
est refusé.

**Appliqué au moment de l'exécution, et non demandé dans l'invite.** Le refus intervient
avant que l'action ne s'exécute, à deux endroits : `agent_worker` contrôle chaque action
avant qu'elle ne soit distribuée, aussi bien sur le chemin d'une action isolée que sur
celui d'un lot, et `agent_actions.execute_action` contrôle de nouveau au niveau du
répartiteur. Les deux interrogent la même fonction, donc il y a une seule règle et deux
endroits qui l'appliquent.

**C'est une liste blanche, pas une liste de verbes interdits.** Toute action ajoutée à TMT
après l'écriture de cette liste est refusée par défaut. Une liste de verbes interdits
admettrait silencieusement le prochain verbe de mutation que quelqu'un enregistrera, et
celui qui l'ajoute n'est pas celui qui a écrit la liste.

Cela couvre les chemins qui ne sont pas manifestement des écritures de fichier :

| Refusé | Parce que |
|---|---|
| `write_file`, `append_file`, `write_files`, `patch_file`, `replace_lines`, `replace_across`, `copy_file`, `rename_file`, `create_folder`, `delete_file`, `delete_folder` | ils modifient des fichiers |
| `run_file` / `run_python` | un programme peut écrire n'importe quoi, donc en exécuter un est un chemin de mutation |
| `git_commit` | commiter modifie le dépôt |
| `open_app` | il lance une application hors de l'espace de travail |
| `remember` | il écrit dans la mémoire propre à TMT |
| `git_push`, `plan`, `review`, `verify`, `project_context` | déjà refusés à tout agent d'arrière-plan, contrat ou pas |

**TMT n'a pas de verbe shell général**, ce qui explique qu'il n'y ait ici aucune liste
d'autorisation de commandes « sûres ». La seule façon pour un agent secondaire d'exécuter
du code arbitraire est `run_file`, et une délégation en lecture seule se le voit refuser
purement et simplement — aucune analyse de chaînes de commande, aucune supposition sur le
fait que `sed -i` écrit ou non. C'est la version honnête de la garantie : elle repose sur
le fait qu'il existe un seul chemin d'exécution et qu'il est fermé, plutôt que sur la
capacité de TMT à distinguer une commande qui modifie d'une commande inoffensive.

**Ce que cela ne prétend pas être.** Une délégation en lecture seule ne peut effectuer
aucun changement persistant par un verbe que TMT lui propose. Ce n'est pas un bac à sable :
TMT n'empêche pas les écritures au niveau du système d'exploitation, et si une action
future en ouvrait une, il faudrait l'ajouter délibérément à la liste blanche avant qu'un
agent en lecture seule puisse l'atteindre.

Une tentative refusée est **signalée, pas dissimulée**. L'agent est informé de ce qui a été
bloqué et pourquoi, afin qu'il puisse s'adapter et poursuivre — une écriture bloquée n'est
pas automatiquement une délégation ratée — et la tentative est enregistrée et parvient à
l'agent principal dans le résultat :

```
Constraint violations: 1 write operation blocked (write_file src/auth.py)
```

#### `timeout_seconds`

Un nombre entier de 1 à 3600. C'est la durée d'exécution maximale de **toute la
délégation**, et non d'une action, et elle n'est pas remise à zéro par la fin d'une action
ni par la réponse du modèle.

Le chronomètre démarre quand l'agent démarre réellement, pas quand il est enregistré, si
bien qu'une délégation ne perd jamais une partie de son temps parce qu'autre chose est
lent.

**Appliqué par le runtime.** L'échéance est contrôlée aux trois mêmes frontières que
l'annulation : en tête de chaque étape, entre les fragments d'une réponse en flux, et sur
la ligne qui précède immédiatement la distribution de chaque action. Quand elle est
dépassée :

- aucune action supplémentaire ne s'exécute ;
- le statut de l'agent devient `timed_out` — ce qui **n'est pas** `failed` ni `killed` ;
- son emplacement d'agent secondaire est libéré aussitôt, si bien qu'une délégation qui
  attendait de la capacité peut démarrer ;
- tout ce qu'il avait fait est conservé, et tout rapport qu'il devait est quand même
  collecté.

La garantie est exactement celle que porte `kill` et pas davantage : **après l'échéance,
aucun appel d'outil supplémentaire n'est distribué.** Une requête déjà en vol finit
d'arriver, parce qu'un fil Python ne peut pas être arrêté et qu'une réponse en flux n'a
pas de primitive d'abandon. Prétendre à une interruption instantanée serait un mensonge
là où le mensonge coûte cher.

Il n'y a pas de fil minuteur. L'échéance est un calcul sur l'enregistrement, balayé chaque
fois que la réponse pourrait compter — avant chaque contrôle de capacité, à chaque
repeinte, et à l'intérieur de chaque attente, qui ne bloque jamais au-delà de l'échéance la
plus proche. C'est la même conception que celle de la rétention de cinq secondes des
cartes, et pour les mêmes raisons : rien à annuler, rien à fuir, et un test qui la pilote
en avançant un nombre au lieu d'attendre dix minutes.

Les délais invalides sont refusés avant que quoi que ce soit ne démarre : un délai négatif,
un zéro, une chaîne, un `true`, une fraction de seconde, ou quoi que ce soit au-delà du
plafond d'une heure. **Un contrat refusé ne lance aucun agent** — une délégation qui
s'exécuterait sous un demi-contrat est la seule issue dont personne ne peut rendre compte.

#### `report`

`file_list`, `diff` et `summary`, chacun indépendamment. Ce ne sont **pas des permissions**
et ils n'affectent jamais ce que l'agent a le droit de faire.

- **`file_list`** — les fichiers que les propres actions de l'agent ont réellement lus et
  écrits, pris dans les requêtes que ces actions portaient. Jamais assemblé à partir de ce
  que l'agent a dit avoir lu.
- **`diff`** — ce que git dit des fichiers que cet agent a écrits. Limité à ces fichiers
  délibérément : l'agent principal continue de travailler pendant qu'un agent secondaire
  tourne et plusieurs peuvent tourner en même temps, si bien que le diff de tout l'arbre
  n'est absolument pas le travail d'une seule délégation. Le diff d'une délégation en
  lecture seule dit `No changes permitted by delegation.` Celui d'une délégation
  autorisée à écrire qui n'a rien changé dit `No workspace changes.`
- **`summary`** — le compte rendu du travail par l'agent lui-même, c'est-à-dire la
  `response` de son `internal_response` et la seule partie du rapport qui soit les mots du
  modèle.

Les rapports sont collectés à **chaque** fin, et pas seulement en cas de succès : une
délégation dépassée en temps ou annulée possède quand même une vraie liste de fichiers, un
vrai diff et un vrai chronométrage, et jeter cela au motif qu'elle ne s'est pas terminée
normalement reviendrait à supprimer la seule trace de ce qu'elle a réussi à faire.

Ce qui revient à l'agent principal est structuré et concis — pas de transcriptions
d'appels d'outils, pas de journaux bruts :

```
Background agent #4
STATUS: TIMED OUT
Contract: READ ONLY  TIMEOUT 10:00  FILES  SUMMARY
Runtime: 10:00 of 10:00
Progress: 17 actions taken, 11 files inspected, 0 files changed

SUMMARY
  Found the authentication entry point in AuthService; three test modules cover it.

FILES
  Inspected (11):
    src/auth/service.py
    src/auth/token.py
    ...
  Changed: none
```

#### À l'écran

Le compteur à côté de l'invite affiche `4/10 agents`, l'en-tête du panneau affiche
`AGENTS 4/10`, et la carte d'un agent sous contrat porte celui-ci de façon compacte :

```
██░░░░░░ #3  RO  8:32/10:00  +0 -0  ~4k out  1m28s  running
```

`RO` signifie lecture seule, la paire est le temps restant rapporté à la limite — un vrai
compte à rebours issu du même calcul qui arrêtera effectivement le travail — et `F D S`
sur la carte marque les exigences de rapport. `/agents` dit tout cela en entier, là où il y
a la largeur pour le lire. Un agent dont le délai est dépassé affiche `timeout` et est
coloré comme un arrêt plutôt que comme un échec, parce que c'en est un.

#### Délégation imbriquée

Les agents d'arrière-plan ne peuvent pas lancer d'agents à eux — leur contexte d'action ne
porte aucun registre —, si bien qu'une délégation en lecture seule n'a aucun moyen
d'atteindre une délégation autorisée à écrire. C'était vrai avant l'existence des contrats
et cela n'a pas changé ; rien ici n'invente une règle d'agents imbriqués pour quelque chose
qui ne peut pas se produire.

### Les regarder travailler

Pendant que des agents tournent, chacun obtient une ligne à lui, directement sous la barre
de progression principale :

```
██████████  60% Working                      <- the main agent, in colour
██░░░░░░ #1  +45 -3  4k out  47s  running    <- one row per agent, in grey
██████░░ #2  +0 -0  ~900 out  1m34s  running
████████ #3  +7 -120  ~15k out  2m21s  done
```

Chaque ligne porte le numéro de l'agent, les lignes qu'il a ajoutées et supprimées, les
jetons qu'il a produits, depuis combien de temps il travaille et son état. Tout ce qui s'y
trouve est mesuré plutôt qu'estimé, sauf là où un chiffre est marqué `~` — cela signifie
que le fournisseur ne l'a pas rapporté et que TMT l'a calculé à partir du texte, et c'est
marqué partout où cela se produit.

**Les barres des agents sont grises et la barre principale est en couleur, et c'est là
toute la raison d'être de la différence.** Le dégradé de couleur signifie « l'agent
principal travaille, et voici où il en est ». Cinq barres colorées se liraient d'un coup
d'œil comme un seul processus rapporté cinq fois. Les agents reçoivent l'absence de
couleur plutôt qu'une couleur à eux.

**La barre d'un agent montre la part de son budget d'étapes qu'il a dépensée, pas à quel
point il est près d'avoir fini.** Rien ne peut connaître la seconde information — une
barre qui la sous-entendrait inventerait le seul chiffre que personne ne possède. La barre
d'un agent terminé est pleine parce qu'il est terminé, et c'est le seul moment où
l'achèvement est effectivement connu.

La ligne et la carte d'un agent terminé restent cinq secondes puis disparaissent. Son
résultat, lui, ne disparaît pas : l'agent principal peut encore le demander longtemps
après.

Le compteur au-dessus de la zone de saisie intègre le travail des agents aux totaux de la
session :

```
+55 lines, -5 lines, ~12k context, 433 out, agents ~22k tokens
```

Les lignes comprennent tout ce que les agents ont écrit — une ligne écrite par un agent
secondaire est une ligne écrite par la session, et un compteur affichant `+0` pendant que
cinq agents réécrivent le projet dirait la vérité sur un fil et un mensonge sur la session.
La dépense en jetons des agents est rapportée séparément de `context`, parce que ce
dernier indique à quel point la fenêtre de la requête en vol est pleine, et y ajouter cinq
agents décrirait un contexte qui n'existe pas.

### `/note` — interroger l'espace de travail sans rien déranger

```
Task> /note which module owns the prompt box?
```

Un agent en lecture seule répond à partir de l'espace de travail pendant que tout le reste
continue. Il peut chercher, lire et inspecter la structure ; il ne peut ni créer, ni
modifier, ni supprimer, ni pousser, et cela est imposé par une liste blanche contrôlée
avant chaque action plutôt qu'en le lui demandant gentiment.

La question tient sur la même ligne. Cette forme fonctionne partout, y compris dans une
exécution redirigée — le lecteur de flux prend une tâche par ligne, donc une invite en
deux temps est tout bonnement inaccessible depuis un tube. Dans un vrai terminal, un
`/note` seul demandera la question séparément.

### Ce que les agents d'arrière-plan ne peuvent délibérément pas faire

Ce sont des limites de la conception, pas des choses laissées en plan :

- **Un agent secondaire ne peut pas pousser.** Il peut lire `git status`, `diff`, `log` et
  `branch`, et il peut commiter ; atteindre un dépôt distant reste l'affaire de l'agent
  principal, qui a besoin de vos propres mots dans la tâche pour le faire.
- **Un agent secondaire ne peut pas supprimer un fichier ni un dossier.** Les deux
  attendent la confirmation d'un humain au terminal, et un fil d'arrière-plan n'a pas de
  terminal où poser la question. L'agent secondaire signale le chemin à la place et l'agent
  principal s'en charge.
- **Un agent secondaire ne peut pas exécuter la suite de tests.** `run_file` abandonne au
  bout de 10 secondes et une vraie suite prend plus longtemps, si bien qu'un agent à qui
  l'on demande de vérifier les tests dit qu'il n'a pas pu et ce qu'il a fait à la place. Il
  ne rapportera pas un résultat qu'il n'a jamais vu.
- **« Tuer » est coopératif, pas instantané, et un dépassement de délai aussi.** Python ne
  peut pas arrêter un fil de force. Ce qui est garanti, et ce qui est testé, c'est
  qu'**aucun appel d'outil supplémentaire ne s'exécute une fois qu'un agent est tué ou a
  dépassé son échéance** — l'annulation prend effet au fragment suivant ou à la frontière
  d'action suivante. Un agent bloqué sur une connexion figée est marqué comme tué et
  abandonné ; son fil est un démon et ne peut jamais maintenir TMT ouvert.
- **Il n'y a pas de file d'attente.** Dix agents secondaires est un plafond strict et la
  onzième demande est refusée par une phrase, pas mise en attente. TMT n'a pas
  d'ordonnanceur avec lequel s'intégrer et en construire un pour cela serait bien plus
  lourd que ce que ce plafond exige ; le refus nomme le plafond et dit quoi faire, ce qui
  est ce sur quoi l'agent principal agit.
- **Attendre bloque l'agent principal.** C'est une action ordinaire, pas une mise en
  veille. L'interface reste vivante pendant l'attente parce que la zone vivante se repeint
  sur son propre fil, et Ctrl-C vous ramène à l'invite.
- **Les agents secondaires ne coordonnent pas leurs écritures.** Toute écriture isolée est
  atomique, et si deux agents touchent au même fichier, l'agent principal est informé
  desquels il s'agit. Il n'y a aucun verrouillage au-delà de cela : donnez donc des
  fichiers séparés à des agents concurrents.
- **Vous ne voyez jamais les actions propres à un agent secondaire.** L'interface montre
  une barre et une étiquette courte pour chacun, pas les lectures et les modifications
  qu'il effectue. Ce qu'il a fait revient dans le résumé de l'agent principal, et c'est
  pourquoi il est demandé à celui-ci de dire ce qu'il a délégué.
- **Une carte n'affiche pas de temps écoulé ; la ligne sous la barre de progression, si.**
  Le panneau ne se repeint que lorsque son contenu change, et une durée dessinée là
  deviendrait obsolète ou forcerait une repeinte à chaque tic, ce qui est ce qui faisait
  autrefois scintiller le curseur.

### Ce que les agents coûtent

Chaque agent secondaire porte sa propre invite système à chaque requête, parce que l'API
est sans état. Cette invite pèse environ 14 000 jetons estimés contre 19 000 pour l'agent
principal : elle porte un `tree` du projet plutôt que le contenu des fichiers que l'invite
principale inclut, ce qui économise à peu près 1 500 jetons par requête. Dix agents
secondaires en portent chacun une copie : la délégation n'est donc pas gratuite — elle
achète du parallélisme avec des jetons, et faire passer le plafond de cinq à dix a doublé
la quantité que peut en acheter une session à un instant donné. Déléguez du travail
réellement séparable, pas du travail que vous pourriez faire vous-même en deux étapes.

Un contrat ajoute quelques centaines de jetons à l'agent qui en porte un, et rien du tout à
celui qui n'en porte pas : l'invite d'une délégation sans contraintes est, octet pour
octet, celle qui existait avant les contrats.

## Interface

Pendant qu'une tâche s'exécute : une animation THINKING jusqu'à la première sortie, puis
une barre de progression, le temps écoulé et un compteur de jetons en direct. Le texte du
modèle est révélé caractère par caractère à mesure qu'il arrive. La réponse finale est
encadrée. Un décompte des agents en cours d'exécution apparaît à côté du compteur dès
qu'il y en a.

**Le panneau des agents est une colonne au pied de l'écran, pas une barre latérale sur
toute la hauteur.** Il partage la zone vivante avec la réponse et la zone de saisie ; la
conversation au-dessus conserve toute la largeur et n'est jamais redessinée. C'est une
limite délibérée plutôt qu'un travail inachevé : le défilement arrière est le seul
enregistrement permanent qu'a TMT d'une session, et les deux séquences d'échappement qui
permettraient à un programme de s'approprier toute la fenêtre — restreindre la région de
défilement et le tampon d'écran alternatif — le détruisent. Les lignes qui sortent d'une
région restreinte sont jetées plutôt que conservées, si bien que remonter cesserait
d'atteindre l'historique de la session. Un test parcourt les modules pour empêcher l'une
ou l'autre de revenir.

Sur un terminal de moins de 45 colonnes, le panneau prend toute la largeur de la zone
vivante et la zone de saisie n'est pas dessinée tant qu'il est ouvert ; en dessous de 30
colonnes, il refuse de s'ouvrir et dit pourquoi. Les cartes abandonnent leur ligne
d'activité avant leur ligne de jetons, et tronquent au lieu de renvoyer à la ligne.

### Taper pendant qu'il travaille

La zone de saisie reste vivante pendant toute la durée d'un tour. Vous pouvez écrire la
question suivante pendant que l'agent travaille encore sur la précédente, touches d'édition
comprises.

**Entrée met la ligne en file d'attente au lieu d'interrompre.** Elle reçoit sa réponse dès
que la tâche en cours se termine, et les lignes sont traitées dans l'ordre où vous les avez
saisies — vous pouvez donc empiler trois questions de suite et vous éloigner. La zone de
saisie indique combien sont en attente.

`/note` peut y être tapé aussi, et c'est tout l'intérêt : il répond à partir de l'espace de
travail sans déranger le travail en cours.

Ctrl-C arrête toujours la tâche en cours, exactement comme avant.

Cela nécessite un vrai terminal. Une exécution redirigée lit une tâche par ligne et la zone
de saisie est inerte, ce qui est le cas de toute exécution scriptée et de la suite de
tests.

Définissez `TMT_STREAM=0` pour désactiver le flux. Le flux nécessite également
`requests` ; sans lui, TMT fonctionne sans flux.

## Commandes slash

À l'invite, une ligne qui **n'est rien d'autre** qu'une commande `/` reçoit une réponse de
TMT lui-même et n'est jamais envoyée au modèle. Les noms sont insensibles à la casse. Tout
le reste est une tâche et part au modèle exactement comme avant — y compris une ligne qui
commence simplement par un chemin, comme `/usr/bin/python is broken`.

`/plan`, `/review` et `/verify` sont les trois qui se lisent des deux façons. Seule sur sa
ligne, chacune est le rapport en lecture seule décrit ci-dessous ; suivie d'une tâche —
`/plan Build the login page` — la ligne est cette tâche, avec la capacité activée pour
elle. Voir [Capacités](#capacités--plan-review-verify).

| Commande | Ce qu'elle fait |
|---|---|
| `/context` | la conversation jusqu'ici : modèle, fournisseur, espace de travail, combien de tours sont repris dans la requête suivante, jetons estimés en entrée et en sortie, lignes ajoutées et supprimées, et les dernières questions |
| `/config` | les réglages sous lesquels une requête s'exécute : modèle, fournisseur, effort, flux, mode JSON, espace de travail, et si une clé API est définie |
| `/clear` | oublier la conversation et repartir de zéro. Le modèle, l'effort, l'espace de travail et tous les autres réglages sont conservés, et aucun fichier n'est touché |
| `/effort` | afficher le niveau d'effort courant |
| `/effort low\|medium\|high` | le définir |
| `/model` | afficher le modèle courant et ceux que ce fournisseur propose |
| `/model <name>` | passer à l'un d'eux, par identifiant ou par le nom affiché dans Settings |
| `/note <question>` | répondre à une question sur l'espace de travail, sans rien changer |
| `/notes` | ce dont TMT se souvient de ce projet entre les sessions : où se trouve `TMT_Context/`, ce que contient chaque fichier, et quelles notes nomment des chemins qui n'existent plus |
| `/agents` | ce que font les agents d'arrière-plan |
| `/back` | sortir vers le menu de démarrage, en gardant la session. Voir ci-dessous |
| `/plan` | les étapes que TMT déroule pour cette tâche, et ce qu'il reste |
| `/verify` | ce qui a réellement été exécuté pour contrôler le travail de cette tâche : chaque contrôle, sa commande, son code de sortie, et la sortie de tout ce qui a échoué |
| `/review` | ce que la revue indépendante a trouvé : le verdict, chaque constat, et l'historique des revues de cette tâche |

### `/back` — le menu, sans perdre la session

`/back` remet le menu de démarrage à l'écran par-dessus une session qui tourne toujours.
Rien n'est terminé, effacé, annulé ni attendu : la conversation est toujours la
conversation, le plan est toujours le plan, et les agents d'arrière-plan continuent de
travailler derrière. Avant cela, Settings et Help n'étaient accessibles qu'en quittant.

Le menu qu'il ouvre est le menu de lancement avec trois différences :

```
> Resume    Go back to the session, which is still here
  Settings  Provider, API key and the model TMT runs on
  Help      What TMT does, and how to drive it
  Exit      Close TMT and end the session
```

- **Start devient Resume**, et son intitulé continue de parcourir le dégradé même quand le
  curseur est ailleurs — c'est l'écran qui vous dit que votre session est toujours là.
- **Exit annonce qu'il termine la session.** Le mot est le même qu'au lancement ; la
  conséquence, non.
- **Settings n'est pas proposé tant que quelque chose tourne encore.** La ligne
  disparaît — ni grisée, ni désactivée — et une ligne au-dessus de la liste dit ce qui
  tourne et quoi faire :

```
Settings are not offered while work is running: 2 agents. Wait for it to
finish, then /back again.
```

Le fournisseur, la clé et le modèle sont tous lus pendant qu'une requête est en vol :
changer l'un d'eux sous un agent qui tourne ferait donc atterrir un changement que
personne n'a demandé au milieu d'une requête déjà commencée. Lorsque le travail se
termine, la ligne revient à la trame suivante, sans fermer le menu.

Choisir Resume efface l'écran, redessine l'en-tête et vous ramène à l'invite, tout étant
comme vous l'aviez laissé.

**L'effort** est la quantité de travail que TMT consacrera à une tâche. Il change deux
choses, et uniquement des choses qui sont réelles chez tous les fournisseurs : la longueur
de réponse demandée, et le nombre de tours de la boucle d'agent qu'une question peut
prendre.

| Niveau | Longueur de réponse demandée | Tours par tâche |
|---|---|---|
| `low` | 4096 jetons | 12 |
| `medium` (par défaut) | 4096 jetons | 35 |
| `high` | 8192 jetons | 60 |

La longueur de réponse ne descend en dessous de 4096 à aucun niveau. Chaque réponse est un
objet JSON unique et celles qui comptent contiennent un fichier entier : une limite plus
basse ne rend donc pas le modèle plus concis — elle coupe l'objet au milieu d'une chaîne et
l'écriture n'a jamais lieu. Le réglage est stocké dans `.tmt_effort` à côté de
l'installation et survit à un redémarrage, comme le choix du modèle.

**La complétion.** Dans un vrai terminal, taper `/` liste les dix commandes sous la ligne
que vous êtes en train de taper, et la liste se resserre à mesure : `/mo` ne laisse que
`/model`. Tab complète jusqu'où les candidats sont d'accord — `/mo` devient `/model `,
`/co` devient `/con`, parce que `/context` et `/config` s'appliquent encore tous les deux.
Une exécution redirigée lit une ligne entière et ne dessine aucune liste ; les commandes
elles-mêmes y fonctionnent toujours.

**Les secrets.** Ni `/context` ni `/config` n'affiche jamais une clé, un jeton ou un mot de
passe. `/config` dit si une clé est définie et rien d'autre à son sujet — ni la valeur, ni
une forme masquée de celle-ci.

## Configuration

| Variable | Valeur par défaut |
|---|---|
| `OPENROUTER_API_KEY` | depuis `.tmt_key` |
| `OPENROUTER_MODEL` | `minimax/minimax-m3:free` |
| `TMT_STREAM` | `1` |
| effort | `medium`, depuis `.tmt_effort` ; défini avec `/effort` |
| contexte de projet | activé, depuis `.tmt_context` ; défini dans Settings. Voir [Contexte de projet](#contexte-de-projet--tmt_context) |
| `TMT_GIT_NAME` | `TMT code` |
| `TMT_GIT_EMAIL` | aucune — requise avant que TMT n'accepte de commiter |
| `TMT_GIT_ROOT` | le dépôt contenant le répertoire du projet |
| l'argument `PATH`, ou `--dir` | le répertoire courant |

## `tmtcode` n'est pas reconnu

La commande est installée, mais le répertoire dans lequel pip l'a placée n'est pas dans
votre PATH. Trouvez ce répertoire :

```bash
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

C'est `Scripts` sous Windows et `bin` sous macOS et Linux, à l'intérieur de
l'environnement Python ou virtuel dans lequel vous avez installé. Ajoutez-le au PATH, ou
utilisez l'une des deux solutions de repli — toutes deux prennent les mêmes arguments et
choisissent le répertoire du projet de la même façon :

```bash
python -m TMT                     # n'importe où, une fois installé
python /path/to/TMT/TMT.py        # n'importe où, directement depuis un clone
```

Si vous avez installé dans un environnement virtuel, `tmtcode` n'existe que tant que cet
environnement est actif.

`tmtcode --help` affiche les arguments.

## Tests

```bash
python run_tests.py
```

La suite vit dans `testing/`, répartie entre `testing/unit/` et `testing/integration/`. Le
lanceur reste à la racine et découvre les deux ; voir
[testing/README.md](../testing/README.md) pour savoir ce qui va où.

1581 tests. Huit d'entre eux lisent la clé API dans `.tmt_key` : sur un clone tout neuf sans
clé configurée, ces huit-là échouent et les autres passent.

Cela prend à peu près quinze minutes plutôt que les deux d'autrefois, et presque tout ce
temps tient à un seul test de `test_agent_review.py` qui lance trois vrais agents
relecteurs et attend un aller-retour d'API réel pour chacun. C'est aussi le seul test ici
qui ne soit pas déterministe : il établit une revue en échec puis envoie des objets bidon
pour prouver qu'aucun d'eux ne peut la transformer en succès — mais exécuter `review`
exécute une revue, si bien qu'un relecteur en direct à qui votre copie de travail plaît la
fait passer par la voie légitime et l'assertion se déclenche. Relancez-le avant de le
croire.

## Licence

Apache license 2. Voir [LICENSE](../LICENSE).
