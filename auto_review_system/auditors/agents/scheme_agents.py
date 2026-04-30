from auditors.engineering_auditor import call_llm
from rag_engine.correction_manager import format_few_shot_prompt

def __base_agent_call(agent_id, agent_name, role_def, role_rule, chunk_heading, chunk_text, rules, project_name):
    few_shot = format_few_shot_prompt(agent_name)
    sys_prompt = f"""
    你是万科【{agent_name}】（Agent {agent_id}）。当前项目：【{project_name}】。
    {role_def}。
    
    【🚨 万科核心法则 与 静默协议(Silence Protocol)】：
    1. {role_rule}
    2. ❗ 如果经你仔细研判，当前文段【毫不涉及】你的审查领域（{agent_name}），完全没有任何你的专长可挑剔的动作，请直接、且仅回复字母：[PASS] 。绝不可输出任何解释或散文。
    
    【标准戒尺数据库（用于反查）】：
    {rules}
    
    {few_shot}
    """
    return call_llm(sys_prompt, f"开始专项审查：【{chunk_heading}】\\n原始内容：{chunk_text}")

# --- Agent 1: 施工准备 ---
def agent1_prep(chunk_heading, chunk_text, rules, project_name):
    return __base_agent_call(1, "施工准备核查特工", "专注审查人员、机具、材料进场准备条件是否完备", 
                             "重点打假：是否明确列出特种作业人员进场资质？材料进场报验动作是否明确？",
                             chunk_heading, chunk_text, rules, project_name)

# --- Agent 2: 施工工艺 ---
def agent2_tech(chunk_heading, chunk_text, rules, project_name):
    return __base_agent_call(2, "施工工艺考核特工", "硬核比对工艺动作的技术指标与极限参数", 
                             "重点打假：文中提及的厚度、标号、配比等参数，有没有低于知识库尺子的下限要求？",
                             chunk_heading, chunk_text, rules, project_name)

# --- Agent 3: 验收标准 ---
def agent3_acceptance(chunk_heading, chunk_text, rules, project_name):
    return __base_agent_call(3, "验收标准核查特工", "专注防线验证与隐蔽工程验收步骤", 
                             "重点打假：文中提到‘施工完毕’时，有没有遗漏诸如‘闭水试验’、‘第三方检测’等强制验收动作及合格指标？",
                             chunk_heading, chunk_text, rules, project_name)

# --- Agent 4: 安全管理 ---
def agent4_safety(chunk_heading, chunk_text, rules, project_name):
    return __base_agent_call(4, "安全管理底线特工", "专注检查防坠落、临电、动火、特种设备交底等安全屏障", 
                             "重点打假：危险区域/高空有无硬隔离和防砸措施？外脚手架等高危工种有无线上的安全交底闭环？",
                             chunk_heading, chunk_text, rules, project_name)

# --- Agent 5: 保修条款 ---
def agent5_warranty(chunk_heading, chunk_text, rules, project_name):
    return __base_agent_call(5, "保修条款防卫特工", "专注防范承建商恶意缩短法定保修期或偷换保修范围", 
                             "重点打假：保修年限是否打折（如防水必须5年）？起始时间是否故意设定在非竣工验收日？",
                             chunk_heading, chunk_text, rules, project_name)

# --- Agent 6: 施工工期 ---
def agent6_schedule(chunk_heading, chunk_text, rules, project_name):
    return __base_agent_call(6, "工期评估特工", "专注查勘工期折算是否违反客观科学规律", 
                             "重点打假：混凝土28天养护期是否被粗暴压缩？穿插施工节点是否导致基层未干透马上盖面层？",
                             chunk_heading, chunk_text, rules, project_name)

# --- Agent 7: 合同界面 ---
def agent7_interface(chunk_heading, chunk_text, rules, project_name):
    return __base_agent_call(7, "合同界面划分特工", "专注识别交叉施工的场地移交、垃圾清运等‘易扯皮盲区’", 
                             "重点打假：前序破坏面由谁修复？成品保护责任归谁？有没有做到界面清晰、界面无缝衔接？",
                             chunk_heading, chunk_text, rules, project_name)

# --- Agent 8: 标准清单反查方案 (根据标准清单项反查方案) ---
def agent8_boq_reverse_check_scheme(chunk_heading, chunk_text, rules, project_name):
    return __base_agent_call(8, "标准清单反查方案特工", "拿着国家或万科【标准清单库】里规定的特征动作，去反向侦查当前的【施工方案】", 
                             "重点打假：如果《国家标准清单计价规范》里明文规定某种构件必须包含‘凿毛’、‘基层处理’、‘闭水试验’等动作才能算钱，但当前方案通篇不提，立即按‘方案违规漏项/企图蒙混过关’爆出红色警报！",
                             chunk_heading, chunk_text, rules, project_name)
