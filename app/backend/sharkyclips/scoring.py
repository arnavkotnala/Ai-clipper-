"""ai-clipper: two scores per highlight candidate, a category, and event phases.

    confidence  0..100  how sure we are that something real happened in this window
    engagement  0..100  how entertaining / shareable it is likely to be
    rank_score  0..100  w_c * confidence + w_e * engagement   (config "rank_weights")

Neither score is a prediction of views. They are transparent heuristics: every input is stored
with the candidate ("score_inputs"), so they can be re-weighted and checked against labelled
footage (scripts/evaluate.py). Default weights are NOT calibrated yet: see docs/RESULTS.md.

Confidence is about EVIDENCE: independent kinds of evidence that agree (audio, voice, speech,
visuals, game HUD, chat) plus the vision model's verdict when it ran. One loud noise alone is
weak evidence; a HUD multi-kill read by OCR is strong evidence.

Engagement is about INTEREST: the relative hype of the moment, how strongly the player reacted,
how rare the event category is, and the vision model's interest rating when it ran.

Phases (Stage D, timestamp refinement): setup (clip start) -> climax (hype peak) -> payoff (the
strongest reaction at/after the climax) -> end. The clip end is extended, within the mode's
maximum length, so the payoff plus post-roll is never cut off.
"""

from __future__ import annotations

from typing import Any

from . import config

# Evidence families: signals in one family are not independent of each other.
FAMILIES: dict[str, tuple[str, ...]] = {
    "audio": ("loudness",),
    "voice": ("laughter",),
    "speech": ("keywords", "speech"),
    "visual": ("motion",),
    "game": ("game",),
    "chat": ("chat",),
}
STRONG = 0.5  # a family "fires" when its best signal reaches this in the clip window

CATEGORIES = ("clutch", "multi_kill", "unexpected_kill", "high_skill", "funny", "unexpected_death",
              "near_death", "intense_fight", "close_victory", "rare_event", "reaction", "celebration",
              "commentary", "unexpected_event", "kill", "death", "other")

# How rare / shareable a category usually is (0..1). A starting point to tune, not a fact.
RARITY: dict[str, float] = {
    "clutch": 0.9, "multi_kill": 0.9, "rare_event": 0.9, "close_victory": 0.8, "high_skill": 0.8,
    "unexpected_kill": 0.7, "unexpected_death": 0.7, "unexpected_event": 0.7, "near_death": 0.6,
    "funny": 0.6, "intense_fight": 0.5, "celebration": 0.5, "kill": 0.4, "reaction": 0.4,
    "commentary": 0.3, "death": 0.3, "other": 0.2,
}

REACTION_CODES = {"laughter", "laugh", "shouting", "shout", "cheering", "cheer", "keyword",
                  "loudness_spike", "chat_spike", "chat_reactions", "pause_then_burst"}

DEFAULT_RANK_WEIGHTS = {"confidence": 0.4, "engagement": 0.6}
VLM_WEIGHT = 0.7  # how far a confident vision verdict moves confidence


def rank_weights() -> dict[str, float]:
    w = {**DEFAULT_RANK_WEIGHTS, **(config.get("rank_weights") or {})}
    c, e = max(0.0, float(w.get("confidence", 0.4))), max(0.0, float(w.get("engagement", 0.6)))
    s = c + e or 1.0
    return {"confidence": c / s, "engagement": e / s}


def _families(signal_scores: dict[str, float]) -> dict[str, float]:
    return {fam: max((float(signal_scores.get(n, 0) or 0) for n in names), default=0.0)
            for fam, names in FAMILIES.items() if any(n in signal_scores for n in names)}


def signal_category(cand: dict) -> str:
    """Coarse category from the detector evidence alone (the vision check can refine it)."""
    codes = [r.get("code", "") for r in cand.get("reasons") or []]
    joined = " ".join(codes)
    if any(k in joined for k in ("penta", "quadra", "triple", "double", "multi", "ace")):
        return "multi_kill"
    if any(k in joined for k in ("victory", "win", "extracted")):
        return "close_victory" if "clutch" in joined else "celebration"
    if "clutch" in joined:
        return "clutch"
    if "death" in joined or "extract_failed" in joined:
        return "death"
    if "kill" in joined:
        return "kill"
    if any(c in ("laughter", "laugh") for c in codes):
        return "funny"
    if any(c in ("cheering", "cheer") for c in codes):
        return "celebration"
    fam = _families(cand.get("signal_scores") or {})
    if fam.get("visual", 0) >= STRONG and fam.get("audio", 0) >= STRONG:
        return "intense_fight"
    if any(c in ("shouting", "shout", "keyword", "loudness_spike", "pause_then_burst") for c in codes):
        return "reaction"
    if any(c in ("fast_speech", "steady_explanation") for c in codes):
        return "commentary"
    if "scene_cut" in codes:
        return "unexpected_event"
    return "other"


