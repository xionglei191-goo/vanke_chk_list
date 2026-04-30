import os
import json
import uuid

CORRECTION_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "correction_cases.json")

def record_correction(agent_name, chunk_heading, wrong_result, correction_text):
    """
    记录一次大模型的幻觉或误判，作为日后的反转“教材”。
    """
    cases = []
    if os.path.exists(CORRECTION_DB_PATH):
        try:
            with open(CORRECTION_DB_PATH, 'r', encoding='utf-8') as f:
                cases = json.load(f)
        except Exception:
            pass
            
    record = {
        "id": f"ERR_{uuid.uuid4().hex[:8].upper()}",
        "agent": agent_name,
        "heading": chunk_heading,
        "wrong_result": wrong_result,
        "correction_text": correction_text
    }
    cases.append(record)
    
    # 确保 data 目录存在
    os.makedirs(os.path.dirname(CORRECTION_DB_PATH), exist_ok=True)
    with open(CORRECTION_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(cases, f, ensure_ascii=False, indent=4)
        
    return True

def get_correction_cases(agent_name=None):
    """
    取出历史所有的纠正教材。用于拼接到 Prompt 中。
    """
    if not os.path.exists(CORRECTION_DB_PATH):
        return []
        
    try:
        with open(CORRECTION_DB_PATH, 'r', encoding='utf-8') as f:
            cases = json.load(f)
            if agent_name:
                return [c for c in cases if c['agent'] == agent_name]
            return cases
    except Exception:
        return []

def format_few_shot_prompt(agent_name):
    """
    生成警示性的历史翻车案例 Prompt
    """
    cases = get_correction_cases(agent_name)
    if not cases:
        return ""
        
    # 为防止 Prompt 无限膨胀，限制最多取近期的 3 个教训
    recent_cases = cases[-3:]
    
    prompt_str = "\\n【🚨🚨 历史违纪红牌警告（血淋淋的教训）】\\n"
    prompt_str += "在过去的审查中，你作为该角色曾经犯过严重的越权或幻觉错误。真正的专家总监狠狠地驳回并纠正了你。\\n"
    prompt_str += "请你像敬畏生命一样吸取以下纠错指示，在本次生成的报告中【绝对不要重蹈覆辙】：\\n"
    
    for i, case in enumerate(recent_cases, 1):
        prompt_str += f"\\n教训案例 {i}:\\n"
        prompt_str += f"▶️ 你的荒谬言论（绝对不要再次输出类似内容）：{case['wrong_result'][:100]}...\\n"
        prompt_str += f"✅ 专家的终极指导（必须严格遵循此逻辑）：{case['correction_text']}\\n"
        
    prompt_str += "\\n（如果再犯上述错误，系统将予以硬性阻断故障。）\\n"
    return prompt_str
