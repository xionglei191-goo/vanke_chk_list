import os
import time
import json
import uuid
import sys
import base64
import logging

# Ensure correct PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag_engine.queue_manager import init_db, get_pending_task, update_task_status, get_task_status
from rag_engine.vector_store import retrieve_rules
from auditors.multi_agent import run_linear_pipeline
from auditors.repair_scheme_engine import run_repair_pipeline
from utils.exporter import markdown_to_docx
from auditors.engineering_auditor import analyze_vision_wbs

from parsers.word_parser import parse_word_doc_structured, parse_word_as_cost_context
from parsers.pdf_parser import parse_pdf_structured, parse_pdf_as_cost_context
from parsers.excel_parser import parse_excel_bill, parse_excel_as_scheme_chunks
from utils.paths import RESULTS_DIR, resolve_runtime_path


def _audit_engine():
    return os.getenv("AUDIT_ENGINE", "v2_repair").strip().lower()


def _cost_review_mode():
    return os.getenv("COST_REVIEW_MODE", "explicit").strip().lower()


def _is_cost_like_file(file_path):
    name = os.path.basename(str(file_path or ""))
    if "施工方案" in name or "工程方案" in name:
        return False
    return any(keyword in name for keyword in ("报价", "清单", "白单", "预算", "结算"))


def _should_extract_cost_context(file_path, file_type):
    if file_type == "cost":
        return True
    mode = _cost_review_mode()
    if mode == "off":
        return False
    if mode == "explicit":
        return _is_cost_like_file(file_path)
    return file_type == "hybrid" or _is_cost_like_file(file_path)

def main_loop():
    logger = logging.getLogger("agent_worker")
    logger.info("🚀 夜间离线审图加工厂 (Daemon Worker) 启动...")
    init_db()
    
    while True:
        task = get_pending_task()
        if not task:
            time.sleep(15)
            continue
            
        task_id = task['task_id']
        project_name = task['project_name']
        logger.info(f"📥 认领新任务: {task_id} ({project_name})")
        
        # 标记为运行中
        update_task_status(task_id, 'RUNNING')
        
        try:
            file_paths = json.loads(task['uploaded_files'])
            chunks_ready_for_agents = []
            global_cost_context = ""
            global_vision_reports = []
            
            logger.info("开始物理文件萃取...")
            for file_item in file_paths:
                # 兼容旧队列任务格式
                if isinstance(file_item, str):
                    file_path = resolve_runtime_path(file_item)
                    file_type = "hybrid" # 盲测捞所有
                else:
                    file_path = resolve_runtime_path(file_item.get("path", ""))
                    file_type = file_item.get("type", "hybrid")
                    

                
                if file_type == "photo" and file_path:
                    try:
                        with open(file_path, "rb") as img_file:
                            b64_str = base64.b64encode(img_file.read()).decode('utf-8')
                        vis_res = analyze_vision_wbs(b64_str)
                        global_vision_reports.append({"agent": "Vision Agent 📷", "heading": "现场实景图合规抽检", "result": f"【图像特征识别】：{vis_res}"})
                    except Exception as e:
                        logger.warning(f"Failed to process photo: {e}")
                    continue
                    
                if not file_path: continue
                
                ext = file_path.rsplit('.', 1)[-1].lower()
                ch = []
                cost_text = ""
                
                # 精细化定向萃取
                if file_type == "scheme" or file_type == "hybrid":
                    if ext == 'xlsx': ch = parse_excel_as_scheme_chunks(file_path)
                    elif ext == 'docx': ch = parse_word_doc_structured(file_path)
                    elif ext == 'pdf': ch = parse_pdf_structured(file_path)
                    
                if _should_extract_cost_context(file_path, file_type):
                    if ext == 'xlsx': cost_text = str(parse_excel_bill(file_path))
                    elif ext == 'docx': cost_text = str(parse_word_as_cost_context(file_path))
                    elif ext == 'pdf': cost_text = str(parse_pdf_as_cost_context(file_path))
                    
                if cost_text and not cost_text.startswith("Error") and "异常" not in cost_text:
                    global_cost_context += cost_text + "\n"
                    
                if isinstance(ch, list):
                    for c in ch:
                        status = get_task_status(task_id)
                        if status == 'CANCELLED': break
                        while status == 'PAUSED': 
                            time.sleep(3)
                            status = get_task_status(task_id)
                        if _audit_engine() == "v2_repair":
                            rules = ""
                        else:
                            time.sleep(1.5) # API限流保护
                            rules = retrieve_rules(c['text'], n_results=5)
                        c['rules'] = rules
                        chunks_ready_for_agents.append(c)
            
            def check_db_cb(): return get_task_status(task_id)
            if check_db_cb() == 'CANCELLED':
                logger.info(f"⛔ 任务 {task_id} 在切片阶段被用户叫停。")
                continue
            
            logger.info(f"切片完成，进入审核引擎({_audit_engine()})... (共 {len(chunks_ready_for_agents)} 切片)")
            def progress(msg, pct):
                logger.info(msg)

            if _audit_engine() == "v2_repair":
                grouped_reports = run_repair_pipeline(
                    chunks_ready_for_agents,
                    project_name,
                    global_cost_context,
                    progress_callback=progress,
                    status_check_callback=check_db_cb,
                )
            else:
                grouped_reports = run_linear_pipeline(chunks_ready_for_agents, project_name, global_cost_context, progress_callback=progress, status_check_callback=check_db_cb)
            
            if global_vision_reports:
                grouped_reports["全局视觉审查"] = global_vision_reports
            
            if check_db_cb() == 'CANCELLED':
                logger.info(f"⛔ 任务 {task_id} 审查被叫停，跳过产出")
                continue
            
            logger.info("初审结束，封存待复核卷宗...")
            
            result_dir = RESULTS_DIR
            os.makedirs(result_dir, exist_ok=True)
            
            json_filename = f"{task_id}_{project_name}_raw_reports.json"
            json_path = os.path.join(result_dir, json_filename)
            
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(grouped_reports, f, ensure_ascii=False, indent=2)
            
            logger.info(f"✅ 断点保护成功: {json_path}")
            update_task_status(task_id, 'REVIEW_PENDING', result_docx_path=json_path)
            
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            logger.error(f"❌ 任务崩溃: {str(e)}", exc_info=True)
            update_task_status(task_id, 'FAILED', error_log=err)
            
        logger.debug("休息 5 秒后继续扫描队列...")
        time.sleep(5)

if __name__ == "__main__":
    main_loop()
