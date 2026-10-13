"""
Moteur de calcul - réservations dans les poutres continues en béton armé.

Méthodes (au choix : Eurocode 2 ou BAEL 91) :
  * Petites ouvertures : l'ouverture s'inscrit dans le treillis de Mörsch
    (triangle rectangle isocèle de côté h_w) -> "possible sans renfort particulier"
    (seules les armatures de suspension / étriers existants sont vérifiées).
  * Grandes ouvertures : méthode des linteaux (membrure comprimée + membrure tendue,
    flexion composée des linteaux, répartition de l'effort tranchant) -> "possible
    avec renforts" (le logiciel donne les sections d'acier à ajouter) ou "impossible".

Unités internes : positions le long de la poutre en m ; sections en mm ;
forces en N ; moments en N.mm ; contraintes en MPa.
Entrées/sorties utilisateur : kN, kN.m, cm² (conversion faite à l'affichage).
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, field, asdict

import numpy as np

STATUS_OK, STATUS_REINF, STATUS_NO = 0, 1, 2
STATUS_LABEL = {
    STATUS_OK: "Possible sans renfort particulier",
    STATUS_REINF: "Possible avec renforts",
    STATUS_NO: "Impossible",
}
SQRT2 = math.sqrt(2.0)


# --------------------------------------------------------------------------------------
# Données de projet
# --------------------------------------------------------------------------------------
@dataclass
class LongBar:
    """Armature longitudinale rattachée à une travée : position + longueur."""
    face: str          # "top" (supérieur) ou "bot" (inférieur)
    span: int          # travée de référence (0 = première)
    start: float       # m, depuis l'axe de l'appui gauche de cette travée (peut être < 0 ou dépasser la travée)
    length: float      # m
    n: int             # nombre de barres
    dia: float         # mm
    row: int = 1       # lit (1 = le plus proche de la face)
    n2: int = 0        # 2e groupe de barres de la même ligne (diamètre différent), 0 = aucun
    dia2: float = 0.0  # mm

    @property
    def area(self):
        return self.n * math.pi * self.dia ** 2 / 4 + (self.n2 * math.pi * self.dia2 ** 2 / 4 if self.n2 > 0 else 0.0)

    @property
    def dmax(self):
        return max(self.dia, self.dia2 if self.n2 > 0 else 0.0)

    @property
    def nbars(self):
        return self.n + (self.n2 if self.n2 > 0 else 0)

    @property
    def label(self):
        s = f"{self.n}HA{self.dia:g}"
        if self.n2 > 0 and self.dia2 > 0:
            s += f"+{self.n2}HA{self.dia2:g}"
        return s


@dataclass
class StirrupZone:
    """Zone d'étriers rattachée à une travée : position + longueur."""
    span: int
    start: float       # m, depuis l'axe de l'appui gauche de la travée
    length: float      # m
    dia: float         # mm
    spacing: float     # mm
    legs: int = 2      # brins verticaux par cours


@dataclass
class Opening:
    """Réservation enregistrée dans le projet."""
    xc: float                  # abscisse du centre (m, depuis l'axe du premier appui)
    a: float                   # longueur (ou diamètre) mm
    ho: float                  # hauteur mm (ignorée pour un cercle)
    shape: str = "rect"        # rect | circle
    mode: str = "auto"         # auto | center | manual
    h1: float = 0.0            # mm sous la face supérieure (mode manual)
    name: str = ""

    @property
    def ho_eff(self):
        return self.a if self.shape == "circle" else self.ho


@dataclass
class AbsBar:
    face: str
    x0: float
    x1: float
    n: int
    dia: float
    row: int
    n2: int = 0
    dia2: float = 0.0

    @property
    def area(self):
        return self.n * math.pi * self.dia ** 2 / 4 + (self.n2 * math.pi * self.dia2 ** 2 / 4 if self.n2 > 0 else 0.0)

    @property
    def dmax(self):
        return max(self.dia, self.dia2 if self.n2 > 0 else 0.0)

    @property
    def nbars(self):
        return self.n + (self.n2 if self.n2 > 0 else 0)

    @property
    def label(self):
        s = f"{self.n}HA{self.dia:g}"
        if self.n2 > 0 and self.dia2 > 0:
            s += f"+{self.n2}HA{self.dia2:g}"
        return s


@dataclass
class AbsStir:
    x0: float
    x1: float
    dia: float
    spacing: float
    legs: int


