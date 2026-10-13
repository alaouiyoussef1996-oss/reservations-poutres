# Réservations dans les poutres continues

## Télécharger / Download

**[⬇ Télécharger ReservationsPoutre.exe (Windows)](https://github.com/alaouiyoussef1996-oss/reservations-poutres/releases/latest/download/ReservationsPoutre.exe)**

Double-cliquer sur `ReservationsPoutre.exe` : aucune installation, Python non requis. Si Windows affiche « Windows a protégé votre ordinateur », cliquer sur « Informations complémentaires » puis « Exécuter quand même ».

Double-click `ReservationsPoutre.exe`: no installation, no Python needed. If Windows SmartScreen shows a warning, click "More info" then "Run anyway".

Manuel / Manual : [Livret_utilisation.pdf](https://github.com/alaouiyoussef1996-oss/reservations-poutres/raw/main/Livret_utilisation.pdf) · Toutes les versions / All releases : [Releases](https://github.com/alaouiyoussef1996-oss/reservations-poutres/releases)

Logiciel de bureau (PyQt6) : **on dessine la poutre continue, on saisit ses armatures longitudinales et transversales,
et le logiciel montre où l'on peut faire une réservation (trémie, gaine, fourreau) sans renfort.**

## Lancer
- Windows : double-clic sur `lancer.bat` (crée l'environnement, installe PyQt6 + numpy, lance l'appli).
- Ou : `pip install -r requirements.txt` puis `python app.py`.
- Un `.exe` autonome : `build_exe.bat` (PyInstaller).
- Tests du moteur : `python -m pytest tests`.

## Utilisation
1. **Poutre** (onglet de gauche) : méthode (Eurocode 2 ou BAEL 91), b, h, f_ck, f_yk, enrobage, charges g/q par travée.
   Les appuis se **déplacent à la souris** sur le dessin ; boutons « Ajouter / Supprimer une travée ».
2. **Armatures** : outils « Armatures inf. », « Armatures sup. » et « Étriers » → on trace à la souris sur la poutre,
   une boîte demande n, Ø, lit / Ø, espacement. Tout reste modifiable dans les tableaux.
   « Ferraillage de départ » propose un ferraillage cohérent avec les efforts (point de départ, à remplacer par le réel).
3. **Réservation** (barre du haut) : rectangulaire ou circulaire, longueur a, hauteur h, position verticale
   (auto = meilleure position, centrée, ou manuelle). La poutre est colorée :
   - **vert** : possible sans renfort particulier
   - **orange** : possible avec renforts (le panneau de droite donne les sections à ajouter)
   - **rouge** : impossible
   Un clic sur la poutre place la réservation et détaille le calcul (panneau de droite).
4. Sous la poutre : bande des zones, enveloppes M et V, et **longueur maximale** possible pour la hauteur choisie.
   Les silhouettes vertes/orange dans la poutre = hauteur maximale pour la longueur a, le long de la poutre.
5. Fichier : enregistrer/ouvrir (`.json`), export PNG, export de la **note de calcul HTML**.

## Méthode
- Efforts : poutre continue à section constante, méthode des rotations, charges réparties, combinaison ELU
  1,35 G + 1,5 Q avec toutes les dispositions de Q travée par travée (enveloppes). Poids propre 25 kN/m³ en option.
- **Vert (petites ouvertures, treillis de Mörsch)** : chapitres 5-6 du livre « Les grandes réservations dans les poutres » :
  épaisseur de bielle a = 2 V / (b σ_Rd,max) (σ_Rd,max = 0,85 ν' f_cd en EC2 ; 0,8 f_c28/γb en BAEL),
  h_w = h − 2(h − d) − 0,4 x_u − max(0,6 x_u ; a). Rectangle admis si a + h ≤ h_w (cercle : Ø ≤ 0,586 h_w),
  placé dans la fenêtre libre entre la zone comprimée et les aciers tendus. Armatures de suspension : V / f_yd
  comparée aux étriers dessinés dans la largeur du triangle.
- **Orange (grandes ouvertures, linteaux)** : méthode du BAEL (« Maîtrise du BAEL 91 », exercice 4) : couple F = M/z dans les
  deux linteaux, effort tranchant réparti selon les inerties, flexion composée des linteaux aux deux extrémités,
  suspension, cadres. Donne les aciers à ajouter. Adaptée à l'EC2 (f_cd, μ_lim = 0,372, V_Rd,c / V_Rd,max).
- **Rouge** : linteau < minimum, zone comprimée recoupée, armatures longitudinales coupées, ferraillage > 4 %,
  bielle d'effort tranchant écrasée, réservation trop près d'un appui (par défaut h depuis l'axe, réglable).

Contrôles faits sur les livres : M = 282 kN·m, V = 205 kN, h_w = 35,6 cm, a = 6,8 cm (chap. 6) ;
A' = 12,87 cm² et A = 13,79 cm² du linteau supérieur, A = 11,87 / 1,72 / 15,31 cm² du linteau inférieur (BAEL ex. 4).

## Limites (à connaître)
- ELU seulement : pas de vérification ELS (contraintes, fissuration) ni de flèche, pas de résistance au feu.
- Charges réparties uniformes, section rectangulaire, appuis simples (pas de console ni d'encastrement).
- La méthode des linteaux est celle du BAEL transposée à l'EC2 ; je n'ai pas eu sous les yeux les chapitres EC2 sur les
  grandes réservations (seuls les chap. 1-3, 5-6 ont été fournis) : à confronter avant tout usage en production.
- Les ancrages des barres au-delà de la réservation ne sont pas vérifiés ; seules les barres qui couvrent toute la
  réservation sont comptées comme existantes.
- L'espacement entre deux réservations (≥ 2 fois la hauteur) n'est pas contrôlé automatiquement : une réservation à la fois.
- Résultats indicatifs, à valider par l'ingénieur structure responsable.

## Version 2 – nouveautés
- Armatures longitudinales saisies **par travée** (position, longueur), étriers par zones et par travée.
- **Largeur d'appui** réglable ; distance mini mesurée depuis la face de l'appui.
- **Plusieurs réservations** stockées dans le projet (écart mini 2 × la plus grande hauteur).
- Dessin **à l'échelle réelle** avec zoom.
- Mode d'emploi illustré : `Livret_utilisation.pdf` (source : `docs/livret.html`).
- **Longueur de calcul = longueur de la poutre − appuis** : les appuis sont des discontinuités sans longueur dans le calcul ; leur largeur n'intervient que dans le dessin. Les abscisses sont comptées sans les appuis.
- Une ligne d'armatures accepte **deux diamètres** (n, Ø + n 2e groupe, Ø 2e groupe), ex. 4HA20+2HA16.
- **Poutre en T** : case à cocher + b_f / h_f (onglet Poutre). Table comprimée sous M>0, âme seule sous M<0 ; réservation dans la table refusée.

## Contact et téléchargements
Auteur : Youssef Alaoui - alaoui.youssef1996@gmail.com (modifiable dans `branding.py`, qui contient aussi les liens GitHub).
Menu **Aide** : manuel, téléchargement du code source et de l'exécutable Windows.
L'exécutable est construit automatiquement par GitHub Actions (`.github/workflows/build.yml`) : créer un tag `v2.0` (`git tag v2.0 && git push --tags`) publie `ReservationsPoutre-windows.zip` dans *Releases*. En local : `build_exe.bat`.

Le principe théorique du calcul est détaillé en annexe du manuel (`Livret_utilisation.pdf`).

## Explication du logiciel

**But.** Dessiner une poutre continue en béton armé, saisir ses armatures, et obtenir **les zones où l'on peut placer une réservation (trémie, fourreau) sans renfort**, celles où il faut des renforts (et lesquels), et celles où c'est impossible.

**Entrées.** Section (rectangulaire ou en T), matériaux, méthode (Eurocode 2 ou BAEL 91) ; longueur de calcul et charges de chaque travée ; largeur des appuis (simples discontinuités dans le calcul) ; armatures longitudinales par travée (position, longueur, 1 ou 2 diamètres par ligne) ; étriers par zones et par travée.

**Ce que fait le calcul.**
1. Efforts ELU de la poutre continue (méthode des rotations, toutes les dispositions de la charge variable) -> enveloppes M et V.
2. Pour une réservation d'essai (dimensions, position), flexion de la section pleine : x_u, z, effort dans les membrures F = M/z.
3. **Vert** : la réservation s'inscrit dans le treillis de Mörsch (h_w = h - marges) et les étriers suffisent.
4. **Orange** : méthode des linteaux (poutre découpée en deux linteaux) ; calcul des aciers et cadres à ajouter.
5. **Rouge** : règle non respectée (appui trop proche, autre réservation trop proche, barre coupée, zone comprimée coupée, linteau trop mince, bielle écrasée...).
6. Balayage tous les 5 cm : bande de verdict le long de la poutre + courbes de longueur/hauteur maximales.

**Utilisation.** Régler l'essai, cliquer dans le dessin (à l'échelle réelle) pour le déplacer, « Ajouter à la poutre » pour enregistrer plusieurs réservations ; chaque verdict détaille les efforts et les aciers à ajouter.

**Limites.** ELU uniquement, charges réparties, pas d'ancrage/ELS/flèche, linteaux = BAEL adapté EC2. Résultats indicatifs, à valider par l'ingénieur.
