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
# XPS / XNALara writes the joint after the side: "leg left thigh", "arm left shoulder 1".
# A sided hip is the thigh. Numbered shoulder 2 is the upper arm when no upper-arm bone exists.
ALIASES["Spine"] += ("spinelower",)
ALIASES["Chest"] += ("spinemiddle",)
ALIASES["UpperChest"] += ("spineupper",)
ALIASES["Neck"] += ("necklower",)
ALIASES["Head"] += ("neckupper",)
for side in ("Left", "Right"):
    ALIASES[side + "Shoulder"] += ("shoulder1",)
    ALIASES[side + "UpperArm"] += ("shoulder2",)
    ALIASES[side + "UpperLeg"] += ("hip",)

ALIAS_WORDS = {alias for names in ALIASES.values() for alias in names}
JUNK_WORDS = {"control", "ctrl", "ik", "fk", "jnt", "joint", "bind", "bnd", "unused", "adj", "adjust",
              "helper", "twist", "end", "nub", "tip", "root", "char", "armor", "outfit", "weapon"}
REGION_WORDS = {"leg", "arm", "head"}
QUALIFIER_WORDS = {"lower", "middle", "upper"}
SIDE_WORDS = {"left": "Left", "right": "Right", "lf": "Left", "lt": "Left", "rt": "Right", "l": "Left", "r": "Right"}
SECONDARY_WORDS = {"back", "mid", "small"}


def _words(name):
    return [word for word in re.split(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]+", name) if word]


def _anatomical_base(words):
    """Prefer a real joint word over a region prefix. Keep spine/neck qualifiers.

    An unknown word such as twist, front or finger is a different bone. Collapsing
    it to the bare joint makes every helper a tied candidate.
    """
    flat = "".join(words)
    if flat in ALIAS_WORDS:
        return flat
    if any(word not in ALIAS_WORDS and word not in QUALIFIER_WORDS and not word.isdigit() for word in words):
        return flat
    specific = [word for word in words if word in ALIAS_WORDS and word not in REGION_WORDS]
    kept = []
    for word in words:
        stem = word.rstrip("0123456789")
        numbered = stem in ALIAS_WORDS and word[len(stem):].isdigit()
        if specific and (word in specific or word in QUALIFIER_WORDS or word.isdigit() or numbered):
            kept.append(word)
        elif not specific and (word in ALIAS_WORDS or word in QUALIFIER_WORDS or word.isdigit() or numbered):
            kept.append(word)
    return "".join(kept)


def normalize(name):
    """Return anatomical name, side, generated-prefix flag and extra score penalty."""
    original = name.casefold()
    name = original
    # Numbered game deformers encode side and joint order explicitly. Do not
    # interpret arbitrary leg1/arm1 names using this namespace convention.
    game = re.fullmatch(r"game_([clr])\d+_(.+)", name)
    if game:
        side = {"c": None, "l": "Left", "r": "Right"}[game.group(1)]
        joint = game.group(2)
        anatomy = {"hip1": "hips", "spine1": "spine", "spine2": "chest", "spine3": "upperchest",
                   "clav1": "shoulder", "arm1": "upperarm", "arm2": "lowerarm", "arm3": "hand",
                   "leg1": "upperleg", "leg2": "lowerleg", "leg3": "foot", "leg4": "toes"}
        return anatomy.get(joint, joint), side, False, 0.0
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
    raw_words = _words(name)
    # Dropped role words still need to lose to the clean joint of the same name.
    junk_penalty = 0.07 if any(word in JUNK_WORDS for word in raw_words) else 0.0
    words = [word for word in raw_words if word not in JUNK_WORDS]
    if side is None:
        for index, word in enumerate(words):
            if word in SIDE_WORDS:
                side = SIDE_WORDS[word]
                del words[index]
                break
    # big/mid/small/back are size or mirrored copies, not a different joint.
    penalty = junk_penalty + (0.02 if any(word in SECONDARY_WORDS for word in words) else 0.0)
    words = [word for word in words if word not in SECONDARY_WORDS | {"big"}]
    # Blender duplicate suffixes (.001) are not anatomical indexes like thumb1.
    if any(len(word) == 3 and word.isdigit() for word in words):
        penalty += 0.01
        words = [word for word in words if not (len(word) == 3 and word.isdigit())]
    if "valvebiped" in original or original.startswith("mixamorig"):
        penalty -= 0.003
    base = _anatomical_base(words)
    if not base:
        base = re.sub(r"[^\w]", "", name).replace("_", "")
    return base, side, generated, penalty


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
            base, found_side, generated, penalty = normalize(name)
            if found_side == side and base in aliases and name not in used:
                # Anatomical alias order settles spine/spine1 and forearm/elbow naming collisions.
                score = (0.91 if generated else 0.97) - aliases.index(base) * .001 - penalty
                suffix = re.search(r"\.(\d+)$", name)
                suffix_rank = -int(suffix.group(1)) if suffix else 0
                # Equal scores prefer the deform bone, then .001 over later duplicates.
                candidates.append(((name in preferred, name in preferred and name.startswith("DEF-"), score, name.startswith("DEF-"), suffix_rank, -len(name)), name))
        candidates.sort(reverse=True)
        if candidates and (len(candidates) == 1 or candidates[0][0] > candidates[1][0]):
            score = candidates[0][0][2]
            name = candidates[0][1]
            result.append({"humanName": human, "boneName": name, "confidence": score})
            used.add(name)
        elif candidates:
            ambiguous.append({"humanName": human, "candidates": [name for _, name in candidates]})
    present = {item["humanName"] for item in result}
    return result, [human for human in REQUIRED if human not in present], ambiguous
