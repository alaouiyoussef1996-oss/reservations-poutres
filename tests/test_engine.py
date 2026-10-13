import math, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
from engine import *

def test_statique_deux_travees():
    p = Project(spans=[5.0, 5.0], b=300, h=600, selfweight=False, g=[10, 10], q=[0, 0])
    an = Analysis(p)
    w = 1.35 * 10
    # appui central d'une poutre 2 travées égales : M = -wL²/8
    i = np.argmin(np.abs(an.xs - 5.0))
    assert abs(an.Mu[0, i] + w * 25 / 8) < 0.05
    # réaction d'appui d'extrémité 3wL/8
    assert abs(an.Vu[0, 0] - 3 * w * 5 / 8) < 0.05

def test_poutre_isostatique_livre_ch6():
    # Chap. 6 : 25x60, L=5,50, g=33, q=20 -> Mu=282 kN.m, Vu=205 kN
    p = Project(spans=[5.5], b=250, h=600, selfweight=False, g=[33], q=[20])
    an = Analysis(p)
    assert abs(an.Mmax.max() - 282) < 1.0
    assert abs(an.Vmax.max() - 205) < 1.0
    # x = 4,20 : M = 203 kN.m, V = -108 kN
    i = np.argmin(np.abs(an.xs - 4.2))
    assert abs(an.Mu[-1, i] - 203) < 1.0
    assert abs(an.Vu[-1, i] + 108) < 1.0

def test_treillis_livre_ch6():
    # h_w = 35,6 cm ; Xu = 12,4 cm ; a = 6,8 cm à x = 4,20 m (EC2, d = 0,54 m)
    p = Project(spans=[5.5], b=250, h=600, selfweight=False, g=[33], q=[20], cnom=0, phi_t=0)
    # armatures inf. dont le CDG est à 60 mm : rang 1 d'un HA20 -> 0+0+10 = 10 ; on force avec cnom
    p.cnom = 50.0; p.phi_t = 0.0
    p.bars = [LongBar("bot", 0, 0.0, 5.5, 3, 20, 1)]       # d_row = 50+0+10 = 60 mm
    an = Analysis(p)
    ck = Checker(an)
    s = ck._section_state(203e6, 108e3, 1.4, 4.1, 4.3)
    assert abs(s["d"] - 540) < 1
    assert abs(s["xu"] - 124) < 1.5, s["xu"]
    assert abs(s["a_v"] - 67.5) < 1.0, s["a_v"]
    assert abs(s["hw"] - 356) < 2.5, s["hw"]

def test_linteaux_bael_ex4():
    # Exemple n°4 BAEL : N=0,591 MN, V1=0,518 MN, M0=0,079 -> A' = 12,87 cm², A = 13,79 cm²
    mat = Mat("BAEL", 25.0, 500.0)
    N, M0, V1, a = 591e3, 79e3 * 1e3, 518e3, 600.0
    Mend = M0 + V1 * a / 2
    r = design_section(N, Mend, 500, 350, 30, 30, mat, gamma=1.502)
    assert abs(r["A_top"] / 100 - 12.87) < 0.4, r
    assert abs(r["A_bot"] / 100 - 13.79) < 0.6, r

def test_linteau_tendu_traction_bael_ex4():
    mat = Mat("BAEL", 25.0, 500.0)
    # extrémité gauche : N = -0,591, M = -0,042 -> section entièrement tendue : A1 = 11,87, A2 = 1,72 cm²
    r = design_section(-591e3, -42e3 * 1e3, 500, 250, 30, 30, mat, 1.4)
    assert abs(r["A_top"] / 100 - 11.87) < 0.3, r
    assert abs(r["A_bot"] / 100 - 1.72) < 0.3, r
    # extrémité droite : N = -0,591, M = +0,072 -> section partiellement tendue : A = 15,31 cm²
    r = design_section(-591e3, 72e3 * 1e3, 500, 250, 30, 30, mat, 1.4)
    assert abs(r["A_bot"] / 100 - 15.31) < 0.5, r
    assert r["A_top"] < 1.0

def test_projet_defaut_zones_vertes():
    p = default_project()
    an = Analysis(p)
    ck = Checker(an)
    xs, hw = ck.hw_curve(100.0, 0.25)
    assert hw.max() > 150          # il existe des zones d'ouverture sans renfort
    r = ck.check(3.0, 150, 100)
    assert r.status in (STATUS_OK, STATUS_REINF)


