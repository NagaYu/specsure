"""自由文（短いエージェント風回答）→ 構造化 claim の抽出 v1（ルールベース、precision 優先）。

対象パターン: 数値＋単位（属性の手がかり語つき）、型番（「型番は」等）、互換性（「〜に対応」「compatible with」）、
成分の含有・不在（「含みます」「不使用」「-free」「contains」）、同梱物（「付属」「comes with」）。
各 claim に元の文スパン（文字オフセット）を付ける。手がかり語の無い数値など、確信の持てない文は claim にせず
coverage（抽出できた文の割合）として数える。誤抽出は誤 BLOCK に直結するため、曖昧なものは捨てる。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

from .claims import Claim, severity_for
from .normalize import UnitError, load_config, units

EXTRACTOR_VERSION = "extract-rules/v2"

# 属性 → (次元, 手がかり語)。手がかり語は数値の前後（同じ節の中）に現れること
ATTRIBUTE_CUES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "output_power": (("power",), ("出力", "output", "outputs")),
    "power_consumption": (("power",), ("消費電力", "rated", "定格")),
    "weight": (("mass",), ("重さ", "重量", "質量", "軽量", "weighs", "weight")),
    "net_weight": (("mass",), ("内容量", "net weight", "net wt")),
    "caffeine": (("mass",), ("カフェイン", "caffeine")),
    "capacity_charge": (("electric_charge",), ("容量", "capacity")),
    "capacity_energy": (("energy",), ("容量", "capacity", "battery", "バッテリー")),
    "battery_life": (("time",), ("再生", "電池持ち", "バッテリー持ち", "battery life", "playback")),
    "length": (("length",), ("長さ", "ケーブル長", "length", "long")),
    "thickness": (("length",), ("厚さ", "厚み", "thick", "thickness")),
    "max_data_rate": (("data_rate",), ("伝送", "転送", "data rate", "bandwidth")),
    "ink_volume": (("volume",), ("インク量", "ink volume")),
    "volume": (("volume",), ("容量", "内容量", "bottle", "ボトル")),
    "page_yield": (("count",), ("印刷", "prints", "page yield")),
    "nominal_voltage": (("voltage",), ("battery", "バッテリー", "公称電圧")),
}
# 手がかり語が無くても単位だけで属性が一意に決まるもの
UNIT_ONLY = {"electric_charge": "capacity_charge", "data_rate": "max_data_rate", "frequency": "frequency"}
UNIT_KEY_ONLY = {"wh": "capacity_energy", "kwh": "capacity_energy"}       # Wh は電池容量（kcal と区別）
REGION_VOLTAGE_CUES = ("電圧", "runs on", "ac", "コンセント")

_NUM = r"(?<![0-9A-Za-z.])(?P<num>\d[\d,]*(?:\.\d+)?)"     # 日本語の直後の数値も拾う（「重さは112g」）
_UNIT = r"\s*(?P<unit>fl\.?\s?oz\.?|ミリリットル|グラム|時間|枚|ページ|インチ|[A-Za-z%]+)"
NUM_RE = re.compile(r"(?P<pre>約|およそ|approx\.?\s*|about\s+|up to\s+|最大|(?<![A-Za-z])(?:AC|DC))?"
                    + _NUM.replace("(?<![0-9A-Za-z.])", "(?<![0-9.])(?<![A-Za-z](?<!AC)(?<!DC))(?<![A-Za-z0-9]-)")
                    + _UNIT + r"(?![0-9-])")                      # 型番の中の数字（TR-LB-56WH）は拾わない
SPF_RE = re.compile(r"SPF\s*(?P<num>\d+)\+?", re.I)
# 入数（E5 数量）: 「12本セット」「2個入り」「pack of 12」「12-pack」「24 count」
PACK_RE = re.compile(r"(?P<a>\d+)\s*(?:本|個|袋|枚|包|粒|スティック)\s*(?:入り|入|セット)|"
                     r"\b(?:pack|set|box) of (?P<b>\d+)\b|\b(?P<c>\d+)[- ](?:pack|count|ct)\b", re.I)
PA_RE = re.compile(r"PA\+{1,4}")
MODEL_RE = re.compile(r"(?:型番は|型番:|品番は|model number is|model is)\s*(?P<m>[A-Za-z0-9][A-Za-z0-9\-‐/+]{2,})", re.I)

EN_CLAUSE_WORDS = {"it", "is", "are", "that", "which", "made", "also", "s", "was", "be", "handles"}
JP_VERB_FRAGMENT = re.compile(r"[ぁ-ゖー]*(?:る|た|て|ない|ます|です|れる|せる)")   # 「している」等の動詞の断片
JP_SPLIT = re.compile(r"[、，,・と]|や(?!す)")
JP_TOPIC = ("は", "にも", "には")
JP_VERB_END = re.compile(r"(?:ので|ため|ですが|ですし|で|て|し|が|も|ます|です|ません)$")

COMPAT_JP = re.compile(r"(?:には|にも|に)(?P<v>対応|使え)(?P<neg>していません|しておりません|しません|できません|ません)?")
COMPAT_JP_NEG2 = re.compile(r"(?P<x>[^\s、。]+?)(?:は|には)?非対応")
COMPAT_EN_NEG = re.compile(r"(?:is not|isn't|not) compatible with|(?:does not|doesn't) (?:work with|fit|support)|"
                           r"(?:won't|will not) (?:fit|work with)|(?:is not|isn't) (?:made|designed) for", re.I)
COMPAT_EN = re.compile(r"(?:compatible with|works with|fits|supports|(?:made|designed) for)", re.I)
# 見出し形式の列挙: 「対応プリンター：A／B」「Compatible models: X」「Fits: X」
COMPAT_HEADER = re.compile(r"(?:対応機種|対応プリンター|対応プリンタ|対応モデル|対応製品|compatible models?|fits|works with)"
                           r"\s*:\s*(?P<x>[^。]+?)(?=。|\.\s|\.$|$)", re.I)
COMPAT_JP_ONLY = re.compile(r"(?P<x>[^、。]+?)(?:専用|までの対応|まで対応)")
# 数値の主張を打ち消す否定（「120V地域では使えません」の 120V は主張にしない）
NUMERIC_NEG = re.compile(r"使えません|使用できません|対応していません|非対応|ではありません|\bnot\b|n't\b", re.I)

ING_JP_DENY = re.compile(
    r"(?P<x>[^\s、。・，,]+?)(?:不使用|フリー|は含みません|を含みません|は含まれていません|は使用していません|は使っていません|"
    r"は入っていません|は原材料に使われていません|は使われていません)")
ING_JP_NON = re.compile(r"(?:ノン|無)(?P<x>シリコン|シリコーン|香料|添加|アルコール|パラベン)")
ING_JP_AFFIRM = re.compile(r"(?:を含みます|を含む|を含んでいます|を使用しています|を使っています|を配合しています|を配合|"
                           r"は含まれます|が含まれます|は含まれています|が含まれています|が配合されています|が使われてい(?:ます|る)|"
                           r"配合(?!さ))")
EN_NEGATOR_BEFORE = re.compile(r"(?:\bnot|n't|\bnever)\s+(?:an?\s+)?$", re.I)
ING_EN_DENY = re.compile(r"\b(?P<x>[A-Za-z]+)-free\b|\bfree of (?P<y>[A-Za-z ]+?)(?=[.,;]|$)|"
                         r"\b(?:does not|doesn't) contain (?:any )?(?P<z>[A-Za-z ]+?)(?=[.,;]|$)|"
                         r"\bcontains no (?P<w>[A-Za-z ]+?)(?=[.,;]|$)", re.I)
ING_EN_AFFIRM = re.compile(r"\b(?:contains|made with)\s+(?!no\b)", re.I)

BUNDLE_JP_AFFIRM = re.compile(r"(?:が付属|も付属|を同梱|が同梱|付き|が箱に入って)")
# 見出し形式の列挙: 「同梱物：A、B」「In the box: A / B」
BUNDLE_HEADER = re.compile(r"(?:同梱物|付属品|内容物|セット内容|in the box|what's in the box|box contents)"
                           r"\s*:\s*(?P<x>[^。]+?)(?=。|\.\s|\.$|$)", re.I)
BUNDLE_JP_DENY = re.compile(r"(?P<x>[^\s、。]+?)(?:は付属しません|は付属していません|は別売|は同梱されていません)")
BUNDLE_EN_AFFIRM = re.compile(r"\bcomes with\s+(?:a |an |the )?(?P<x>[^,.;]+?)(?=,|\.|;| but |$)", re.I)
BUNDLE_EN_INCLUDED = re.compile(r"\b(?:an?|the)\s+(?P<x>[A-Za-z][A-Za-z\- ]+?)\s+(?:is|are) included", re.I)
BUNDLE_EN_DENY = re.compile(r"(?:does not|doesn't) come with\s+(?:a |an |the )?(?P<x>[^,.;]+?)(?=,|\.|;|$)|"
                            r"(?:the\s+)?(?P<y>[A-Za-z][A-Za-z\-\s]+?)\s+(?:is|are) sold separately", re.I)


@dataclass
class Span:
    start: int
    end: int
    text: str


@dataclass
class Extracted:
    claim: Claim
    span: Span
    sentence: int
    rule: str

    def to_dict(self) -> dict:
        return {"claim": self.claim.to_dict(), "span": asdict(self.span), "sentence": self.sentence, "rule": self.rule}


@dataclass
class ExtractResult:
    text: str
    sentences: list[Span]
    claims: list[Extracted] = field(default_factory=list)

    @property
    def covered(self) -> list[bool]:
        hit = {e.sentence for e in self.claims}
        return [i in hit for i in range(len(self.sentences))]

    @property
    def coverage(self) -> float:
        return sum(self.covered) / len(self.sentences) if self.sentences else 0.0


def _nfkc_map(text: str) -> tuple[str, list[int]]:
    """NFKC 後の文字 → 元テキストのオフセット"""
    out, idx = [], []
    for i, ch in enumerate(text):
        n = unicodedata.normalize("NFKC", ch)
        out.append(n)
        idx.extend([i] * len(n))
    idx.append(len(text))
    return "".join(out), idx


def split_sentences(text: str) -> list[Span]:
    spans = []
    for m in re.finditer(r"[^。！？!?\n]+[。！？!?]?", text):
        chunk, base = m.group(0), m.start()
        start = 0
        for b in re.finditer(r"(?<=[.])\s+(?=[A-Z\"(])", chunk):      # 英文の文境界
            seg = chunk[start:b.start()]
            if seg.strip():
                spans.append(Span(base + start, base + b.start(), seg))
            start = b.end()
        seg = chunk[start:]
        if seg.strip():
            spans.append(Span(base + start, base + len(chunk), seg.rstrip()))
    return spans


def split_items(t: str, start: int, end: int):
    """[start, end) を列挙の区切り（、，,・／/ と や and）で分ける。括弧の中は分けない（「イヤーピース（S/M/L）」）。"""
    depth, piece = 0, start
    i = start
    while i < end:
        ch = t[i]
        if ch in "(（[":
            depth += 1
        elif ch in ")）]":
            depth = max(0, depth - 1)
        elif depth == 0:
            m = re.match(r"[、，,・/／]|と|や(?!す)|\s+and\s+|\s+or\s+", t[i:end])
            if m and m.end() > 0:
                yield piece, i
                i += m.end()
                piece = i
                continue
        i += 1
    yield piece, end


class Extractor:
    def __init__(self, catalog=None):
        self.u = units()
        self.catalog = catalog          # あれば、手がかり語の無い数値を商品の属性（次元が一意なもの）に割り当てる
        self._dims: dict[str, list[str]] = {}

    # ───────────── 公開 API ─────────────

    def extract(self, text: str, product_ref: dict | None = None, id_prefix: str = "ex") -> ExtractResult:
        res = ExtractResult(text, split_sentences(text))
        self._dims = self._product_dims(product_ref)
        for si, sent in enumerate(res.sentences):
            norm, idx = _nfkc_map(sent.text)
            used: list[tuple[int, int]] = []
            found: list[tuple[int, int, dict, str]] = []
            for rule in (self._model, self._spf_pa, self._pack, self._numeric, self._ingredient_deny, self._bundle_deny,
                         self._compat, self._ingredient_affirm, self._bundle_affirm):
                for s, e, kw, name in rule(norm, used):
                    if any(s < ue and us < e for us, ue in used):
                        continue
                    used.append((s, e))
                    found.append((s, e, kw, name))
            for s, e, kw, name in sorted(found, key=lambda x: x[0]):
                cid = f"{id_prefix}-{si}-{len(res.claims)}"
                ctype, attr = kw["claim_type"], kw.get("attribute")
                claim = Claim(claim_id=cid, product_ref=dict(product_ref or {}), severity=severity_for(ctype, attr),
                              provenance={"kind": "agent", "generator": EXTRACTOR_VERSION}, **kw)
                a, b = sent.start + idx[s], sent.start + idx[e]
                res.claims.append(Extracted(claim, Span(a, b, text[a:b]), si, name))
        return res

    # ───────────── ルール（NFKC 済みの文に対して (start, end, claim_kwargs, rule) を返す） ─────────────

    def _model(self, t, used):
        for m in MODEL_RE.finditer(t):
            yield m.start("m"), m.end("m"), {"claim_type": "model", "value": m.group("m")}, "model_cue"

    def _spf_pa(self, t, used):
        for m in SPF_RE.finditer(t):
            yield m.start(), m.end(), {"claim_type": "numeric", "attribute": "spf", "value": int(m.group("num"))}, "spf"
        for m in PA_RE.finditer(t):
            yield m.start(), m.end(), {"claim_type": "region", "attribute": "pa_rating", "value": m.group(0)}, "pa"

    def _pack(self, t, used):
        for m in PACK_RE.finditer(t):
            n = int(next(m.group(k) for k in ("a", "b", "c") if m.group(k)))
            yield m.start(), m.end(), {"claim_type": "numeric", "attribute": "pack_count", "value": n,
                                       "unit": "pcs"}, "pack_count"

    def _product_dims(self, ref: dict | None) -> dict[str, list[str]]:
        """対象商品の数値属性を次元ごとに（カタログがある場合のみ）"""
        if not (self.catalog and ref):
            return {}
        r = self.catalog.identity.resolve(gtin=ref.get("gtin"), model=ref.get("model_number"), name=ref.get("name"))
        if not r.ok:
            return {}
        snap = self.catalog.snapshots[r.product_id]
        out: dict[str, list[str]] = {}
        for sec in ("attributes", "region_spec"):
            for attr, v in (snap.get(sec) or {}).items():
                if "unit" in v and ("min" in v or isinstance(v.get("value"), (int, float))):
                    try:
                        out.setdefault(self.u.canon(v["unit"])[0], []).append(attr)
                    except UnitError:
                        continue
        return out

    def _clause(self, t: str, s: int, e: int) -> tuple[str, str]:
        left = re.split(r"[、,;。]|\bbut\b", t[:s])[-1]
        right = re.split(r"[、,;。]|\bbut\b|\band\b", t[e:])[0]
        return left.casefold(), right.casefold()

    def _numeric(self, t, used):
        for m in NUM_RE.finditer(t):
            try:
                dim, unit = self.u.canon(m.group("unit"))
            except UnitError:
                continue
            value = float(m.group("num").replace(",", ""))
            value = int(value) if value.is_integer() else value
            left, right = self._clause(t, m.start(), m.end())
            if NUMERIC_NEG.search(right) or re.search(r"\bnot\b|n't\b|\bnever\b", left):
                continue                                              # 否定された数値は主張にしない
            attr = self._attribute(dim, left, right, t.casefold(), unit)
            if attr is None:
                continue
            ctype = "region" if attr in ("voltage", "frequency") else "numeric"
            yield m.start(), m.end(), {"claim_type": ctype, "attribute": attr, "value": value,
                                       "unit": m.group("unit").strip()}, "numeric_cue"

    def _attribute(self, dim: str, left: str, right: str, sentence: str, unit_key: str = "") -> str | None:
        if dim == "voltage":
            if any(c in sentence for c in ("battery", "バッテリー")):
                return "nominal_voltage"
            return "voltage"
        best, best_dist = None, 10 ** 6
        for attr, (dims, cues) in ATTRIBUTE_CUES.items():
            if dim not in dims:
                continue
            for c in cues:
                i = left.rfind(c)
                # 手がかり語の「終わり」から数値までの距離。同距離なら長い語（"net weight" > "weight"）を優先
                d = len(left) - (i + len(c)) - len(c) / 1000 if i >= 0 else None
                if d is not None and d < best_dist:
                    best, best_dist = attr, d
                j = right.find(c)
                if j >= 0 and j + 0.5 - len(c) / 1000 < best_dist:
                    best, best_dist = attr, j + 0.5 - len(c) / 1000
        if best:
            return best
        if dim == "count" and ("枚" in sentence or "page" in sentence):
            return "page_yield"
        by_unit = UNIT_KEY_ONLY.get(unit_key) or UNIT_ONLY.get(dim)
        if by_unit:
            return by_unit
        cands = [a for a in self._dims.get(dim, []) if a != "pack_count"]
        return cands[0] if len(cands) == 1 else None             # 商品にその次元の属性が 1 つだけなら割り当てる

    # 互換性
    def _list(self, t, m, ctype, attr, rule, polarity="affirm"):
        for a, b in split_items(t, m.start("x"), m.end("x")):
            raw = t[a:b]
            item = raw.strip().rstrip(".")
            if not item or len(item) > 60:
                continue
            s = a + raw.find(item)
            yield s, s + len(item), {"claim_type": ctype, "attribute": attr, "value": item, "polarity": polarity}, rule

    def _compat(self, t, used):
        for m in COMPAT_HEADER.finditer(t):
            yield from self._list(t, m, "compatibility", "compatible_with", "compat_header")
        for m in COMPAT_JP_ONLY.finditer(t):
            x = re.split(r"[はがをも]|では|で(?=[^A-Za-z0-9])", m.group("x"))[-1].strip()
            if x and not JP_VERB_FRAGMENT.fullmatch(x):
                yield m.end("x") - len(x), m.end("x"), {"claim_type": "compatibility", "attribute": "compatible_with",
                                                        "value": x}, "compat_jp_only"
        for m in COMPAT_EN_NEG.finditer(t):
            for s, e, item in self._en_items(t, m.end()):
                yield s, e, {"claim_type": "compatibility", "attribute": "compatible_with", "value": item,
                             "polarity": "deny"}, "compat_en_neg"
        neg_spans = [(m.start(), m.end()) for m in COMPAT_EN_NEG.finditer(t)]
        for m in COMPAT_EN.finditer(t):
            if any(s <= m.start() < e for s, e in neg_spans):
                continue
            for s, e, item in self._en_items(t, m.end()):
                yield s, e, {"claim_type": "compatibility", "attribute": "compatible_with", "value": item}, "compat_en"
        prev = 0
        for m in COMPAT_JP.finditer(t):
            pol = "deny" if m.group("neg") else "affirm"
            for s, e, item in self._jp_items(t, prev, m.start(), used):
                yield s, e, {"claim_type": "compatibility", "attribute": "compatible_with", "value": item,
                             "polarity": pol}, "compat_jp"
            prev = m.end()
        for m in COMPAT_JP_NEG2.finditer(t):
            yield m.start("x"), m.end("x"), {"claim_type": "compatibility", "attribute": "compatible_with",
                                             "value": m.group("x"), "polarity": "deny"}, "compat_jp_hi"

    def _en_items(self, t: str, pos: int):
        m = re.match(r"\s*(?:the\s+)?(?P<x>[^.;]+?)(?=\s*(?:\.|;|,\s*but|\s+but\b|\s+only\b|\s+too\b|$))", t[pos:], re.I)
        if not m:
            return
        base = pos + m.start("x")
        chunk = m.group("x")
        prev_name = None
        for part in re.finditer(r"[^,]+?(?=\s*(?:,|\band\b|\bor\b|$))", chunk):
            raw = part.group(0)
            item = re.sub(r"^\s*(?:and|or|the)\s+", "", raw).strip()
            words = item.casefold().replace("'", " ").split()
            if not item or len(words) > 6 or set(words) & EN_CLAUSE_WORDS:   # 節（"it's made in a facility…"）は品目にしない
                continue
            if re.fullmatch(r"\(\d{4}\)", item) and prev_name:          # 「BookAir 14 (2024) and (2025)」
                item = re.sub(r"\s*\(\d{4}\)$", "", prev_name) + " " + item
            s = base + part.start() + raw.find(item.split()[0]) if item.split()[0] in raw else base + part.start()
            yield s, base + part.end(), item
            prev_name = item

    def _jp_items(self, t: str, lo: int, cue: int, used):
        seg_start = lo
        for topic in JP_TOPIC:
            i = t.rfind(topic, lo, cue)
            if i >= 0:
                seg_start = max(seg_start, i + len(topic))
        for a, b in split_items(t, seg_start, cue):
            part = t[a:b]
            start = a + (len(part) - len(part.lstrip()))
            item = part.strip()
            if not item or JP_VERB_END.search(item) or JP_VERB_FRAGMENT.fullmatch(item):
                continue
            if "の" in item:                                            # 「Nova 5 Proの急速充電」→「Nova 5 Pro」
                item = item.split("の")[0]
            if not item or len(item) > 40 or JP_VERB_FRAGMENT.fullmatch(item):
                continue
            if any(start < ue and us < start + len(item) for us, ue in used):
                continue
            yield start, start + len(item), item

    # 成分
    def _ingredient_deny(self, t, used):
        for m in ING_JP_NON.finditer(t):
            yield m.start(), m.end(), {"claim_type": "ingredient", "attribute": "contains", "value": m.group("x"),
                                       "polarity": "deny"}, "ing_jp_non"
        for m in ING_JP_DENY.finditer(t):
            # 「香料・着色料は入っていません」: 同じ節の中で ・／と で並んだ前の品目も否定の対象
            lo = max(t.rfind(c, 0, m.start("x")) for c in "、。") + 1
            head = t[lo:m.start("x")]
            if head and re.search(r"[・と]$", head):
                seg_lo = lo + max((head.rfind(c) + 1 for c in "はがを"), default=0)
                for a, b in split_items(t, seg_lo, m.start("x")):
                    item = t[a:b].strip()
                    if item and not JP_VERB_FRAGMENT.fullmatch(item) and not re.search(r"\d", item):
                        yield a, a + len(item), {"claim_type": "ingredient", "attribute": "contains", "value": item,
                                                 "polarity": "deny"}, "ing_jp_deny_list"
            x = re.split(r"[はがをも]", re.sub(r"^(?:この|その)", "", m.group("x")))[-1]   # 「ダークチョコは乳成分」→「乳成分」
            if re.search(r"\d", x) or JP_VERB_END.search(x) and len(x) <= 1:
                continue
            if not x:
                continue
            if re.match(r"(?:では|じゃ)(?:ありません|ない)", t[m.end():]):   # 「パラベンフリーではありません」→ 含有
                yield m.end("x") - len(x), m.end(), {"claim_type": "ingredient", "attribute": "contains", "value": x,
                                                     "polarity": "affirm"}, "ing_jp_not_free"
                continue
            yield m.end("x") - len(x), m.end(), {"claim_type": "ingredient", "attribute": "contains", "value": x,
                                                 "polarity": "deny"}, "ing_jp_deny"
        for m in ING_EN_DENY.finditer(t):
            g = next(k for k in ("x", "y", "z", "w") if m.group(k))
            x = m.group(g).strip()
            if x.casefold() in ("hands", "battery", "gluten") and g != "x":
                continue
            if g == "x" and EN_NEGATOR_BEFORE.search(t[max(0, m.start() - 16):m.start()]):
                # 「not peanut-free」「isn't nut-free」は含有の肯定（否定の否定）
                yield m.start(), m.end(), {"claim_type": "ingredient", "attribute": "contains", "value": x,
                                           "polarity": "affirm"}, "ing_en_not_free"
                continue
            yield m.start(), m.end(), {"claim_type": "ingredient", "attribute": "contains", "value": x,
                                       "polarity": "deny"}, "ing_en_deny"

    def _ingredient_affirm(self, t, used):
        prev = 0
        for m in ING_JP_AFFIRM.finditer(t):
            for s, e, item in self._jp_items(t, prev, m.start(), used):
                yield s, e, {"claim_type": "ingredient", "attribute": "contains", "value": item}, "ing_jp_affirm"
            prev = m.end()
        for m in ING_EN_AFFIRM.finditer(t):
            for s, e, item in self._en_items(t, m.end()):
                yield s, e, {"claim_type": "ingredient", "attribute": "contains", "value": item}, "ing_en_affirm"

    # 同梱物
    def _bundle_deny(self, t, used):
        for m in BUNDLE_JP_DENY.finditer(t):
            yield m.start("x"), m.end("x"), {"claim_type": "bundle", "attribute": "includes", "value": m.group("x"),
                                             "polarity": "deny"}, "bundle_jp_deny"
        for m in BUNDLE_EN_DENY.finditer(t):
            g = "x" if m.group("x") else "y"
            raw = m.group(g).strip()
            x = re.sub(r"^(?:but\s+)?(?:the\s+)?", "", raw, flags=re.I)
            yield m.end(g) - len(x), m.end(g), {"claim_type": "bundle", "attribute": "includes", "value": x,
                                         "polarity": "deny"}, "bundle_en_deny"

    def _bundle_affirm(self, t, used):
        for m in BUNDLE_HEADER.finditer(t):
            yield from self._list(t, m, "bundle", "includes", "bundle_header")
        prev = 0
        for m in BUNDLE_JP_AFFIRM.finditer(t):
            for s, e, item in self._jp_items(t, prev, m.start(), used):
                yield s, e, {"claim_type": "bundle", "attribute": "includes", "value": item}, "bundle_jp"
            prev = m.end()
        for rx in (BUNDLE_EN_AFFIRM, BUNDLE_EN_INCLUDED):
            for m in rx.finditer(t):
                yield m.start("x"), m.end("x"), {"claim_type": "bundle", "attribute": "includes",
                                                 "value": m.group("x").strip()}, "bundle_en"


def extract(text: str, product_ref: dict | None = None, id_prefix: str = "ex", catalog=None) -> ExtractResult:
    return Extractor(catalog).extract(text, product_ref, id_prefix)


# ───────────────────────────── 評価（手書き 30 文 × 人手ラベル） ─────────────────────────────

def _same(pred: Claim, gold: dict) -> bool:
    from .identity import Relation, compare_models
    from .normalize import text_key, vocab

    if pred.claim_type != gold["type"] or pred.polarity != gold.get("polarity", "affirm"):
        return False
    t, v = gold["type"], vocab()
    if t in ("numeric", "region"):
        if pred.attribute != gold.get("attribute"):
            return False
        if isinstance(gold["value"], str):
            return text_key(pred.value) == text_key(gold["value"])
        try:
            if gold.get("unit"):
                g = units().quantity(gold["value"], gold["unit"])
                q = units().quantity(pred.value, pred.unit, g.dimension)
                return q.dimension == g.dimension and abs(q.si() - g.si()) <= 1e-9 * max(1, abs(g.si()))
            return float(pred.value) == float(gold["value"])
        except (UnitError, TypeError, ValueError):
            return False
    if t == "compatibility":
        return (v.compat_key(pred.value) == v.compat_key(gold["value"])
                or v.compat_key(pred.value, brandless=True) == v.compat_key(gold["value"], brandless=True))
    if t == "ingredient":
        return v.ingredient(pred.value).key == v.ingredient(gold["value"]).key
    if t == "bundle":
        return v.term(pred.value) == v.term(gold["value"])
    if t == "model":
        return compare_models(str(pred.value), str(gold["value"])) is Relation.SAME
    return text_key(pred.value) == text_key(gold["value"])


def evaluate_answers(path=None, catalog=None) -> dict:
    """抽出 precision / recall（類型別）、coverage、抽出 claim を verifier に通した時の誤抽出起因の BLOCK"""
    import time
    from collections import Counter

    import yaml

    from .normalize import ROOT
    from .snapshot import Catalog
    from .verify import Verifier

    path = path or ROOT / "data" / "extract" / "answers_v1.yaml"
    doc = yaml.safe_load(open(path, encoding="utf-8"))
    catalog = catalog or Catalog.from_seed()
    verifier, ex = Verifier(catalog), Extractor(catalog)
    tp, fp, fn = Counter(), Counter(), Counter()
    sentences = covered = 0
    rows, e2e = [], Counter()
    t0 = time.perf_counter()
    for a in doc["answers"]:
        p = catalog.products[a["pid"]]
        ref = {"gtin": p["gtin"]} if p.get("gtin") else {"model_number": p["model_number"]}
        res = ex.extract(a["text"], ref, id_prefix=a["id"])
        sentences += len(res.sentences)
        covered += sum(res.covered)
        gold = list(a.get("claims") or [])
        unmatched = list(range(len(gold)))
        for e in res.claims:
            hit = next((i for i in unmatched if _same(e.claim, gold[i])), None)
            r = verifier.verify_claim(e.claim)
            if hit is None:
                fp[e.claim.claim_type] += 1
                e2e["fp_" + r.outcome] += 1
                rows.append({"answer": a["id"], "kind": "誤抽出", "type": e.claim.claim_type, "value": e.claim.value,
                             "unit": e.claim.unit, "polarity": e.claim.polarity, "span": e.span.text,
                             "rule": e.rule, "verifier": f"{r.verdict}/{r.outcome}"})
            else:
                unmatched.remove(hit)
                tp[e.claim.claim_type] += 1
                e2e["tp_" + r.outcome] += 1
        for i in unmatched:
            fn[gold[i]["type"]] += 1
            rows.append({"answer": a["id"], "kind": "抽出漏れ", "type": gold[i]["type"], "value": gold[i]["value"],
                         "unit": gold[i].get("unit"), "polarity": gold[i].get("polarity", "affirm"), "span": "",
                         "rule": "", "verifier": ""})
    elapsed = time.perf_counter() - t0
    types = sorted(set(tp) | set(fp) | set(fn))
    per = {}
    for t in types:
        per[t] = {"tp": tp[t], "fp": fp[t], "fn": fn[t],
                  "precision": round(tp[t] / (tp[t] + fp[t]), 4) if tp[t] + fp[t] else None,
                  "recall": round(tp[t] / (tp[t] + fn[t]), 4) if tp[t] + fn[t] else None}
    T, F, N = sum(tp.values()), sum(fp.values()), sum(fn.values())
    return {"extractor": EXTRACTOR_VERSION, "answers": len(doc["answers"]), "synthetic": bool(doc.get("synthetic")),
            "gold_claims": T + N, "predicted": T + F,
            "overall": {"precision": round(T / (T + F), 4) if T + F else None,
                        "recall": round(T / (T + N), 4) if T + N else None},
            "by_type": per, "sentences": sentences, "covered_sentences": covered,
            "coverage": round(covered / sentences, 4) if sentences else None,
            "end_to_end": dict(e2e), "errors": rows, "ms_total": round(elapsed * 1000, 1)}


def render_eval_md(rep: dict, eval_commit: str | None, holdout: bool = False) -> str:
    def pct(x):
        return "—" if x is None else f"{x * 100:.1f}%"

    if holdout:
        head = ["# extractor v1 ホールドアウト評価（自動生成）", "",
                "> 評価文 10 件は **SYNTHETIC**（Claude Code の手書き）。extractor 完成後・実行前にコミットし、1 回だけ実行した"
                f"（`{eval_commit}`）。結果を見た後の修正はしていない。", ""]
    else:
        head = ["# extractor v1 評価（自動生成）", "",
                "> `python -m src.cli extract-eval` が生成。評価文 30 件は **SYNTHETIC**（Claude Code の手書き、実在エージェントの回答ではない）。",
                f"> 評価セット（data/extract/answers_v1.yaml）は extractor 実装前にコミット: `{eval_commit}`。",
                "> ただし実装後のデバッグでこのセットの出力を参照し、実装バグ（日本語直後の数値、助詞の切り出し等）を修正した。"
                "語彙・言い回しパターンの追加はしていないが、厳密な未見評価ではない。**未見の数字は results/extract-v1-holdout.md**。", ""]
    L = head + [
         f"- 抽出器: `{rep['extractor']}`、回答 {rep['answers']} 件、人手ラベル {rep['gold_claims']} 件、抽出 {rep['predicted']} 件",
         f"- **全体 precision {pct(rep['overall']['precision'])} / recall {pct(rep['overall']['recall'])}**",
         f"- coverage（1 件以上抽出できた文の割合）: {pct(rep['coverage'])}（{rep['covered_sentences']}/{rep['sentences']} 文。"
         "主張を含まない文もあるため 100% が正解とは限らない）",
         f"- 処理時間: {rep['ms_total']} ms（{rep['answers']} 回答）", "",
         "| 類型 | TP | FP（誤抽出） | FN（抽出漏れ） | precision | recall |", "|---|---|---|---|---|---|"]
    for t, v in rep["by_type"].items():
        L.append(f"| {t} | {v['tp']} | {v['fp']} | {v['fn']} | {pct(v['precision'])} | {pct(v['recall'])} |")
    e = rep["end_to_end"]
    L += ["", "## 抽出 → verifier（合成 seed カタログ）", "",
          f"- 正しく抽出した claim の判定: {', '.join(f'{k[3:]}={v}' for k, v in sorted(e.items()) if k.startswith('tp_'))}",
          f"- 誤抽出 claim の判定: {', '.join(f'{k[3:]}={v}' for k, v in sorted(e.items()) if k.startswith('fp_')) or 'なし'}",
          f"- **誤抽出が原因の BLOCK: {e.get('fp_BLOCK', 0)} 件**、WARN: {e.get('fp_WARN', 0)} 件", "",
          "## 誤抽出・抽出漏れ", "", "| 回答 | 種別 | 類型 | 値 | スパン | ルール | verifier |", "|---|---|---|---|---|---|---|"]
    for r in rep["errors"]:
        val = f"{'NOT ' if r['polarity'] == 'deny' else ''}{r['value']}{' ' + r['unit'] if r['unit'] else ''}"
        L.append(f"| {r['answer']} | {r['kind']} | {r['type']} | {val} | {r['span']} | {r['rule']} | {r['verifier']} |")
    return "\n".join(L) + "\n"


def render_v2_report(sets: dict, v1_refs: dict, commits: dict) -> str:
    """3 セット（v1 開発 30 文 / v1 holdout＝開発扱い / v2 holdout＝未見・1 回のみ）の比較"""
    def pct(x):
        return "—" if x is None else f"{x * 100:.1f}%"

    L = ["# extractor v2 — 3 セット評価（自動生成）", "",
         "> 回答文はすべて **SYNTHETIC**（Claude Code の手書き、実在エージェントの回答ではない）。",
         f"> v2 holdout（data/extract/answers_holdout_v2.yaml）は extractor に触れる前にコミット（`{commits.get('v2_holdout')}`）し、"
         "v2 完成後に 1 回だけ実行した。結果を見た後の調整はしていない（この Phase では行わない）。", "",
         "| セット | 位置づけ | 抽出器 | precision | recall | coverage | 誤抽出起因の BLOCK |", "|---|---|---|---|---|---|---|"]
    names = {"v1_dev": ("v1 開発 30 文", "開発（v1・v2 とも参照）"),
             "v1_holdout": ("v1 holdout 10 文", "Phase 2 の未見 → 今回から開発扱い"),
             "v2_holdout": ("**v2 holdout 15 文**", "**未見（1 回のみ）**")}
    for key, (title, role) in names.items():
        if key in v1_refs:
            r = v1_refs[key]
            L.append(f"| {title} | {role} | v1（Phase 2 記録） | {pct(r['overall']['precision'])} | "
                     f"{pct(r['overall']['recall'])} | {pct(r['coverage'])} | {r['end_to_end'].get('fp_BLOCK', 0)} |")
        r = sets[key]
        L.append(f"| {title} | {role} | **v2** | {pct(r['overall']['precision'])} | {pct(r['overall']['recall'])} | "
                 f"{pct(r['coverage'])} | {r['end_to_end'].get('fp_BLOCK', 0)} |")
    h = sets["v2_holdout"]
    L += ["", "## v2 holdout の類型別", "", "| 類型 | TP | FP | FN | precision | recall |", "|---|---|---|---|---|---|"]
    for t, v in h["by_type"].items():
        L.append(f"| {t} | {v['tp']} | {v['fp']} | {v['fn']} | {pct(v['precision'])} | {pct(v['recall'])} |")
    e = h["end_to_end"]
    L += ["", f"抽出 → verifier: 正しい抽出 {', '.join(f'{k[3:]}={v}' for k, v in sorted(e.items()) if k.startswith('tp_'))}／"
          f"誤抽出 {', '.join(f'{k[3:]}={v}' for k, v in sorted(e.items()) if k.startswith('fp_')) or 'なし'}", "",
          "## v2 holdout の誤抽出・抽出漏れ（Phase 4 以降のバックログ。この Phase では直さない）", "",
          "| 回答 | 種別 | 類型 | 値 | スパン | ルール | verifier |", "|---|---|---|---|---|---|---|"]
    for r in h["errors"]:
        val = f"{'NOT ' if r['polarity'] == 'deny' else ''}{r['value']}{' ' + r['unit'] if r['unit'] else ''}"
        L.append(f"| {r['answer']} | {r['kind']} | {r['type']} | {val} | {r['span']} | {r['rule']} | {r['verifier']} |")
    L += ["", "## 注意", "",
          "- v1 holdout の h07「It's not peanut-free」は Phase 2 のラベルでは主張なし。v2 は規約どおり含有（affirm）として抽出するため、"
          "v1 holdout 上は誤抽出 1 件として数えられている（ラベル規約の差。ラベルは書き換えていない）。",
          "- v2 では、手がかり語の無い数値を「対象商品にその次元の属性が 1 つだけ」の場合に限り割り当てる（カタログ参照）。", ""]
    return "\n".join(L) + "\n"
