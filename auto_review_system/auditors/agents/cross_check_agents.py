from auditors.engineering_auditor import call_llm
from rag_engine.correction_manager import format_few_shot_prompt

# --- Agent 11: 正向 方案->清单 ---
def agent11_forward_check(chunk_heading, chunk_text, excel_context, project_name):
    if not excel_context.strip():
         return "✅ 无关联清单文件，正向漏项比对跳过。"
    few_shot = format_few_shot_prompt("正向漏项追击特工")
    sys_prompt = f"""
    你是万科【正向漏项追击特工】（Agent 11）。当前项目：【{project_name}】。
    你的唯一任务：找出阴阳合同和未来签证的导火索！
    对比下方的【施工方案动作】和【报价清单明细】。
    
    【核心核查维度】：
    如果在施工方案里大张旗鼓地吹嘘了某种高价工艺、大中型机械使用、或特殊的脚手架防护，但在下方的清单里根本找不到这笔钱。
    立即发出【🔴 漏项/签证前置警报：要求清单予以增补明细】！
    
    【施工方案(要做的事)】：
    [{chunk_heading}]
    {chunk_text}
    
    【报价清单(给的钱)】：
    {excel_context}
    
    【防幻觉警告 与 静默协议(Silence Protocol)】：
    ❗ 如果方案动作已在清单中完全标价无遗漏，未发现任何阴阳合同或漏项风险，请直接、且仅回复：[PASS] 。绝不可输出任何解释或散文。
    
    {few_shot}
    """
    return call_llm(sys_prompt, "请开始交叉比对 (方案 ➔ 清单)！")

# --- Agent 13: 反查 标准->清单 ---
def agent13_cost_reverse_check(excel_context, rules, project_name):
    if not excel_context.strip():
         return "✅ 无关联清单文件，造价底线比对跳过。"
    few_shot = format_few_shot_prompt("造价反向定额特工")
    sys_prompt = f"""
    你是万科【造价反向定额特工】（Agent 13）。当前项目：【{project_name}】。
    拿着【知识库定额底线】，去衡量整个【报价清单】里的材料和做法参数是不是在以次充好！
    
    【知识库定额底线(强条)】：
    {rules}
    
    【待审报价清单明细】：
    {excel_context}
    
    【核心核查维度】：
    如果发现清单里防水写了1.0mm厚度，而底线是2.0mm厚度；或者清单里列的是低级C20水泥，底线要求是抗渗微膨胀；
    立即发出【🔴 定额特征击穿下限警报：涉嫌串通减配】！
    
    【静默协议 Silence Protocol】：
    ❗ 如果找不出明显违反数字下限的东西，清单特征数值未跌穿企业定额底线，请直接、且仅回复：[PASS] 。绝不可输出任何解释或散文。
    
    {few_shot}
    """
    return call_llm(sys_prompt, "请开始交叉比对 (标准 ➔ 清单)！")
