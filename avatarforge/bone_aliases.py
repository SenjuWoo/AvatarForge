"""Conservative, deterministic humanoid naming; no model/API calls required."""
import re

REQUIRED = ("Hips", "Spine", "Head", "LeftUpperLeg", "LeftLowerLeg", "LeftFoot",
            "RightUpperLeg", "RightLowerLeg", "RightFoot", "LeftUpperArm",
            "LeftLowerArm", "LeftHand", "RightUpperArm", "RightLowerArm", "RightHand")
ALIASES = {
    "Hips": ("hips", "hip", "pelvis", "bip01pelvis", "下半身", "センター"),
    "Spine": ("spine", "spine0", "spine1", "spine01", "abdomen", "上半身"),
    "Chest": ("chest", "spine2", "spine02", "upperbody", "上半身2"),
    "UpperChest": ("upperchest", "spine3", "spine03", "thorax"),
    "Neck": ("neck", "neck1", "neck01", "首"),
    "Head": ("head", "head1", "head01", "頭"),
    "Jaw": ("jaw", "jawbone", "jawbonex", "顎"),
}
PAIRS = {
    "Shoulder": ("shoulder", "clavicle", "collar", "肩"),
    "UpperArm": ("upperarm", "arm", "uparm", "腕"),
    "LowerArm": ("lowerarm", "forearm", "elbow", "ひじ"),
    "Hand": ("hand", "wrist", "手首"),
    "UpperLeg": ("upperleg", "upleg", "thigh", "足"),
    "LowerLeg": ("lowerleg", "calf", "shin", "knee", "leg", "ひざ"),
    "Foot": ("foot", "ankle", "足首"),
    "Toes": ("toes", "toe", "toebase", "つま先"),
    "Eye": ("eye", "eyeball", "目"),
}
for side in ("Left", "Right"):
    for body, names in PAIRS.items():
        # Generated bendy chains use Thigh_1 / Forearm_1 for their first joint.
        ALIASES[side + body] = tuple(alias for name in names for alias in (name, name + "1")) if body in {"UpperArm", "LowerArm", "UpperLeg", "LowerLeg"} else names
    for finger in ("Thumb", "Index", "Middle", "Ring", "Little"):
        alternatives = (finger.lower(), "pinky" if finger == "Little" else finger.lower())
        for number, segment in enumerate(("Proximal", "Intermediate", "Distal"), 1):
            ALIASES[side + finger + segment] = tuple(
                alias + str(number) for alias in alternatives
            ) + tuple("finger" + alias + str(number) for alias in alternatives) + tuple("hand" + alias + str(number) for alias in alternatives)


def normalize(name):
    """Return anatomical name, side and penalty for generated control prefixes."""
    name = name.casefold()
    # Numbered game deformers encode side and joint order explicitly. Do not
    # interpret arbitrary leg1/arm1 names using this namespace convention.
    game = re.fullmatch(r"game_([clr])\d+_(.+)", name)
    if game:
        side = {"c": None, "l": "Left", "r": "Right"}[game.group(1)]
        joint = game.group(2)
        anatomy = {"hip1": "hips", "spine1": "spine", "spine2": "chest", "spine3": "upperchest",
                   "clav1": "shoulder", "arm1": "upperarm", "arm2": "lowerarm", "arm3": "hand",
                   "leg1": "upperleg", "leg2": "lowerleg", "leg3": "foot", "leg4": "toes"}
        return anatomy.get(joint, joint), side, False
    generated = bool(re.match(r"^(?:def|org|mch|ctrl|ik|fk|dsp|root)[-_]", name))
    name = re.sub(r"^(?:def|org|mch|ctrl|ik|fk|dsp|root)[-_]", "", name)
    name = re.sub(r"^(?:mixamorig[:_]?|valvebiped[._]?bip0?[12][._]?|bip0?[12][._]?|bip[._])", "", name)
    side = None
    for label, tokens in (("Left", ("left", "左")), ("Right", ("right", "右"))):
        for token in tokens:
            if name.startswith(token) or name.endswith(token):
                name = name.removeprefix(token).removesuffix(token)
                side = label
    match = re.match(r"^([lr])[._ -](.+)$", name) or re.match(r"^(.+)[._ -]([lr])$", name)
    if match:
        if len(match.group(1)) == 1:
            side, name = ("Left" if match.group(1) == "l" else "Right"), match.group(2)
        else:
            side, name = ("Left" if match.group(2) == "l" else "Right"), match.group(1)
    return re.sub(r"[^\w]", "", name).replace("_", ""), side, generated


def map_humanoid(bones, overrides=None, preferred=None):
    """bones is a list of name strings; ambiguous alias matches stay unmapped."""
    overrides = overrides or {}
    result, ambiguous, used = [], [], set()
    preferred = set(preferred or ())
    for human, aliases in ALIASES.items():
        if human in overrides:
            target = overrides[human]
            if target in bones and target not in used:
                result.append({"humanName": human, "boneName": target, "confidence": 1.0})
                used.add(target)
            else:
                ambiguous.append({"humanName": human, "candidates": [target], "invalid_override": True})
            continue
        side = "Left" if human.startswith("Left") else "Right" if human.startswith("Right") else None
        candidates = []
        for name in bones:
            base, found_side, generated = normalize(name)
            if found_side == side and base in aliases and name not in used:
                # Anatomical alias order settles spine/spine1 and forearm/elbow naming collisions.
                score = (0.91 if generated else 0.97) - aliases.index(base) * .001
                candidates.append(((name in preferred, name in preferred and name.startswith("DEF-"), score), name))
        candidates.sort(reverse=True)
        if candidates and (len(candidates) == 1 or candidates[0][0] > candidates[1][0]):
            (_, _, score), name = candidates[0]
            result.append({"humanName": human, "boneName": name, "confidence": score})
            used.add(name)
        elif candidates:
            ambiguous.append({"humanName": human, "candidates": [name for _, name in candidates]})
    present = {item["humanName"] for item in result}
    return result, [human for human in REQUIRED if human not in present], ambiguous
