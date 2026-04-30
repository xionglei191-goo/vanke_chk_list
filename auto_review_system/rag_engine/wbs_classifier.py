"""
Zero-cost WBS classifier.

This module intentionally avoids LLM calls. It maps source names, headings and
rule text to a small set of high-value GB50300 WBS nodes with conservative
confidence. Low-confidence items remain "通用".
"""
import re

RULES = [
    ("01-08-01", 3, ("地下防水", "地下室防水", "底板防水", "基坑防水", "防水混凝土", "止水带")),
    ("04-03-01", 3, ("屋面卷材", "天面卷材", "卷材防水", "防水卷材", "sbs")),
    ("04-03-02", 3, ("屋面涂膜", "天面涂膜", "涂膜防水", "防水涂料", "911", "聚氨酯防水")),
    ("04-01-04", 3, ("屋面保护层", "天面保护层", "找平层", "找坡层")),
    ("03-03-02", 3, ("外墙防水", "外墙渗漏", "外墙渗水", "墙面防水")),
    ("03-10-01", 3, ("涂料", "乳胶漆", "墙面翻新", "油漆翻新", "水性涂料")),
    ("03-02-01", 2, ("抹灰", "批荡", "砂浆找平")),
    ("03-08-02", 3, ("外墙砖", "瓷砖脱落", "瓷片脱落", "饰面砖")),
    ("03-04-04", 3, ("防火门", "特种门")),
    ("03-04-05", 3, ("玻璃安装", "夹胶玻璃", "钢化玻璃", "门窗玻璃")),
    ("03-01-02", 3, ("地坪", "地面", "路面", "环氧", "硬化", "整体面层")),
    ("03-12-04", 3, ("护栏", "栏杆", "扶手")),
    ("05-01-01", 3, ("给水管", "供水管", "生活水", "水泵房", "管网改造")),
    ("05-01-03", 3, ("消火栓", "消防管", "消防箱")),
    ("05-01-04", 3, ("喷淋", "消防喷淋")),
    ("05-02-01", 3, ("排水管", "排污管", "雨水管", "污水管", "排水沟")),
    ("06-01-03", 3, ("风管", "通风", "排风", "空调风")),
    ("08-16-03", 3, ("监控", "摄像头", "高空抛物", "安防", "人脸识别")),
    ("08-15-04", 3, ("消防主机", "火灾报警", "报警控制器")),
    ("07-04-06", 2, ("导管", "线管", "穿线", "电缆", "临电")),
    ("02-01-04", 3, ("混凝土", "浇筑", "楼板", "后浇带", "c30")),
    ("02-01-02", 3, ("钢筋", "植筋")),
    ("02-02-01", 3, ("砌体", "砌筑", "砖墙")),
]

NOISE = re.compile(r"\s+")


def classify_wbs(text="", category="", heading="", min_confidence=3):
    haystack = NOISE.sub("", f"{category}\n{heading}\n{text}".lower())
    best = ("通用", 0, "")
    for code, weight, keywords in RULES:
        matched = [kw for kw in keywords if kw.lower() in haystack]
        if not matched:
            continue
        confidence = min(5, weight + len(matched) - 1)
        if confidence > best[1]:
            best = (code, confidence, "、".join(matched[:4]))
    if best[1] < min_confidence:
        return "通用", best[1], best[2]
    return best


def classify_rule(rule, min_confidence=3):
    return classify_wbs(
        text=rule.get("content") or rule.get("summary") or rule.get("full_text") or "",
        category=rule.get("category") or rule.get("source_file") or "",
        heading=rule.get("node_title") or "",
        min_confidence=min_confidence,
    )