def phases(cand: dict, post_roll: float, clip_max: float, duration: float) -> dict:
    """Setup / climax / payoff / end. May extend cand["end"] so the payoff isn't cut off."""
    start, end, peak = float(cand["start"]), float(cand["end"]), float(cand.get("peak", cand["start"]))
    climax = min(max(peak, start), end)
    payoff, best = climax, -1.0
    for r in cand.get("reasons") or []:
        t = float(r.get("t", -1))
        if r.get("code") in REACTION_CODES and climax <= t <= end + post_roll + 2:
            if float(r.get("strength", 0)) > best:
                payoff, best = t, float(r.get("strength", 0))
    want_end = min(duration, payoff + post_roll)
    extended = False
    if want_end > end and want_end - start <= clip_max:
        cand["end"] = round(want_end, 2)
        cand["duration"] = round(want_end - start, 2)
        extended = True
    return {"setup": round(start, 2), "climax": round(climax, 2), "payoff": round(payoff, 2),
            "end": round(float(cand["end"]), 2), "end_extended_for_payoff": extended}


def score(cand: dict) -> dict[str, Any]:
    """Compute confidence / engagement / rank_score for one candidate dict (in place)."""
    sig = cand.get("signal_scores") or {}
    fam = _families(sig)
    strong = [f for f, v in fam.items() if v >= STRONG]
    game_event = fam.get("game", 0) >= STRONG
    base = min(0.9, 0.25 + 0.12 * min(len([f for f in strong if f != "game"]), 4) + (0.3 if game_event else 0.0))

    ver = cand.get("verification") or {}
    vc = float(ver.get("confidence") or 0) if ver.get("status") == "ok" else 0.0
    verdict = ver.get("happened") if ver.get("status") == "ok" else None
    if verdict == "yes":
        conf = base + (1 - base) * VLM_WEIGHT * vc
    elif verdict == "no":
        conf = base * (1 - VLM_WEIGHT * vc)
    else:
        conf = base

    category = cand.get("category_vision") if verdict == "yes" and cand.get("category_vision") else signal_category(cand)
    reaction = max((float(sig.get(n, 0) or 0) for n in ("laughter", "keywords", "loudness", "chat")), default=0.0)
    hype = float(cand.get("hype_score") or 0) / 100
    rarity = RARITY.get(category, 0.2)
    if verdict in ("yes", "no") and ver.get("interest") is not None:
        eng = 0.45 * hype + 0.20 * reaction + 0.15 * rarity + 0.20 * float(ver["interest"])
    else:
        eng = 0.55 * hype + 0.25 * reaction + 0.20 * rarity

    w = rank_weights()
    cand["category"] = category
    cand["confidence"] = int(round(100 * min(1.0, max(0.0, conf))))
    cand["engagement"] = int(round(100 * min(1.0, max(0.0, eng))))
    cand["rank_score"] = int(round(w["confidence"] * cand["confidence"] + w["engagement"] * cand["engagement"]))
    cand["likely_false_positive"] = bool(verdict == "no" and vc >= 0.6)
    cand["score_inputs"] = {"families": {k: round(v, 3) for k, v in fam.items()}, "strong": strong,
                            "evidence_base": round(base, 3), "vision": verdict, "vision_conf": round(vc, 3),
                            "reaction": round(reaction, 3), "hype": round(hype, 3), "rarity": rarity,
                            "weights": w}
    return cand


def apply(result: dict, post_roll: float = 3.0, clip_max: float = 60.0) -> dict:
    """Score every candidate in a highlights.json dict and store the display order."""
    duration = float(result.get("duration") or 0) or 1e9
    for c in result.get("candidates", []):
        if "phases" not in c:
            c["phases"] = phases(c, post_roll, clip_max, duration)
        score(c)
    order = sorted(result.get("candidates", []),
                   key=lambda c: (c.get("likely_false_positive", False), -c.get("rank_score", 0), c["start"]))
    result["order"] = [c["id"] for c in order]
    result["scoring"] = {"version": 1, "rank_weights": rank_weights()}
    return result