@dataclass
class Project:
    spans: list = field(default_factory=lambda: [6.0, 5.0])   # portées entre axes d'appuis (m)
    support_w: list = field(default_factory=lambda: [300.0, 300.0, 300.0])   # largeur de chaque appui (mm)
    b: float = 300.0                  # largeur (mm)
    h: float = 600.0                  # hauteur (mm)
    bf: float = 0.0                   # largeur efficace de la table (poutre en T), 0 = section rectangulaire (mm)
    hf: float = 0.0                   # épaisseur de la table (mm)
    code: str = "EC2"                 # "EC2" ou "BAEL"
    fck: float = 25.0                 # MPa
    fyk: float = 500.0                # MPa
    cnom: float = 30.0                # enrobage nominal (mm)
    phi_t: float = 8.0                # diamètre d'étrier par défaut pour l'enrobage (mm)
    selfweight: bool = True           # ajoute le poids propre 25 kN/m3
    g: list = field(default_factory=lambda: [20.0, 20.0])    # permanentes hors poids propre (kN/m)
    q: list = field(default_factory=lambda: [10.0, 10.0])    # variables (kN/m)
    bars: list = field(default_factory=list)
    stirrups: list = field(default_factory=list)
    openings: list = field(default_factory=list)
    hlint_min: float = 125.0          # hauteur minimale d'un linteau (mm)
    dist_appui: float = 0.0           # distance mini réservation / FACE d'appui (mm) ; 0 -> h/2

    # ---- géométrie
    def xsup(self):
        out = [0.0]
        for L in self.spans:
            out.append(out[-1] + L)
        return out

    @property
    def is_T(self):
        return self.bf > self.b + 1e-6 and self.hf > 0

    def sync(self):
        """Met en cohérence les listes avec le nombre de travées."""
        n = len(self.spans)
        self.g = (list(self.g) + [self.g[-1] if self.g else 0.0] * n)[:n]
        self.q = (list(self.q) + [self.q[-1] if self.q else 0.0] * n)[:n]
        w = list(self.support_w) if self.support_w else [300.0]
        self.support_w = (w + [w[-1]] * (n + 1))[:n + 1]
        for br in self.bars:
            br.span = min(max(int(br.span), 0), n - 1)
        for z in self.stirrups:
            z.span = min(max(int(z.span), 0), n - 1)

    def _x0(self, span):
        xs = self.xsup()
        return xs[min(max(int(span), 0), len(self.spans) - 1)]

    def bar_x(self, br):
        x0 = self._x0(br.span) + br.start
        return x0, x0 + br.length

    def stir_x(self, z):
        x0 = self._x0(z.span) + z.start
        return x0, x0 + z.length

    def span_of(self, x):
        xs = self.xsup()
        for k in range(len(self.spans)):
            if x < xs[k + 1] - 1e-9:
                return k
        return len(self.spans) - 1

    # ---- (dé)sérialisation
    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        d = dict(d)
        spans = d.get("spans", [6.0, 5.0])
        xs = [0.0]
        for L in spans:
            xs.append(xs[-1] + L)

        def span_of(x):
            for k in range(len(spans)):
                if x < xs[k + 1] - 1e-9:
                    return k
            return len(spans) - 1

        bars = []
        for x in d.pop("bars", []):
            if "x0" in x:                                   # ancien format (positions absolues)
                k = span_of(x["x0"])
                bars.append(LongBar(x["face"], k, x["x0"] - xs[k], x["x1"] - x["x0"], x["n"], x["dia"], x.get("row", 1)))
            else:
                bars.append(LongBar(**x))
        stirrups = []
        for x in d.pop("stirrups", []):
            if "x0" in x:
                k = span_of(x["x0"])
                stirrups.append(StirrupZone(k, x["x0"] - xs[k], x["x1"] - x["x0"], x["dia"], x["spacing"], x.get("legs", 2)))
            else:
                stirrups.append(StirrupZone(**x))
        openings = [Opening(**x) for x in d.pop("openings", [])]
        p = Project(**d)
        p.bars, p.stirrups, p.openings = bars, stirrups, openings
        p.sync()
        return p

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @staticmethod
    def load(path):
        with open(path, "r", encoding="utf-8") as f:
            return Project.from_dict(json.load(f))


def default_project() -> Project:
    p = Project()
    p.bars = [
        LongBar("bot", 0, 0.0, 6.0, 4, 20, 1),
        LongBar("bot", 1, 0.0, 5.0, 4, 16, 1),
        LongBar("top", 0, 0.0, 11.0, 2, 12, 1),
        LongBar("top", 0, 4.5, 3.0, 4, 16, 1),
    ]
    p.stirrups = [
        StirrupZone(0, 0.0, 1.5, 8, 150, 2),
        StirrupZone(0, 1.5, 3.0, 8, 200, 2),
        StirrupZone(0, 4.5, 1.5, 8, 150, 2),
        StirrupZone(1, 0.0, 1.5, 8, 150, 2),
        StirrupZone(1, 1.5, 2.0, 8, 200, 2),
        StirrupZone(1, 3.5, 1.5, 8, 150, 2),
    ]
    p.openings = [Opening(2.8, 300.0, 200.0, "rect", "auto", 0.0, "R1")]
    p.sync()
    return p


# --------------------------------------------------------------------------------------
# Matériaux
# --------------------------------------------------------------------------------------
class Mat:
    def __init__(self, code: str, fck: float, fyk: float):
        self.code = code
        self.fck = fck
        self.fyk = fyk
        self.gc = 1.5
        self.gs = 1.15
        self.fyd = fyk / self.gs
        # EC2 (AN France) : alpha_cc = 1 ; BAEL : 0.85 fc28 / (theta gamma_b), theta = 1
        self.fc = fck / self.gc if code == "EC2" else 0.85 * fck / self.gc
        self.fctm = 0.30 * fck ** (2 / 3) if fck <= 50 else 2.12 * math.log(1 + (fck + 8) / 10)
        self.ft28 = 0.6 + 0.06 * fck
        self.nu1 = 0.6 * (1 - fck / 250.0)
        self.nup = 1 - fck / 250.0

    def mu_lim(self, gamma: float) -> float:
        if self.code == "EC2":
            return 0.372
        fc = min(self.fck, 30.0)
        g = min(max(gamma, 1.0), 1.6)
        return min(0.372, max(0.2, (3220 * g + 51 * fc - 3100) / 1e4))

    def sig_sc(self, gamma: float, delta: float) -> float:
        if self.code == "EC2":
            return self.fyd
        g = min(max(gamma, 1.0), 1.6)
        fc = min(self.fck, 30.0)
        s = 9 * g * fc - delta * (13 * fc + 415)
        return min(self.fyd, max(0.4 * self.fyd, s))

    def strut_limit(self) -> float:
        """Contrainte limite de la bielle (noeud tendu dans une direction)."""
        if self.code == "EC2":
            return 0.85 * self.nup * self.fc          # K2 * nu' * fcd  (= 12,8 MPa pour C25)
        return 0.8 * self.fck / self.gc                 # 0,8 fc28 / gamma_b (= 13,33 MPa)


# --------------------------------------------------------------------------------------
# Dimensionnement d'une section rectangulaire sous (N, M)
# --------------------------------------------------------------------------------------
def design_section(N, M, b, hh, dtop, dbot, mat: Mat, gamma=1.4, bf=0.0, hf=0.0):
    """N > 0 compression ; M > 0 : traction en partie basse (autour du centre de gravité).
    dtop / dbot : distance du centre de gravité des aciers à la face haute / basse.
    bf, hf : table de compression située en face haute (section en T) ; ignorée si M < 0 (table tendue).
    Retourne dict(A_top, A_bot [mm²], ok, mu)."""
    flipped = False
    if M < 0:
        M = -M
        dtop, dbot = dbot, dtop
        flipped = True
        bf = hf = 0.0
    r = _design_pos(N, M, b, hh, dtop, dbot, mat, gamma, bf, hf)
    if flipped:
        r["A_top"], r["A_bot"] = r["A_bot"], r["A_top"]
    return r


