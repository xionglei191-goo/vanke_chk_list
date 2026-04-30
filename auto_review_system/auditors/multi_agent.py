import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from auditors.agents.scheme_agents import (agent1_prep, agent2_tech, agent3_acceptance, 
                                           agent4_safety, agent5_warranty, agent6_schedule, agent7_interface, agent8_boq_reverse_check_scheme)
from auditors.agents.cost_agents import agent9_completeness, agent10_feature_match, agent11_brand_contract
from auditors.agents.cross_check_agents import agent11_forward_check, agent13_cost_reverse_check
from auditors.engineering_auditor import call_llm, is_llm_runtime_failure
from utils.cost_controls import agent_routing_enabled, max_scheme_agents, triage_mode

# 方案特工并行度（受底层 QPS 限流器保护，不会超）
_PARALLEL_WORKERS = int(os.getenv("AGENT_PARALLEL_WORKERS", "4"))
logger = logging.getLogger(__name__)

_ENGINEERING_KEYWORDS = (
    "施工", "工艺", "材料", "安全", "验收", "保修", "工期", "拆除", "安装", "防水",
    "维修", "修复", "改造", "混凝土", "钢筋", "管道", "涂料", "消防", "玻璃",
    "外墙", "屋面", "天面", "地面", "质量", "试验", "报价", "清单",
)
_NOISE_HEADINGS = ("目录", "前言", "编制说明", "公司简介", "封面", "附录", "授权委托")


def _summarize_agent_labels(agent_labels):
    unique_labels = []
    for label in agent_labels:
        if label not in unique_labels:
            unique_labels.append(label)
    if not unique_labels:
        return ""
    if len(unique_labels) <= 3:
        return "、".join(unique_labels)
    return f"{'、'.join(unique_labels[:3])} 等 {len(unique_labels)} 个智能体"


def _build_runtime_failure_notice(agent_labels, scope="本章节"):
    agents = _summarize_agent_labels(agent_labels)
    return (
        f"⚠️ {scope}有部分审查智能体未能完成（{agents}）。"
        "原因：模型服务超时或暂时不可用。"
        f"{scope}结果可能不完整，建议稍后重试。"
    )

def local_triage_chunk(chunk_heading, chunk_text):
    text = f"{chunk_heading}\n{chunk_text}".strip()
    compact = "".join(text.split())
    if len(compact) < 10:
        return False
    if any(noise in chunk_heading for noise in _NOISE_HEADINGS) and not any(
        kw in compact for kw in _ENGINEERING_KEYWORDS
    ):
        return False
    # Conservative default: pass through if uncertain, because missing a real
    # engineering issue is worse than spending a few audit calls.
    return True if any(kw in compact for kw in _ENGINEERING_KEYWORDS) else len(compact) >= 80


def triage_chunk(chunk_heading, chunk_text):
    mode = triage_mode()
    if mode == "off":
        return True
    if mode == "local":
        return local_triage_chunk(chunk_heading, chunk_text)

    prompt = f"""
    你是万科工程资料【预审分发哨兵】。
    判断以下施工方案片段，是否包含实质性的工程信息（如工艺、材料、进度、安全、合同、验收、保修、造价等）。
    如果该片段纯粹是空词空话、编制依据说明、公司简介、地理位置介绍或无实质内容的标题，请直接判定为“拦截”。
    如果包含任何上述实质性工程要素，请判定为“放行”。
    请直接且仅输出“放行”或“拦截”，绝不准输出其他解释字符。
    
    片段标题：{chunk_heading}
    片段内容：{chunk_text}
    """
    res = call_llm(prompt, "鉴定开始").strip()
    if is_llm_runtime_failure(res):
        logger.warning("Triage fallback to pass-through for [%s]: %s", chunk_heading, res)
        return True
    return "放行" in res


