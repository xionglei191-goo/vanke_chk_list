from auditors.engineering_auditor import call_llm
from rag_engine.correction_manager import format_few_shot_prompt

def __base_cost_agent(agent_id, agent_name, role_desc, focal_point, excel_context, rules, project_name):
    if not excel_context.strip():
        return "✅ 未提供关联清单，跳过核查。"
    few_shot = format_few_shot_prompt(agent_name)
    sys_prompt = f"""
    你是万科【{agent_name}】特工（Agent {agent_id}）。当前项目：【{project_name}】。
    你的职责极其单一且明确：【{role_desc}】。
    你只对下方的《待审报价清单》负责！
    
    【核心核查维度】：
    {focal_point}
    
    【静默协议 Silence Protocol】：
    ❗ 如果经你仔细研判，当前清单在你负责的维度没有任何漏项、降配或违约风险，彻底合规，请直接、且仅回复字母：[PASS] 。绝不可输出任何解释或散文。
    
    【万科/国家定额规范尺度库】：
    {rules}
    
    【待审报价清单（Excel明细数据）】：
    {excel_context}
    
    {few_shot}
    """
    return call_llm(sys_prompt, f"开始专注审查报价清单，执行 {agent_name} 协议。")

# --- Agent 9: 清单齐备度 ---
def agent9_completeness(excel_context, rules, project_name):
    return __base_cost_agent(9, "清单齐备度特工", "硬算子目完整度、规费/措施费有无缺漏",
                             "检查清单是否漏算了国家强制单列的‘安全文明施工费’、‘超高降效费’或‘大型机械进出场费’？",
                             excel_context, rules, project_name)

# --- Agent 10: 特征核验 ---
def agent10_feature_match(excel_context, rules, project_name):
    return __base_cost_agent(10, "清单特征核验特工", "专查清单项目特征描里的工艺与材质定额是否涉嫌以次充好或含混不清",
                             "对于给出的单价条目，它的材质/厚度描述是不是低于了知识库下方的定额底线？",
                             excel_context, rules, project_name)

# --- Agent 11: 品牌合同 ---
def agent11_brand_contract(excel_context, rules, project_name):
    return __base_cost_agent(11, "合同品牌审查特工", "复核材料品牌是否符合集采大牌、有无狸猫换太子",
                             "若清单里未明确写清核心大材品牌名称，或选用劣质无名牌子，立即警告涉嫌合同违约！",
                             excel_context, rules, project_name)