def _rect_flex(MA, b, d, dtop, mat, gamma):
    """Flexion simple d'une section rectangulaire : retourne (A_tendue_brute, A_comprimée, mu)."""
    fc, fyd = mat.fc, mat.fyd
    mu = MA / (b * d * d * fc)
    mul = mat.mu_lim(gamma)
    if mu <= mul:
        xi = 1.25 * (1 - math.sqrt(max(0.0, 1 - 2 * mu)))
        z = d * (1 - 0.4 * xi)
        return (MA / (z * fyd) if z > 0 else 0.0), 0.0, mu
    xil = 1.25 * (1 - math.sqrt(1 - 2 * mul))
    zl = d * (1 - 0.4 * xil)
    Mlim = mul * b * d * d * fc
    ssc = mat.sig_sc(gamma, dtop / d)
    Ac = (MA - Mlim) / ((d - dtop) * ssc)
    return Mlim / (zl * fyd) + Ac * ssc / fyd, Ac, mu


def _design_pos(N, M, b, hh, dtop, dbot, mat, gamma, bf=0.0, hf=0.0):
    fc, fyd = mat.fc, mat.fyd
    out = {"A_top": 0.0, "A_bot": 0.0, "ok": True, "mu": 0.0}
    d = hh - dbot
    if d <= 0 or d <= dtop:
        out["ok"] = False
        return out
    if N < 0:
        e0 = M / N if abs(N) > 1e-9 else 0.0       # <= 0 : N est sous le centre de gravité
        yb = -(hh / 2 - dbot)
        yt = hh / 2 - dtop
        if yb <= e0 <= yt:                          # section entièrement tendue
            L = yt - yb
            if L <= 1e-6:
                out["A_top"] = out["A_bot"] = abs(N) / (2 * fyd)
            else:
                out["A_top"] = abs(N) * (e0 - yb) / (L * fyd)
                out["A_bot"] = abs(N) * (yt - e0) / (L * fyd)
            return out
    MA = M + N * (d - hh / 2)
    if MA < 0:
        MA = 0.0
    Af_flange = 0.0
    bw = b
    if bf > b and hf > 0:                           # section en T (table comprimée en haut)
        muf = MA / (bf * d * d * fc)
        mul0 = mat.mu_lim(gamma)
        xif = 1.25 * (1 - math.sqrt(max(0.0, 1 - 2 * min(muf, mul0))))
        if 0.8 * xif * d <= hf + 1e-9 and muf <= mul0:
            bw = bf                                 # axe neutre dans la table : rectangle de largeur bf
        else:
            Mf = (bf - b) * hf * fc * (d - hf / 2.0)
            if MA > Mf:
                MA -= Mf
                Af_flange = (bf - b) * hf * fc / fyd
            else:
                bw = bf
    Af, Ac, mu = _rect_flex(MA, bw, d, dtop, mat, gamma)
    out["mu"] = mu
    out["A_bot"] = max(0.0, Af + Af_flange - N / fyd)
    out["A_top"] = Ac
    if N > bw * hh * fc:
        out["ok"] = False
    return out