def _contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def _selected_scheme_agents(heading, chunk_text):
    all_agents = [
        (agent1_prep, "Agent 1 [施工准备]", ("进场", "人员", "材料", "机具", "设备", "报验", "资质", "持证", "准备")),
        (agent2_tech, "Agent 2 [施工工艺]", ()),
        (agent3_acceptance, "Agent 3 [验收标准]", ()),
        (agent4_safety, "Agent 4 [安全管理]", ("安全", "高空", "高处", "临电", "动火", "脚手架", "吊篮", "拆除", "切割", "消防", "围蔽", "防坠", "警戒", "洞口", "临边")),
        (agent5_warranty, "Agent 5 [保修防卫]", ("保修", "质保", "防水", "渗漏", "渗水")),
        (agent6_schedule, "Agent 6 [工期折算]", ("工期", "进度", "养护", "节点", "计划", "天内", "日历天")),
        (agent7_interface, "Agent 7 [合同界面]", ("界面", "移交", "垃圾", "成品保护", "恢复", "拆改", "交叉", "责任", "清运")),
        (agent8_boq_reverse_check_scheme, "Agent 8 [标准反查方案]", ("清单", "报价", "计量", "工程量", "定额", "项目特征", "漏项")),
    ]
    if not agent_routing_enabled():
        return [(fn, label) for fn, label, _ in all_agents]

    text = f"{heading}\n{chunk_text}"
    fixed = [(agent2_tech, "Agent 2 [施工工艺]"), (agent3_acceptance, "Agent 3 [验收标准]")]
    optional = []
    for fn, label, keywords in all_agents:
        if label in {"Agent 2 [施工工艺]", "Agent 3 [验收标准]"}:
            continue
        if _contains_any(text, keywords):
            optional.append((fn, label))

    priority = {
        "Agent 4 [安全管理]": 0,
        "Agent 1 [施工准备]": 1,
        "Agent 7 [合同界面]": 2,
        "Agent 5 [保修防卫]": 3,
        "Agent 6 [工期折算]": 4,
        "Agent 8 [标准反查方案]": 5,
    }
    optional.sort(key=lambda item: priority.get(item[1], 99))

    selected = fixed + optional
    deduped = []
    seen = set()
    for fn, label in selected:
        if label in seen:
            continue
        seen.add(label)
        deduped.append((fn, label))
    return deduped[:max_scheme_agents()]


def _should_run_forward_check(heading, chunk_text, global_cost_context):
    if not global_cost_context.strip():
        return False
    text = f"{heading}\n{chunk_text}"
    priced_action_keywords = (
        "拆除", "安装", "更换", "新增", "维修", "修复", "改造", "涂刷", "铺贴",
        "开挖", "浇筑", "台班", "材料", "设备", "防水", "管道", "门", "玻璃",
        "清运", "恢复", "制作",
    )
    return _contains_any(text, priced_action_keywords)