def test_positions_par_travee_et_appuis():
    p = default_project()
    an = Analysis(p)
    # barre inf. de la travée 2 : x de 6,0 à 11,0 ; zone d'étriers travée 2 début 3,5 m -> x = 9,5
    assert abs(an.bars[1].x0 - 6.0) < 1e-9 and abs(an.bars[1].x1 - 11.0) < 1e-9
    assert abs(an.stir[5].x0 - 9.5) < 1e-9
    # ancien format (x0/x1 absolus) relu correctement
    d = p.to_dict()
    d["bars"] = [dict(face="bot", x0=6.0, x1=11.0, n=4, dia=16, row=1)]
    q = Project.from_dict(d)
    assert q.bars[0].span == 1 and abs(q.bars[0].start) < 1e-9 and abs(q.bars[0].length - 5.0) < 1e-9

def test_largeur_appui_et_reservations_voisines():
    p = default_project()
    p.support_w = [300, 600, 300]
    ck = Checker(Analysis(p))
    # l'appui est une discontinuité sans longueur : l'exclusion (h/2 = 300 mm) ne dépend pas de sa largeur
    assert ck._near_support(5.5, 5.7) is None
    assert ck._near_support(5.8, 5.9) == 1
    assert abs(Analysis(p).Ltot - 11.0) < 1e-9
    assert ck._near_support(2.5, 2.7) is None
    # deux réservations de 300 mm de haut trop proches
    r = ck.check(2.8, 300, 300, others=[(3.3, 300, 300)])
    assert r.status == STATUS_NO and "autre réservation" in r.reasons[0]
    r2 = ck.check(2.8, 300, 300, others=[(4.5, 300, 300)])
    assert "autre réservation" not in " ".join(r2.reasons)

def test_ferraillage_depart_et_openings_json(tmp_path=None):
    import tempfile, os
    p = default_project()
    p.bars, p.stirrups = auto_reinforcement(p)
    f = os.path.join(tempfile.mkdtemp(), "p.json")
    p.save(f)
    q = Project.load(f)
    assert len(q.openings) == 1 and q.openings[0].name == "R1" and len(q.bars) == len(p.bars)

def test_diametres_mixtes_meme_ligne():
    import math
    p = default_project()
    p.bars[0].n2, p.bars[0].dia2 = 2, 16.0      # 4HA20 + 2HA16 sur la même ligne
    an = Analysis(p)
    A, d, nb, lab = an.bars_cover("bot", 1.0, 2.0)
    assert abs(A - (4 * math.pi * 400 / 4 + 2 * math.pi * 256 / 4)) < 1e-6
    assert nb == 6 and lab == "4HA20+2HA16"
    q = Project.from_dict(p.to_dict())
    assert q.bars[0].n2 == 2 and q.bars[0].dia2 == 16.0

def test_poutre_en_T():
    import math
    from engine import design_section, Mat
    m = Mat("EC2", 25, 500)
    # axe neutre dans la table : même résultat qu'une section rectangulaire de largeur bf
    rT = design_section(0, 150e6, 300, 600, 50, 50, m, 1.4, bf=900, hf=150)
    rR = design_section(0, 150e6, 900, 600, 50, 50, m, 1.4)
    assert abs(rT["A_bot"] - rR["A_bot"]) < 1e-6
    # table mince : la table est partiellement dans la bielle, besoin d'acier supérieur à la section rectangulaire large
    rT2 = design_section(0, 400e6, 300, 600, 50, 50, m, 1.4, bf=900, hf=60)
    rW = design_section(0, 400e6, 300, 600, 50, 50, m, 1.4)
    assert rT2["A_bot"] < rW["A_bot"] or rW["A_top"] > 0
    # poutre en T : poids propre augmenté, xu plus faible, réservation dans la table refusée
    p = default_project(); p0 = Analysis(p).sw
    p.bf, p.hf = 900.0, 200.0
    an = Analysis(p)
    assert an.sw > p0
    ck = Checker(an)
    r = ck.check(2.8, 300, 200, h1=150.0, mode="manual")
    assert "table" in r.reasons[0]
    q = Project.from_dict(p.to_dict())
    assert q.is_T and q.bf == 900.0