# --------------------------------------------------------------------------------------
# Efforts dans la poutre continue (méthode des rotations, EI constant)
# --------------------------------------------------------------------------------------
class Analysis:
    def __init__(self, prj: Project, dx: float = 0.025):
        self.p = prj
        self.mat = Mat(prj.code, prj.fck, prj.fyk)
        L = np.array(prj.spans, dtype=float)
        self.L = L
        n = len(L)
        self.n = n
        self.xsup = np.concatenate([[0.0], np.cumsum(L)])
        self.Ltot = float(self.xsup[-1])
        prj.sync()
        self.bars = [AbsBar(br.face, *prj.bar_x(br), br.n, br.dia, br.row, br.n2, br.dia2) for br in prj.bars]
        self.stir = [AbsStir(*prj.stir_x(z), z.dia, z.spacing, z.legs) for z in prj.stirrups]
        sw = 25.0 * (prj.b / 1000.0 * prj.h / 1000.0 + ((prj.bf - prj.b) * prj.hf / 1e6 if prj.is_T else 0.0)) if prj.selfweight else 0.0
        self.sw = sw
        self.g = np.array(prj.g, dtype=float) + sw
        self.q = np.array(prj.q, dtype=float)
        # grille de calcul
        xs, sid, xl = [], [], []
        for k in range(n):
            m = max(2, int(round(L[k] / dx)) + 1)
            loc = np.linspace(0.0, L[k], m)
            xs.append(self.xsup[k] + loc)
            sid.append(np.full(m, k))
            xl.append(loc)
        self.xs = np.concatenate(xs)
        self.sid = np.concatenate(sid)
        self.xl = np.concatenate(xl)
        self._combos()

    # matrice de raideur (EI = 1)
    def _K(self):
        n, L = self.n, self.L
        K = np.zeros((n + 1, n + 1))
        for j in range(n):
            K[j, j] += 4 / L[j]
            K[j + 1, j + 1] += 4 / L[j]
            K[j, j + 1] += 2 / L[j]
            K[j + 1, j] += 2 / L[j]
        return K

    def _solve(self, w):
        """w : (C, n) charges réparties par travée -> M0 (C,n), V0 (C,n) (M positif = fibre inf. tendue)."""
        C, n = w.shape
        L = self.L
        F = np.zeros((C, n + 1))
        for j in range(n + 1):
            if j < n:
                F[:, j] += -w[:, j] * L[j] ** 2 / 12
            if j > 0:
                F[:, j] += w[:, j - 1] * L[j - 1] ** 2 / 12
        theta = np.linalg.solve(self._K(), (-F).T).T          # (C, n+1)
        Mab = (2 / L) * (2 * theta[:, :-1] + theta[:, 1:]) - w * L ** 2 / 12
        Mba = (2 / L) * (2 * theta[:, 1:] + theta[:, :-1]) + w * L ** 2 / 12
        M0 = Mab
        ML = -Mba
        V0 = (ML - M0 + w * L ** 2 / 2) / L
        return M0, V0

    def _field(self, w):
        M0, V0 = self._solve(w)
        s = self.sid
        wl = w[:, s]
        M = M0[:, s] + V0[:, s] * self.xl - wl * self.xl ** 2 / 2
        V = V0[:, s] - wl * self.xl
        return M, V

    def _combos(self):
        n = self.n
        if n <= 8:
            subsets = [c for c in itertools.product([0, 1], repeat=n)]
        else:
            subsets = [tuple([0] * n)]
            for k in range(n):
                s = [0] * n
                s[k] = 1
                subsets.append(tuple(s))
            subsets.append(tuple(1 if k % 2 == 0 else 0 for k in range(n)))
            subsets.append(tuple(1 if k % 2 == 1 else 0 for k in range(n)))
            subsets.append(tuple([1] * n))
        S = np.array(subsets, dtype=float)                       # (C, n)
        self.subsets = S
        wu = 1.35 * self.g[None, :] + 1.5 * self.q[None, :] * S
        ws = self.g[None, :] + self.q[None, :] * S
        self.Mu, self.Vu = self._field(wu)
        self.Ms, _ = self._field(ws)
        self.Mmax = self.Mu.max(axis=0)
        self.Mmin = self.Mu.min(axis=0)
        self.Vmax = self.Vu.max(axis=0)
        self.Vmin = self.Vu.min(axis=0)

    # efforts dominants dans une fenêtre [x0, x1]
    def demands(self, x0, x1):
        i0 = int(np.searchsorted(self.xs, x0 - 1e-9, "left"))
        i1 = int(np.searchsorted(self.xs, x1 + 1e-9, "right"))
        if i1 <= i0:
            i1 = i0 + 1
        Mw = self.Mu[:, i0:i1]
        Vw = self.Vu[:, i0:i1]
        C = Mw.shape[0]
        k = np.argmax(np.abs(Mw), axis=1)
        ar = np.arange(C)
        Mg = Mw[ar, k]
        Msg = self.Ms[:, i0:i1][ar, k]
        Vg = np.max(np.abs(Vw), axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            gam = np.where(np.abs(Msg) > 1e-6, Mg / Msg, 1.4)
        gam = np.clip(np.nan_to_num(gam, nan=1.4), 1.0, 1.6)
        cand = {int(np.argmax(Mg)), int(np.argmin(Mg)), int(np.argmax(Vg)), int(np.argmax(np.abs(Mg)))}
        return [(float(Mg[c]) * 1e6, float(Vg[c]) * 1e3, float(gam[c])) for c in cand]

    # ------------------------------------------------------------------ armatures existantes
    def d_row(self, row, dia):
        p = self.p
        return p.cnom + p.phi_t + dia / 2 + (row - 1) * (dia + max(dia, 25.0))

    def bars_cover(self, face, x0, x1):
        """Barres de la face qui couvrent entièrement [x0, x1] :
        (A mm², profondeur du centre de gravité mm, n barres, libellé)."""
        A, S, nb, lab = 0.0, 0.0, 0, []
        for br in self.bars:
            if br.face != face:
                continue
            if br.x0 <= x0 + 1e-6 and br.x1 >= x1 - 1e-6:
                a = br.area
                A += a
                S += a * self.d_row(br.row, br.dmax)
                nb += br.nbars
                lab.append(br.label)
        return A, (S / A if A > 0 else None), nb, "+".join(lab)

    def bars_cut(self, x0, x1):
        """Barres qui rencontrent la fenêtre : liste (face, profondeur extérieure mm)."""
        res = []
        for br in self.bars:
            if br.x1 > x0 + 1e-6 and br.x0 < x1 - 1e-6:
                res.append((br.face, self.d_row(br.row, br.dmax) + br.dmax / 2, br))
        return res

    def stirrup_area(self, x0, x1):
        """Section d'acier d'étriers (mm²) dont les cours tombent dans [x0, x1]."""
        tot = 0.0
        x0 = max(0.0, x0)
        x1 = min(self.Ltot, x1)
        for z in self.stir:
            a, b2 = max(x0, z.x0), min(x1, z.x1)
            if b2 <= a:
                continue
            lines = math.floor((b2 - a) * 1000.0 / z.spacing + 1e-6)
            lines = max(lines, 1) if (b2 - a) * 1000.0 > 0.5 * z.spacing else lines
            tot += lines * z.legs * math.pi * z.dia ** 2 / 4
        return tot


# --------------------------------------------------------------------------------------
# Vérification d'une réservation
# --------------------------------------------------------------------------------------
@dataclass
class OpeningResult:
    status: int = STATUS_NO
    xc: float = 0.0
    a: float = 0.0
    ho: float = 0.0
    h1: float = 0.0
    h2: float = 0.0
    method: str = ""
    shape: str = "rect"
    reasons: list = field(default_factory=list)
    info: dict = field(default_factory=dict)
    extra_mm2: float = 0.0          # total d'acier à ajouter (indicatif, pour comparer les variantes)

    @property
    def label(self):
        return STATUS_LABEL[self.status]


class Checker:
    def __init__(self, an: Analysis):
        self.an = an
        self.p = an.p
        self.mat = an.mat
        self.dmin_appui = (self.p.dist_appui if self.p.dist_appui > 0 else self.p.h / 2) / 1000.0
        self.others = []      # [(xc m, a mm, ho mm)] autres réservations déjà placées

    # ---------- point d'entrée
    def _near_support(self, xl, xr):
        """Indice de l'appui trop proche (distance mini à la face), sinon None."""
        for k, xs in enumerate(self.an.xsup):
            # l'appui est une discontinuité (largeur nulle dans le calcul) : distance mini mesurée depuis l'appui
            if xl < xs + self.dmin_appui and xr > xs - self.dmin_appui:
                return k
        return None

    def check_opening(self, op, others=None):
        return self.check(op.xc, op.a, op.ho_eff, h1=(op.h1 if op.mode == "manual" else None),
                          mode=op.mode, shape=op.shape, others=others)

    def check(self, xc, a, ho, h1=None, mode="auto", shape="rect", others=None) -> OpeningResult:
        """xc (m) ; a, ho (mm) ; h1 (mm) = distance face haute -> haut de la réservation.
        mode : "auto" (cherche la meilleure position verticale), "center", "manual" (utilise h1)."""
        p = self.p
        if shape == "circle":
            ho = a
        if mode == "manual" and h1 is not None:
            cands = [h1]
        else:
            lo = p.hlint_min
            hi = p.h - ho - p.hlint_min
            if hi < lo - 1e-6:
                r = OpeningResult(STATUS_NO, xc, a, ho, 0, 0, shape=shape)
                r.reasons.append(f"Hauteur de réservation trop grande pour garder deux linteaux de {p.hlint_min:.0f} mm.")
                return r
            c = (p.h - ho) / 2
            if mode == "center":
                cands = [c]
            else:
                cands = sorted(set(round(v, 1) for v in
                                   list(np.linspace(lo, hi, 7)) + [c]))
                cands = self._window_candidates(xc, a, ho) + cands
        best = None
        for h1c in cands:
            r = self._check_fixed(xc, a, ho, h1c, shape, others)
            key = (r.status, r.extra_mm2)
            if best is None or key < (best.status, best.extra_mm2):
                best = r
            if r.status == STATUS_OK:
                break
        return best

    def _window_candidates(self, xc, a, ho):
        """Positions h1 qui placent la réservation dans la fenêtre du treillis (si elle existe)."""
        try:
            w = self._truss_window(xc, a)
        except Exception:
            return []
        if w is None:
            return []
        top, bot = w
        lo, hi = top, self.p.h - bot - ho
        if hi < lo:
            return []
        return [round(0.5 * (lo + hi), 1), round(lo, 1), round(hi, 1)]

    # ---------- fenêtre du treillis pour une section
    def _section_state(self, M, V, gamma, xl, xr):
        """Flexion globale de la section pleine + largeur de bielle + marges du treillis."""
        p, mat, an = self.p, self.mat, self.an
        h, b = p.h, p.b
        sgn = 1 if M >= 0 else -1
        face_t, face_c = ("bot", "top") if sgn > 0 else ("top", "bot")
        A_ts, d_ts, n_ts, lab_ts = an.bars_cover(face_t, xl, xr)
        A_cs, d_cs, n_cs, lab_cs = an.bars_cover(face_c, xl, xr)
        d_def = p.cnom + p.phi_t + 10.0
        dts = d_ts if d_ts is not None else d_def
        dcs = d_cs if d_cs is not None else d_def
        d = h - dts
        Mn = abs(M)
        mul = mat.mu_lim(gamma)
        bfl = p.bf if (p.is_T and sgn > 0) else 0.0          # table comprimée seulement sous moment positif
        bw = b
        mu = Mn / ((bfl if bfl > 0 else b) * d * d * mat.fc)
        Fflange = 0.0
        MAw = Mn
        if bfl > 0:
            xif = 1.25 * (1 - math.sqrt(max(0.0, 1 - 2 * min(mu, mul))))
            if 0.8 * xif * d <= p.hf + 1e-9 and mu <= mul:
                bw = bfl
            else:
                Mf = (bfl - b) * p.hf * mat.fc * (d - p.hf / 2.0)
                if Mn > Mf:
                    Fflange = (bfl - b) * p.hf * mat.fc
                    MAw = Mn - Mf
                    mu = MAw / (b * d * d * mat.fc)
                else:
                    bw = bfl
        ok = mu <= 0.5
        mue = min(mu, mul)
        xi = 1.25 * (1 - math.sqrt(max(0.0, 1 - 2 * mue)))
        z = d * (1 - 0.4 * xi)
        xu = xi * d
        if mu <= mul:
            Fw = MAw / z
        else:
            Mlim = mul * bw * d * d * mat.fc
            Fw = Mlim / z + (MAw - Mlim) / max(d - dcs, 1.0)
        F = Fw + Fflange
        z = Mn / F if F > 0 else z
        a_v = 2.0 * V / (b * mat.strut_limit())                 # épaisseur verticale de la bielle
        m_c = 0.4 * xu + max(0.6 * xu, a_v)                     # marge côté membrure comprimée
        m_t = 2.0 * (h - d)                                     # marge côté membrure tendue
        hw = h - m_t - m_c
        return dict(sgn=sgn, A_ts=A_ts, d_ts=dts, lab_ts=lab_ts, n_ts=n_ts,
                    A_cs=A_cs, d_cs=dcs, lab_cs=lab_cs, n_cs=n_cs,
                    d=d, mu=mu, mul=mul, xi=xi, xu=xu, z=z, F=F, a_v=a_v,
                    m_c=m_c, m_t=m_t, hw=hw, ok=ok, M=Mn, V=V, gamma=gamma,
                    top_margin=(m_c if sgn > 0 else m_t),
                    bot_margin=(m_t if sgn > 0 else m_c))

    def _truss_window(self, xc, a):
        an = self.an
        xl, xr = xc - a / 2000.0, xc + a / 2000.0
        top, bot = 0.0, 0.0
        for (M, V, g) in an.demands(xl, xr):
            s = self._section_state(M, V, g, xl, xr)
            top = max(top, s["top_margin"])
            bot = max(bot, s["bot_margin"])
        return top, bot

    # ---------- vérification pour une position verticale donnée
    def _check_fixed(self, xc, a, ho, h1, shape, others=None) -> OpeningResult:
        p, mat, an = self.p, self.mat, self.an
        h, b = p.h, p.b
        h2 = h - h1 - ho
        R = OpeningResult(STATUS_NO, xc, a, ho, h1, h2, shape=shape)
        xl, xr = xc - a / 2000.0, xc + a / 2000.0
        # ----- règles géométriques
        if xl < 0 or xr > an.Ltot:
            R.reasons.append("La réservation sort de la poutre.")
            return R
        if h1 < p.hlint_min - 1e-6 or h2 < p.hlint_min - 1e-6:
            R.reasons.append(f"Linteau inférieur à {p.hlint_min:.0f} mm (h1 = {h1:.0f}, h2 = {h2:.0f} mm).")
            return R
        if p.is_T and h1 < p.hf - 1e-6:
            R.reasons.append(f"Réservation dans la table de compression (h1 = {h1:.0f} mm < épaisseur de table {p.hf:.0f} mm).")
            return R
        k = self._near_support(xl, xr)
        if k is not None:
            R.reasons.append(f"Réservation à moins de {self.dmin_appui*1000:.0f} mm de l'appui A{k+1}.")
            return R
        for (oxc, oa, oho) in (self.others if others is None else others):
            gap = abs(xc - oxc) * 1000.0 - a / 2.0 - oa / 2.0
            need = 2.0 * max(ho, oho)
            if gap < need - 1e-6:
                R.reasons.append(f"Trop près d'une autre réservation : écart {max(gap,0):.0f} mm < 2 × {max(ho,oho):.0f} = {need:.0f} mm.")
                return R
        for face, ext, br in an.bars_cut(xl, xr):
            lim = h1 if face == "top" else h2
            if ext > lim - 5.0:
                R.reasons.append(f"Armatures {br.label} ({'sup.' if face=='top' else 'inf.'}) recoupées par la réservation.")
                return R

        pairs = an.demands(xl, xr)
        worst = None
        for (M, V, g) in pairs:
            r = self._eval_pair(R, M, V, g, xl, xr)
            key = (r["rank"], r["extra"])
            if worst is None or key > (worst["rank"], worst["extra"]):
                worst = r
        R.status = worst["rank"]
        R.reasons = worst["reasons"]
        R.info = worst["info"]
        R.method = worst["method"]
        R.extra_mm2 = worst["extra"]
        return R

    def _eval_pair(self, R, M, V, gamma, xl, xr):
        p, mat, an = self.p, self.mat, self.an
        h, b = p.h, p.b
        a, ho, h1, h2 = R.a, R.ho, R.h1, R.h2
        s = self._section_state(M, V, gamma, xl, xr)
        sgn = s["sgn"]
        reasons, info = [], {}
        info.update(M_kNm=M / 1e6, V_kN=V / 1e3, sens="M>0 (membrure comprimée en haut)" if sgn > 0 else "M<0 (membrure comprimée en bas)",
                    xu_mm=s["xu"], z_mm=s["z"], F_kN=s["F"] / 1e3, a_v_mm=s["a_v"], hw_mm=s["hw"],
                    marge_haut_mm=s["top_margin"], marge_bas_mm=s["bot_margin"],
                    A_ts_exist_cm2=s["A_ts"] / 100, A_cs_exist_cm2=s["A_cs"] / 100,
                    lab_ts=s["lab_ts"], lab_cs=s["lab_cs"], gamma=gamma)
        out = dict(rank=STATUS_NO, reasons=reasons, info=info, method="", extra=0.0)
        if not s["ok"]:
            reasons.append("Section de poutre insuffisante en flexion (μ > 0,5) : la poutre elle-même n'est pas vérifiée à cet endroit.")
            return out
        if s["A_ts"] * mat.fyd < s["F"] * 0.999:
            info["nota_flexion"] = ("Armatures tendues existantes ({:.1f} cm²) inférieures à celles que demande le moment "
                                    "(≈ {:.1f} cm²) : à vérifier indépendamment de la réservation.").format(s["A_ts"] / 100, s["F"] / mat.fyd / 100)

        # ----- 1) critère du treillis de Mörsch (petites ouvertures)
        hw = s["hw"]
        if R.shape == "circle":
            fits = hw > 0 and a <= (2 - SQRT2) * hw + 1e-6
            info["critere_treillis"] = f"Ø {a:.0f} ≤ 0,586·h_w = {(2-SQRT2)*max(hw,0):.0f} mm"
        else:
            fits = hw > 0 and (a + ho) <= hw + 1e-6
            info["critere_treillis"] = f"a + h = {a+ho:.0f} ≤ h_w = {max(hw,0):.0f} mm"
        in_window = (h1 >= s["top_margin"] - 1e-6) and (h1 + ho <= h - s["bot_margin"] + 1e-6)
        info["treillis_ok"] = bool(fits and in_window)
        # armatures de suspension (montants) : A_wt = V / f_yd sur la largeur du triangle
        wl = max(hw, a, 100.0) / 1000.0
        xm = 0.5 * (xl + xr)
        A_req_susp = V / mat.fyd
        A_ex_susp = an.stirrup_area(xm - wl / 2, xm + wl / 2)
        info["susp_req_cm2"] = A_req_susp / 100
        info["susp_exist_cm2"] = A_ex_susp / 100
        if fits and in_window:
            out["method"] = "Treillis de Mörsch (petite ouverture)"
            if A_ex_susp + 1e-6 >= A_req_susp:
                out["rank"] = STATUS_OK
                reasons.append("L'ouverture s'inscrit dans le treillis ; étriers existants suffisants.")
            else:
                out["rank"] = STATUS_REINF
                out["extra"] = A_req_susp - A_ex_susp
                reasons.append("L'ouverture s'inscrit dans le treillis mais il faut renforcer les étriers (armatures de suspension) : "
                               f"{A_req_susp/100:.2f} cm² requis pour {A_ex_susp/100:.2f} cm² en place.")
                info["extra_susp_cm2"] = (A_req_susp - A_ex_susp) / 100
            return out

        # ----- 2) grandes ouvertures : méthode des linteaux
        out["method"] = "Linteaux (grande ouverture)"
        if not in_window and fits:
            reasons.append("Position verticale hors de la fenêtre du treillis : calcul par la méthode des linteaux.")
        else:
            reasons.append(f"Ne s'inscrit pas dans le treillis ({info['critere_treillis']}) : calcul par la méthode des linteaux.")
        if sgn > 0:
            hc, ht = h1, h2
        else:
            hc, ht = h2, h1
        # zone comprimée non recoupée
        yblock = 0.8 * s["xu"]
        info["bloc_comprime_mm"] = yblock
        if yblock > hc + 1e-6:
            reasons.append(f"Le linteau comprimé ({hc:.0f} mm) est plus mince que le bloc comprimé ({yblock:.0f} mm) : zone comprimée recoupée.")
            return out
        F = s["F"]
        d_def = p.cnom + p.phi_t + 10.0
        # répartition de l'effort tranchant entre linteaux
        if a < max(h1, h2):
            Vc = V * hc / (hc + ht)
        else:
            Vc = V * hc ** 3 / (hc ** 3 + ht ** 3)
        Vt = V - Vc
        # linteau comprimé : N = +F appliqué à 0,4 x sous la face comprimée
        Nc = F
        ec = hc / 2 - 0.4 * s["xu"]
        # linteau tendu : N = -F appliqué au centre de gravité des aciers tendus
        Nt = -F
        et = -(ht / 2 - s["d_ts"])
        lint = {}
        fail = False
        for name, Nn, e0, Vi, hi, d_out, d_in, exist_out, out_is_top in (
                ("comprimé", Nc, ec, Vc, hc, s["d_cs"], d_def, s["A_cs"], True),
                ("tendu", Nt, et, Vt, ht, s["d_ts"], d_def, s["A_ts"], False)):
            M0 = Nn * e0
            dtop, dbot = (d_out, d_in) if out_is_top else (d_in, d_out)
            reqs = []
            for sg in (+1, -1):
                Mend = M0 + sg * Vi * a / 2.0
                r = design_section(Nn, Mend, b, hi, dtop, dbot, mat, gamma, *((p.bf, p.hf) if (p.is_T and sgn > 0 and out_is_top and hi >= p.hf) else (0.0, 0.0)))
                if not r["ok"]:
                    fail = True
                reqs.append(r)
            A_top = max(r["A_top"] for r in reqs)
            A_bot = max(r["A_bot"] for r in reqs)
            A_outer, A_inner = (A_top, A_bot) if out_is_top else (A_bot, A_top)
            extra_outer = max(0.0, A_outer - exist_out)
            extra_inner = A_inner
            if A_outer + A_inner > 0.04 * b * hi:
                fail = True
                reasons.append(f"Linteau {name} : ferraillage > 4 % de la section ({(A_outer+A_inner)/100:.1f} cm²).")
            # cisaillement du linteau
            di = hi - d_def
            zi = 0.9 * di
            Asw_s = 0.0
            if mat.code == "EC2":
                VRdmax = b * zi * mat.nu1 * mat.fc / 2.0
                if Vi > VRdmax:
                    fail = True
                    reasons.append(f"Linteau {name} : bielle d'effort tranchant écrasée (V = {Vi/1e3:.0f} kN > {VRdmax/1e3:.0f} kN).")
                k = min(1 + math.sqrt(200.0 / max(di, 1.0)), 2.0)
                rho = min(exist_out / (b * di), 0.02)
                sigcp = max(0.0, min(Nn / (b * hi), 0.2 * mat.fc)) if Nn > 0 else 0.0
                vmin = 0.035 * k ** 1.5 * math.sqrt(mat.fck)
                VRdc = (max(0.18 / mat.gc * k * (100 * rho * mat.fck) ** (1 / 3), vmin) + 0.15 * sigcp) * b * di if Nn > 0 else 0.0
                if Vi > VRdc:
                    Asw_s = Vi / (zi * mat.fyd)
            else:
                tau = Vi / (b * di)
                tlim = min(0.2 * mat.fck / mat.gc, 5.0)
                if tau > tlim:
                    fail = True
                    reasons.append(f"Linteau {name} : τu = {tau:.2f} MPa > {tlim:.2f} MPa.")
                if Nn > 0:
                    kk = 1 + 3 * (Nn / (b * hi)) / mat.fck
                else:
                    kk = max(0.0, 1 - 10 * abs(Nn) / (b * hi * mat.fck))
                ft = min(mat.ft28, 3.3)
                Asw_s = max(0.0, mat.gs * b * (tau - 0.3 * ft * kk) / (0.9 * mat.fyk))
            lint[name] = dict(h=hi, N_kN=Nn / 1e3, e_mm=e0, V_kN=Vi / 1e3, M0_kNm=M0 / 1e6,
                              A_outer_req_cm2=A_outer / 100, A_inner_req_cm2=A_inner / 100,
                              A_outer_exist_cm2=exist_out / 100,
                              extra_outer_cm2=extra_outer / 100, extra_inner_cm2=extra_inner / 100,
                              Asw_s_cm2_m=Asw_s * 10.0, outer_face=("haute" if (out_is_top == (sgn > 0)) else "basse"))
            out["extra"] += extra_outer + extra_inner + Asw_s * 1000.0 * 0.5
        info["linteaux"] = lint
        if fail:
            if not any("Linteau" in r for r in reasons[1:]):
                reasons.append("Linteau(x) insuffisant(s) : section ou ferraillage hors limites.")
            return out
        # armatures de suspension autour de l'ouverture (largeur de bielle)
        reach = max(s["a_v"], 100.0) / 1000.0
        A_l = an.stirrup_area(xl - reach, xl)
        A_r = an.stirrup_area(xr, xr + reach)
        A_req = V / mat.fyd
        info["susp_req_cm2"] = A_req / 100
        info["susp_g_cm2"] = A_l / 100
        info["susp_d_cm2"] = A_r / 100
        extra_susp = max(0.0, A_req - A_l) + max(0.0, A_req - A_r)
        info["extra_susp_cm2"] = extra_susp / 100
        out["extra"] += extra_susp
        # bielles à l'extrémité du linteau comprimé : largeur disponible
        tot_extra = sum(l["extra_outer_cm2"] + l["extra_inner_cm2"] for l in lint.values())
        need_stirrups = any(l["Asw_s_cm2_m"] > 0 for l in lint.values())
        if tot_extra > 0.01 or need_stirrups or extra_susp > 1.0:
            out["rank"] = STATUS_REINF
            reasons.append("Renforts à prévoir : aciers longitudinaux dans les linteaux, cadres des linteaux et/ou étriers de suspension.")
        else:
            out["rank"] = STATUS_OK
            reasons.append("Les armatures en place suffisent pour les linteaux.")
        return out

    # ---------- cartes le long de la poutre
    def scan(self, a, ho, step=0.05, mode="auto", shape="rect"):
        xs = np.arange(0.0, self.an.Ltot + 1e-9, step)
        st = np.array([self.check(float(x), a, ho, mode=mode, shape=shape).status for x in xs])
        return xs, st

    def hw_curve(self, a, step=0.05):
        """h_w(x) pour une réservation de longueur a (critère du treillis) -> hauteur max sans renfort = h_w - a."""
        xs = np.arange(0.0, self.an.Ltot + 1e-9, step)
        out = np.zeros_like(xs)
        for i, x in enumerate(xs):
            xl, xr = x - a / 2000.0, x + a / 2000.0
            if xl < 0 or xr > self.an.Ltot:
                continue
            if self._near_support(xl, xr) is not None:
                continue
            hw = 1e9
            for (M, V, g) in self.an.demands(xl, xr):
                s = self._section_state(M, V, g, xl, xr)
                hw = min(hw, s["hw"])
            out[i] = max(0.0, hw)
        return xs, out

    def _bisect(self, f, lo, hi, it=8):
        """Plus grande valeur dans [lo, hi] pour laquelle f est vraie (f décroissante)."""
        if not f(lo):
            return 0.0
        if f(hi):
            return hi
        for _ in range(it):
            mid = 0.5 * (lo + hi)
            if f(mid):
                lo = mid
            else:
                hi = mid
        return lo

    def max_height_curves(self, a, step=0.1, mode="auto", shape="rect", progress=None):
        """Pour une longueur a : hauteur max sans renfort (vert) et avec renforts (orange), le long de la poutre."""
        p = self.p
        xs = np.arange(0.0, self.an.Ltot + 1e-9, step)
        hg = np.zeros_like(xs)
        ho_ = np.zeros_like(xs)
        hmax = p.h - 2 * p.hlint_min
        for i, x in enumerate(xs):
            x = float(x)
            hg[i] = self._bisect(lambda v: self.check(x, a, v, mode=mode, shape=shape).status == STATUS_OK, 40.0, hmax, 7)
            ho_[i] = self._bisect(lambda v: self.check(x, a, v, mode=mode, shape=shape).status <= STATUS_REINF, 40.0, hmax, 7)
            if progress:
                progress(i + 1, len(xs))
        return xs, hg, np.maximum(hg, ho_)

    def max_length_curves(self, ho, step=0.1, mode="auto", shape="rect", amax=3000.0, progress=None):
        xs = np.arange(0.0, self.an.Ltot + 1e-9, step)
        ag = np.zeros_like(xs)
        ar = np.zeros_like(xs)
        for i, x in enumerate(xs):
            x = float(x)
            ag[i] = self._bisect(lambda v: self.check(x, v, ho, mode=mode, shape=shape).status == STATUS_OK, 40.0, amax, 7)
            ar[i] = self._bisect(lambda v: self.check(x, v, ho, mode=mode, shape=shape).status <= STATUS_REINF, 40.0, amax, 7)
            if progress:
                progress(i + 1, len(xs))
        return xs, ag, np.maximum(ag, ar)


# --------------------------------------------------------------------------------------
# Ferraillage de départ (aide au dessin - ne remplace pas une note de calcul de la poutre)
# --------------------------------------------------------------------------------------
def auto_reinforcement(prj: Project):
    """Propose un ferraillage de base cohérent avec les efforts (flexion + effort tranchant ELU).
    Retourne (bars, stirrups) au format « par travée »."""
    an = Analysis(prj)
    mat = an.mat
    b, h = prj.b, prj.h
    d = 0.9 * h
    n = len(prj.spans)
    bars, stir = [], []

    def pick(As):
        for dia in (12, 14, 16, 20, 25, 32):
            sp = dia + max(dia, 25.0)
            nmax = int((b - 2 * (prj.cnom + prj.phi_t) + max(dia, 25.0)) // sp)
            k = max(2, math.ceil(As / (math.pi * dia ** 2 / 4)))
            if k <= max(nmax, 2):
                return k, dia
        return 2, 32

    for k in range(n):
        x0, x1 = an.xsup[k], an.xsup[k + 1]
        sel = (an.xs >= x0) & (an.xs <= x1)
        Mp = max(float(an.Mmax[sel].max()), 0.0) * 1e6
        nb, dia = pick(Mp / (0.9 * d * mat.fyd))
        bars.append(LongBar("bot", k, 0.0, float(prj.spans[k]), nb, float(dia), 1))
    for j in range(1, n):
        xs_ = an.xsup[j]
        sel = (an.xs >= xs_ - 0.3) & (an.xs <= xs_ + 0.3)
        Mn = max(-float(an.Mmin[sel].min()), 0.0) * 1e6
        nb, dia = pick(Mn / (0.9 * d * mat.fyd))
        Lg, Ld = prj.spans[j - 1], prj.spans[j]
        bars.append(LongBar("top", j - 1, 0.7 * Lg, 0.3 * Lg + 0.3 * Ld, nb, float(dia), 1))
    bars.append(LongBar("top", 0, 0.0, float(an.Ltot), 2, 12.0, 1))

    def spacing(V):
        A1 = 2 * math.pi * 8 ** 2 / 4
        need = V / (0.9 * d * mat.fyd)            # mm²/mm
        s_ = A1 / need if need > 1e-9 else 1e9
        s_ = min(s_, 0.75 * d, 300.0)
        return float(max(75.0, math.floor(s_ / 25.0) * 25.0))

    for k in range(n):
        x0, x1 = float(an.xsup[k]), float(an.xsup[k + 1])
        Lk = x1 - x0
        sel = (an.xs >= x0) & (an.xs <= x1)
        xk = an.xs[sel]
        Vk = np.maximum(an.Vmax[sel], -an.Vmin[sel])
        Va = float(Vk[xk <= x0 + 0.25 * Lk].max()) * 1e3 * 0.9
        Vb = float(Vk[xk >= x1 - 0.25 * Lk].max()) * 1e3 * 0.9
        Vm = float(Vk[(xk > x0 + 0.25 * Lk) & (xk < x1 - 0.25 * Lk)].max()) * 1e3
        stir.append(StirrupZone(k, 0.0, 0.25 * Lk, 8.0, spacing(Va), 2))
        stir.append(StirrupZone(k, 0.25 * Lk, 0.5 * Lk, 8.0, spacing(Vm), 2))
        stir.append(StirrupZone(k, 0.75 * Lk, 0.25 * Lk, 8.0, spacing(Vb), 2))
    return bars, stir