def run_linear_pipeline(chunks_ready_for_agents, project_name, global_cost_context="", progress_callback=None, status_check_callback=None):
    """
    线性执行管线：串行调用 13 个微智能体，并支持 AI 哨兵拦截与探员静默协议过滤。
    """
    final_output = []
    had_runtime_failures = False
    
    # 统计真正需要过方案特工的切片数
    valid_chunks = [c for c in chunks_ready_for_agents if len(c['text'].strip()) >= 10]
    total_chunks = len(valid_chunks)
    current_chunk_idx = 0
    
    global_reports = []
    if global_cost_context:
        global_failed_agents = []
        if progress_callback: progress_callback("🟢 正在呼叫造价防线：执行全局造价清单核验特工...", 0.05)
        # Group B
        r9 = agent9_completeness(global_cost_context, "参照国家定额规费强制扣取标准", project_name)
        if is_llm_runtime_failure(r9):
            had_runtime_failures = True
            global_failed_agents.append("Agent 9 [造价齐备度]")
        elif "[PASS]" not in r9:
            global_reports.append({"agent": "Agent 9 [造价齐备度]", "heading": "全局造价清单", "result": r9})
        
        r10 = agent10_feature_match(global_cost_context, "参照企业材料下限特刊", project_name)
        if is_llm_runtime_failure(r10):
            had_runtime_failures = True
            global_failed_agents.append("Agent 10 [造价特征核验]")
        elif "[PASS]" not in r10:
            global_reports.append({"agent": "Agent 10 [造价特征核验]", "heading": "全局造价清单", "result": r10})
        
        r11 = agent11_brand_contract(global_cost_context, "参照A级集采品牌库", project_name)
        if is_llm_runtime_failure(r11):
            had_runtime_failures = True
            global_failed_agents.append("Agent 11 [品牌/合同违约]")
        elif "[PASS]" not in r11:
            global_reports.append({"agent": "Agent 11 [品牌/合同违约]", "heading": "全局造价清单", "result": r11})
        
        # Group C (Standard -> Cost)
        r13 = agent13_cost_reverse_check(global_cost_context, "参照国家强制地下防水/结构等核心极限数值", project_name)
        if is_llm_runtime_failure(r13):
            had_runtime_failures = True
            global_failed_agents.append("Agent 13 [造价反向查漏]")
        elif "[PASS]" not in r13:
            global_reports.append({"agent": "Agent 13 [造价反向查漏]", "heading": "全局造价清单", "result": r13})

        if global_failed_agents:
            global_reports.append({
                "agent": "系统审计中枢",
                "heading": "全局造价清单",
                "result": _build_runtime_failure_notice(global_failed_agents, scope="本次全局造价核验"),
            })
        
    for chunk in valid_chunks:
        chunk_text = chunk['text']
        heading = chunk['heading']
        rules = chunk['rules']
        
        current_chunk_idx += 1
        
        if progress_callback:
            progress_ratio = 0.1 + 0.9 * (current_chunk_idx / max(1, total_chunks))
            progress_callback(f"🔍 启动 8 维反渗透阵列：剖析文档切片 ({current_chunk_idx}/{total_chunks}) - 【{heading}】", progress_ratio)
            
        if status_check_callback:
            status = status_check_callback()
            while status == 'PAUSED':
                time.sleep(3)
                if progress_callback: progress_callback(f"⏸️ 人工挂起中：等候调度... ({current_chunk_idx}/{total_chunks})", progress_ratio)
                status = status_check_callback()
            if status == 'CANCELLED':
                if progress_callback: progress_callback("⛔ 人工叫停：流水线紧急停转", progress_ratio)
                break
                
        # 哨兵预审拦截
        if chunk.get("parser_source") == "pageindex":
            if progress_callback:
                progress_callback(f"🌳 PageIndex 语义节点已通过结构化预筛：【{heading}】，跳过哨兵废话拦截。", progress_ratio)
        elif not triage_chunk(heading, chunk_text):
            if progress_callback:
                progress_callback(f"🛡️ 哨兵预审拦截：【{heading}】被判定为无实质内容的废话段落，已丢弃。", progress_ratio)
            continue
            
        chunk_reports = []
        failed_agents = []
        
        # Group A (Scheme Auditing - 8 Agents, 并行执行)
        _scheme_agents = _selected_scheme_agents(heading, chunk_text)
        
        def _run_agent(agent_fn_label):
            fn, label = agent_fn_label
            result = fn(heading, chunk_text, rules, project_name)
            return label, result
        
        with ThreadPoolExecutor(max_workers=min(8, _PARALLEL_WORKERS)) as pool:
            futures = [pool.submit(_run_agent, (fn, label)) for fn, label in _scheme_agents]
            for future in futures:
                label, result = future.result()
                if is_llm_runtime_failure(result):
                    had_runtime_failures = True
                    failed_agents.append(label)
                    continue
                if "[PASS]" not in result:
                    chunk_reports.append({"agent": label, "heading": heading, "result": result})
        
        # Group C (Scheme Cross Checks)
        if _should_run_forward_check(heading, chunk_text, global_cost_context):
            r12 = agent11_forward_check(heading, chunk_text, global_cost_context, project_name)
            if is_llm_runtime_failure(r12):
                had_runtime_failures = True
                failed_agents.append("Agent 12 [正向查漏项]")
            elif "[PASS]" not in r12:
                chunk_reports.append({"agent": "Agent 12 [正向查漏项]", "heading": heading, "result": r12})

        if failed_agents:
            chunk_reports.append({
                "agent": "系统审计中枢",
                "heading": heading,
                "result": _build_runtime_failure_notice(failed_agents),
            })
            
        final_output.extend(chunk_reports)
        time.sleep(0.5) # API限流保护
        
    final_output.extend(global_reports)
    
    # 若某一切片所有特工都PASS了，且没有全局意见，提供兜底说明
    if not final_output:
        if had_runtime_failures:
            final_output.append({
                "agent": "系统审计中枢",
                "heading": "全局审计结论",
                "result": "⚠️ 本次审查未生成完整结论：执行过程中模型服务出现超时或暂时不可用，建议稍后重试该任务。",
            })
        else:
            final_output.append({"agent": "系统审计中枢", "heading": "全局审计结论", "result": "✅ 🎉 完美！经 13 名超级特工与哨兵清洗多维核查，该文档未见任何违规、漏项、弱化标准或商务风险隐患！建议直接通过。"})
        
    # 转换为按 Heading 聚类格式，平滑对接原生卡片 UI
    grouped = {}
    for item in final_output:
        h = item['heading']
        if h not in grouped:
            grouped[h] = []
        grouped[h].append(item)
        
    return grouped
