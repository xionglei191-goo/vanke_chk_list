#!/usr/bin/env python3
"""
V9.0 PageIndex 树索引生成器。

该脚本是离线工具：读取国标 PDF/Markdown，调用 PageIndex 生成树结构，
并保存到 data/pageindex_trees/。默认不灌入知识库，追加 --ingest 才会写入 KB。
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = APP_DIR.parent
TREE_DIR = APP_DIR / "data" / "pageindex_trees"
REGISTRY_PATH = APP_DIR / "data" / "standard_pdf_registry.json"
OCR_CACHE_DIR = APP_DIR / "data" / "pageindex_ocr_cache"

sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(PROJECT_DIR))


def _load_dotenv_if_available():
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_DIR / ".env")
        load_dotenv(APP_DIR / ".env")
    except Exception:
        pass


def _slugify(value):
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value.strip())
    value = re.sub(r"\s+", " ", value)
    return value[:120] or "pageindex_tree"


def _configure_pageindex_import(pageindex_root=None):
    root = pageindex_root or os.getenv("PAGEINDEX_ROOT")
    if root:
        sys.path.insert(0, str(Path(root).expanduser().resolve()))


def _configure_llm_env(api_key=None, api_base=None, use_project_llm=True):
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    if api_base:
        os.environ["OPENAI_API_BASE"] = api_base

    if use_project_llm and (not os.getenv("OPENAI_API_KEY") or not os.getenv("OPENAI_API_BASE")):
        try:
            from auditors.engineering_auditor import API_KEY, API_URL

            os.environ.setdefault("OPENAI_API_KEY", API_KEY)
            os.environ.setdefault("OPENAI_API_BASE", API_URL.rsplit("/chat/completions", 1)[0])
        except Exception:
            pass


def _load_pageindex():
    try:
        from pageindex import page_index
        _patch_pageindex_llm()
        return page_index
    except Exception as exc:
        raise RuntimeError(
            "未找到 PageIndex 本地 page_index() 构建入口。请用 --pageindex-root "
            "指向已 clone 的 https://github.com/VectifyAI/PageIndex 源码目录。"
            "注意：PyPI 的 pageindex 包当前主要暴露云端 PageIndexClient，"
            "不能替代本脚本需要的本地树索引生成入口。"
        ) from exc


def _extract_llm_content_raw(response):
    if isinstance(response, dict):
        try:
            message = response.get("choices", [{}])[0].get("message", {})
            content = message.get("content")
            if isinstance(content, list):
                return "".join(str(part.get("text", "") if isinstance(part, dict) else part) for part in content).strip()
            return str(content or "").strip()
        except Exception:
            return ""
    try:
        message = response.choices[0].message
        content = getattr(message, "content", None)
        if isinstance(content, list):
            return "".join(str(part.get("text", "") if isinstance(part, dict) else part) for part in content).strip()
        return str(content or "").strip()
    except Exception:
        return ""


def _pageindex_llm_completion(model, prompt, chat_history=None, return_finish_reason=False):
    from auditors.engineering_auditor import _build_chat_payload, _post_chat_completion, _extract_llm_content

    normalized_model = str(model or os.getenv("PAGEINDEX_MODEL") or os.getenv("LLM_MODEL") or "gpt-5.4")
    normalized_model = normalized_model.removeprefix("litellm/")
    messages = list(chat_history or []) + [{"role": "user", "content": prompt}]
    payload = _build_chat_payload(
        messages,
        model=normalized_model,
        max_tokens=int(os.getenv("PAGEINDEX_MAX_TOKENS", "4096")),
        temperature=0,
    )
    response = None
    last_error = None
    max_retries = int(os.getenv("PAGEINDEX_LLM_RETRIES", "3"))
    timeout = int(os.getenv("PAGEINDEX_LLM_TIMEOUT", "180"))
    hard_timeout = int(os.getenv("PAGEINDEX_LLM_HARD_TIMEOUT", str(timeout + 20)))

    def _raise_timeout(_signum, _frame):
        raise TimeoutError(f"PageIndex LLM 调用超过硬超时: {hard_timeout}s")

    for attempt in range(max_retries):
        use_alarm = hard_timeout > 0 and threading.current_thread() is threading.main_thread()
        previous_handler = None
        try:
            if use_alarm:
                previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
                signal.alarm(hard_timeout)
            response = _post_chat_completion(payload, timeout=timeout)
            break
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries - 1:
                raise
            time.sleep(min(60, 2 ** attempt * 5))
        finally:
            if use_alarm:
                signal.alarm(0)
                if previous_handler is not None:
                    signal.signal(signal.SIGALRM, previous_handler)
    if response is None:
        raise RuntimeError(f"PageIndex LLM 调用失败: {last_error}")
    content = _extract_llm_content(response)
    finish_reason = response.get("choices", [{}])[0].get("finish_reason", "finished")
    if return_finish_reason:
        return content, "max_output_reached" if finish_reason == "length" else "finished"
    return content


async def _pageindex_llm_acompletion(model, prompt):
    import asyncio

    return await asyncio.to_thread(_pageindex_llm_completion, model, prompt)


def _patch_pageindex_llm():
    try:
        import importlib
        import pageindex.utils as pageindex_utils

        pageindex_pdf = importlib.import_module("pageindex.page_index")
        original_get_page_tokens = pageindex_pdf.get_page_tokens
        original_find_toc_pages = pageindex_pdf.find_toc_pages
        original_toc_extractor = pageindex_pdf.toc_extractor
        original_toc_index_extractor = pageindex_pdf.toc_index_extractor
        original_toc_transformer = pageindex_pdf.toc_transformer
        original_process_none_page_numbers = pageindex_pdf.process_none_page_numbers
        original_verify_toc = pageindex_pdf.verify_toc
        original_fix_incorrect_toc_with_retries = getattr(pageindex_pdf, "fix_incorrect_toc_with_retries", None)

        pageindex_utils.llm_completion = _pageindex_llm_completion
        pageindex_utils.llm_acompletion = _pageindex_llm_acompletion
        pageindex_utils.get_page_tokens = _build_page_token_loader(
            original_get_page_tokens,
            count_tokens=pageindex_utils.count_tokens,
        )
        pageindex_pdf.llm_completion = _pageindex_llm_completion
        pageindex_pdf.llm_acompletion = _pageindex_llm_acompletion
        pageindex_pdf.get_page_tokens = pageindex_utils.get_page_tokens
        pageindex_pdf.find_toc_pages = _build_standard_toc_page_finder(original_find_toc_pages)
        pageindex_pdf.toc_extractor = _build_standard_toc_extractor(original_toc_extractor)
        pageindex_pdf.toc_index_extractor = _build_standard_toc_index_extractor(original_toc_index_extractor)
        pageindex_pdf.process_none_page_numbers = _build_standard_none_page_filler(original_process_none_page_numbers)
        pageindex_pdf.verify_toc = _build_standard_toc_verifier(original_verify_toc)
        pageindex_pdf.check_title_appearance_in_start_concurrent = _standard_check_title_appearance_in_start_concurrent
        pageindex_pdf.toc_transformer = lambda toc_content, model=None: _toc_transformer_with_standard_fallback(
            toc_content,
            model=model,
            original_toc_transformer=original_toc_transformer,
        )
        if original_fix_incorrect_toc_with_retries is not None:
            pageindex_pdf.fix_incorrect_toc_with_retries = _build_toc_fix_limiter(
                original_fix_incorrect_toc_with_retries
            )
    except Exception as exc:
        raise RuntimeError(f"PageIndex LLM 流式补丁挂载失败: {exc}") from exc


def _build_toc_fix_limiter(original_fix_incorrect_toc_with_retries):
    async def _limited_fix_incorrect_toc_with_retries(
        toc_with_page_number,
        page_list,
        incorrect_results,
        start_index=1,
        max_attempts=3,
        model=None,
        logger=None,
    ):
        configured_limit = os.getenv("PAGEINDEX_TOC_FIX_MAX_ATTEMPTS")
        if configured_limit is not None:
            try:
                max_attempts = min(max_attempts, max(0, int(configured_limit)))
            except ValueError:
                pass
        if max_attempts <= 0:
            message = (
                "skip fix_incorrect_toc because "
                f"PAGEINDEX_TOC_FIX_MAX_ATTEMPTS={configured_limit}"
            )
            print(message)
            if logger:
                logger.info(message)
            return toc_with_page_number, incorrect_results
        return await original_fix_incorrect_toc_with_retries(
            toc_with_page_number,
            page_list,
            incorrect_results,
            start_index=start_index,
            max_attempts=max_attempts,
            model=model,
            logger=logger,
        )

    return _limited_fix_incorrect_toc_with_retries


def _standard_fallback_enabled():
    return os.getenv("PAGEINDEX_STANDARD_TOC_FALLBACK", "1").strip().lower() not in {"0", "false", "no"}


def _pageindex_ocr_fallback_mode():
    return os.getenv("PAGEINDEX_OCR_FALLBACK", "auto").strip().lower()


def _pageindex_ocr_enabled():
    return _pageindex_ocr_fallback_mode() not in {"0", "false", "no", "never", "off"}


def _safe_int_env(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _safe_float_env(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _page_text(page_item):
    if isinstance(page_item, (list, tuple)):
        value = page_item[0] if page_item else ""
        return "" if value is None else str(value)
    return "" if page_item is None else str(page_item)


def _page_token(page_item):
    if isinstance(page_item, (list, tuple)) and len(page_item) > 1:
        try:
            return int(page_item[1] or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _page_list_quality(page_list):
    texts = [_page_text(item) for item in page_list or []]
    page_count = len(texts)
    non_empty_pages = sum(1 for text in texts if len(text.strip()) >= _safe_int_env("PAGEINDEX_MIN_PAGE_TEXT_CHARS", 20))
    total_chars = sum(len(text.strip()) for text in texts)
    total_tokens = sum(_page_token(item) for item in page_list or [])
    return {
        "page_count": page_count,
        "non_empty_pages": non_empty_pages,
        "non_empty_ratio": non_empty_pages / page_count if page_count else 0,
        "total_chars": total_chars,
        "total_tokens": total_tokens,
    }


def _format_page_quality(quality):
    return (
        f"pages={quality['page_count']}, "
        f"non_empty={quality['non_empty_pages']} ({quality['non_empty_ratio']:.0%}), "
        f"chars={quality['total_chars']}, "
        f"tokens={quality['total_tokens']}"
    )


def _page_text_quality_sufficient(page_list):
    quality = _page_list_quality(page_list)
    if quality["page_count"] == 0:
        return False
    min_chars = _safe_int_env("PAGEINDEX_MIN_TEXT_CHARS", 1000)
    min_page_ratio = _safe_float_env("PAGEINDEX_MIN_TEXT_PAGE_RATIO", 0.2)
    return quality["total_chars"] >= min_chars and quality["non_empty_ratio"] >= min_page_ratio


def _should_use_ocr_fallback(page_list):
    mode = _pageindex_ocr_fallback_mode()
    if mode in {"always", "force", "forced"}:
        return True
    if not _pageindex_ocr_enabled():
        return False
    return not _page_text_quality_sufficient(page_list)


def _ocr_cache_enabled():
    return os.getenv("PAGEINDEX_OCR_CACHE", "1").strip().lower() not in {"0", "false", "no"}


def _ocr_cache_path(pdf_path, engine, cache_dir=None):
    path = Path(pdf_path)
    try:
        resolved = path.expanduser().resolve()
        stat = resolved.stat()
        fingerprint = f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}|{engine}"
    except OSError:
        resolved = path
        fingerprint = f"{path}|{engine}"
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
    configured_cache_dir = cache_dir or os.getenv("PAGEINDEX_OCR_CACHE_DIR")
    output_dir = Path(configured_cache_dir).expanduser() if configured_cache_dir else OCR_CACHE_DIR
    if not output_dir.is_absolute():
        output_dir = PROJECT_DIR / output_dir
    return output_dir / f"{_slugify(path.stem)}.{engine}.{digest}.json"


def _load_ocr_page_text_cache(pdf_path, engine):
    if not _ocr_cache_enabled():
        return None
    cache_path = _ocr_cache_path(pdf_path, engine)
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        pages = payload.get("pages")
        if not isinstance(pages, list):
            return None
        print(f"PageIndex OCR cache hit: {cache_path}")
        return [str(page or "") for page in pages]
    except Exception as exc:
        print(f"PageIndex OCR cache ignored: {cache_path} ({exc})")
        return None


def _write_ocr_page_text_cache(pdf_path, engine, page_texts):
    if not _ocr_cache_enabled():
        return None
    cache_path = _ocr_cache_path(pdf_path, engine)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_path": str(Path(pdf_path).expanduser().resolve()),
            "engine": engine,
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pages": list(page_texts),
        }
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"PageIndex OCR cache saved: {cache_path}")
        return cache_path
    except Exception as exc:
        print(f"PageIndex OCR cache save failed: {exc}")
        return None


def _ocr_results_to_page_texts(ocr_pages, page_count):
    page_texts = [""] * max(0, page_count)
    for index, page in enumerate(ocr_pages or []):
        page_num = getattr(page, "page_num", index + 1)
        try:
            page_num = int(page_num)
        except (TypeError, ValueError):
            page_num = index + 1
        if page_num <= 0:
            continue
        if page_num > len(page_texts):
            page_texts.extend([""] * (page_num - len(page_texts)))
        page_texts[page_num - 1] = str(getattr(page, "text", "") or "").strip()
    return page_texts


def _page_texts_to_page_list(page_texts, model=None, count_tokens=None):
    page_list = []
    for text in page_texts:
        text = str(text or "")
        try:
            token_length = count_tokens(text, model) if count_tokens else len(text)
        except Exception:
            token_length = len(text)
        page_list.append((text, token_length))
    return page_list


def _run_ocr_for_pageindex(pdf_path, engine, original_page_count, model=None, count_tokens=None):
    cached_texts = _load_ocr_page_text_cache(pdf_path, engine)
    if cached_texts is not None:
        return _page_texts_to_page_list(cached_texts, model=model, count_tokens=count_tokens)

    from ocr_engine import ocr_extract

    print(f"PageIndex OCR fallback start: engine={engine}, pdf={pdf_path}")
    ocr_pages = ocr_extract(str(pdf_path), engine=engine)
    page_texts = _ocr_results_to_page_texts(ocr_pages, original_page_count)
    _write_ocr_page_text_cache(pdf_path, engine, page_texts)
    return _page_texts_to_page_list(page_texts, model=model, count_tokens=count_tokens)


def _build_page_token_loader(original_get_page_tokens, count_tokens=None):
    def _get_page_tokens(pdf_path, model=None, pdf_parser="PyPDF2"):
        original_page_list = original_get_page_tokens(pdf_path, model=model, pdf_parser=pdf_parser)
        original_quality = _page_list_quality(original_page_list)
        print(f"PageIndex PDF text-layer quality: {_format_page_quality(original_quality)}")

        if not _should_use_ocr_fallback(original_page_list):
            return original_page_list

        if not _pageindex_ocr_enabled():
            return original_page_list

        engine = os.getenv("PAGEINDEX_OCR_ENGINE") or "auto"
        try:
            ocr_page_list = _run_ocr_for_pageindex(
                pdf_path,
                engine=engine,
                original_page_count=len(original_page_list),
                model=model,
                count_tokens=count_tokens,
            )
        except Exception as exc:
            print(f"PageIndex OCR fallback failed: {exc}")
            return original_page_list

        ocr_quality = _page_list_quality(ocr_page_list)
        print(f"PageIndex OCR text quality: {_format_page_quality(ocr_quality)}")
        if ocr_quality["total_chars"] > original_quality["total_chars"]:
            print("PageIndex OCR fallback selected.")
            return ocr_page_list

        if _pageindex_ocr_fallback_mode() in {"always", "force", "forced"} and ocr_quality["total_chars"] > 0:
            print("PageIndex OCR fallback forced.")
            return ocr_page_list

        print("PageIndex OCR fallback skipped because OCR text was not better than PDF text layer.")
        return original_page_list

    return _get_page_tokens


def _compact_text(value):
    return re.sub(r"\s+", "", str(value or ""))


def _contains_chinese_toc_marker(text):
    return bool(re.search(r"目\s*次", text or ""))


def _looks_like_main_content_start(text):
    compact = _compact_text(text[:300])
    compact = re.sub(r"^[^\dA-Za-z\u4e00-\u9fff]+", "", compact)
    return bool(re.match(r"^(1|第1章|第一章)总则", compact) or re.match(r"^1General", compact, flags=re.I))


def _find_standard_toc_pages(page_list, start_page_index=0, opt=None):
    if not _standard_fallback_enabled():
        return []
    check_limit = getattr(opt, "toc_check_page_num", None) or 20
    search_end = min(len(page_list), max(check_limit, start_page_index + 1))
    toc_start = None

    for page_index in range(start_page_index, search_end):
        text = _page_text(page_list[page_index])
        if _contains_chinese_toc_marker(text):
            toc_start = page_index
            break
    if toc_start is None:
        return []

    max_span = int(os.getenv("PAGEINDEX_STANDARD_TOC_MAX_SPAN", "12"))
    toc_pages = []
    for page_index in range(toc_start, min(len(page_list), toc_start + max_span)):
        text = _page_text(page_list[page_index])
        if page_index > toc_start and _looks_like_main_content_start(text):
            break
        toc_pages.append(page_index)
    return toc_pages


def _build_standard_toc_page_finder(original_find_toc_pages):
    def _find_toc_pages(start_page_index, page_list, opt, logger=None):
        toc_pages = _find_standard_toc_pages(page_list, start_page_index=start_page_index, opt=opt)
        if toc_pages:
            message = f"find_toc_pages standard fallback hit: pages={toc_pages}"
            print(message)
            if logger:
                logger.info(message)
            return toc_pages
        return original_find_toc_pages(start_page_index, page_list, opt, logger=logger)

    return _find_toc_pages


def _transform_toc_dots_to_colon(text):
    text = re.sub(r"\.{5,}", ": ", text)
    text = re.sub(r"(?:\. ){5,}\.?", ": ", text)
    return text


def _build_standard_toc_extractor(original_toc_extractor):
    def _toc_extractor(page_list, toc_page_list, model):
        toc_content = _transform_toc_dots_to_colon(
            "".join(_page_text(page_list[page_index]) for page_index in toc_page_list)
        )
        parsed = _parse_standard_toc(toc_content)
        parsed_with_pages = [item for item in parsed if item.get("page") is not None]
        if len(parsed) >= 10 and len(parsed_with_pages) / max(1, len(parsed)) >= 0.75:
            print(
                "toc_extractor standard fallback hit: "
                f"entries={len(parsed)}, with_pages={len(parsed_with_pages)}"
            )
            return {
                "toc_content": toc_content,
                "page_index_given_in_toc": "yes",
            }
        return original_toc_extractor(page_list, toc_page_list, model)

    return _toc_extractor


def _normalize_title_for_match(value):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", _compact_text(value)).lower()


def _page_contains_title(page_text, title, structure=None):
    normalized_page = _normalize_title_for_match(page_text)
    normalized_title = _normalize_title_for_match(title)
    if not normalized_title:
        return False
    candidates = [normalized_title]
    normalized_structure = _normalize_title_for_match(structure)
    if normalized_structure:
        candidates.insert(0, f"{normalized_structure}{normalized_title}")
    return any(candidate and candidate in normalized_page for candidate in candidates)


def _page_starts_with_title(page_text, title, structure=None):
    prefix = _normalize_title_for_match(str(page_text or "")[:300])
    normalized_title = _normalize_title_for_match(title)
    normalized_structure = _normalize_title_for_match(structure)
    candidates = [normalized_title]
    if normalized_structure:
        candidates.insert(0, f"{normalized_structure}{normalized_title}")
    return any(candidate and candidate in prefix[:80] for candidate in candidates if candidate)


def _extract_physical_pages(content):
    pages = []
    pattern = re.compile(r"<physical_index_(\d+)>\s*(.*?)\s*<physical_index_\1>", re.S)
    for match in pattern.finditer(content or ""):
        pages.append((int(match.group(1)), match.group(2)))
    return pages


def _standard_toc_index_extract(toc, content):
    pages = _extract_physical_pages(content)
    if not pages:
        return []

    results = []
    for item in toc:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        structure = item.get("structure")
        for physical_index, page_text in pages:
            if _page_contains_title(page_text, title, structure=structure):
                matched = dict(item)
                matched["physical_index"] = f"<physical_index_{physical_index}>"
                results.append(matched)
                break
    return results


def _build_standard_toc_index_extractor(original_toc_index_extractor):
    def _toc_index_extractor(toc, content, model=None):
        results = _standard_toc_index_extract(toc, content)
        if results:
            print(f"toc_index_extractor standard fallback hit: matched={len(results)}")
            return results
        return original_toc_index_extractor(toc, content, model=model)

    return _toc_index_extractor


def _nearest_known_physical_index(toc_items, item_index):
    previous_index = None
    next_index = None
    for cursor in range(item_index - 1, -1, -1):
        if toc_items[cursor].get("physical_index") is not None:
            previous_index = toc_items[cursor]["physical_index"]
            break
    for cursor in range(item_index + 1, len(toc_items)):
        if toc_items[cursor].get("physical_index") is not None:
            next_index = toc_items[cursor]["physical_index"]
            break
    if previous_index is not None and next_index is not None:
        return min(next_index, previous_index + 1)
    return previous_index if previous_index is not None else next_index


def _build_standard_none_page_filler(original_process_none_page_numbers):
    def _process_none_page_numbers(toc_items, page_list, start_index=1, model=None):
        if not _standard_fallback_enabled():
            return original_process_none_page_numbers(toc_items, page_list, start_index=start_index, model=model)

        filled = 0
        max_allowed_page = len(page_list) + start_index - 1
        for item_index, item in enumerate(toc_items):
            if item.get("physical_index") is not None:
                continue
            inferred_index = _nearest_known_physical_index(toc_items, item_index)
            if inferred_index is None:
                continue
            item["physical_index"] = max(start_index, min(max_allowed_page, inferred_index))
            item.pop("page", None)
            filled += 1
        if filled:
            print(f"process_none_page_numbers standard fallback hit: filled={filled}")
        return toc_items

    return _process_none_page_numbers


def _toc_item_page_text(page_list, item, start_index=1):
    physical_index = item.get("physical_index")
    if physical_index is None:
        return ""
    page_list_index = physical_index - start_index
    if page_list_index < 0 or page_list_index >= len(page_list):
        return ""
    return _page_text(page_list[page_list_index])


def _build_standard_toc_verifier(original_verify_toc):
    async def _verify_toc(page_list, list_result, start_index=1, N=None, model=None):
        if not _standard_fallback_enabled() or os.getenv("PAGEINDEX_STANDARD_VERIFY_FALLBACK", "1").strip().lower() in {"0", "false", "no"}:
            return await original_verify_toc(page_list, list_result, start_index=start_index, N=N, model=model)

        print("verify_toc standard fallback hit")
        if N is None:
            sample_indices = range(0, len(list_result))
        else:
            sample_indices = range(0, min(N, len(list_result)))

        correct_count = 0
        checked_count = 0
        incorrect_results = []
        for item_index in sample_indices:
            item = list_result[item_index]
            page_text = _toc_item_page_text(page_list, item, start_index=start_index)
            checked_count += 1
            if page_text and _page_contains_title(page_text, item.get("title"), structure=item.get("structure")):
                correct_count += 1
                continue
            incorrect_results.append({
                "list_index": item_index,
                "answer": "no",
                "title": item.get("title"),
                "page_number": item.get("physical_index"),
                "physical_index": item.get("physical_index"),
            })

        accuracy = correct_count / checked_count if checked_count > 0 else 0
        print(f"accuracy: {accuracy*100:.2f}%")
        return accuracy, incorrect_results

    return _verify_toc


async def _standard_check_title_appearance_in_start_concurrent(structure, page_list, model=None, logger=None):
    if logger:
        logger.info("Checking title appearance in start with standard fallback")
    for item in structure:
        page_text = _toc_item_page_text(page_list, item, start_index=1)
        item["appear_start"] = "yes" if page_text and _page_starts_with_title(
            page_text,
            item.get("title"),
            structure=item.get("structure"),
        ) else "no"
    return structure


def _strip_toc_leaders(value):
    value = re.sub(r"[·•…⋯.。:：]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip().strip(" ,，、一-—")


def _extract_trailing_page(value):
    match = re.search(r"(?P<page>(?:\d\s*){1,3})\s*$", value)
    if not match:
        return value, None
    page_text = re.sub(r"\s+", "", match.group("page"))
    if not page_text.isdigit():
        return value, None
    return value[:match.start()].strip(), int(page_text)


def _is_toc_leader_line(value):
    return bool(re.fullmatch(r"[\s·•…⋯.。:：,，、\-—一]+", value or ""))


def _is_page_number_line(value):
    return bool(re.fullmatch(r"\d{1,3}", (value or "").strip()))


def _normalize_toc_structure(value):
    return re.sub(r"\s+", "", str(value or "").replace("．", "."))


def _normalize_toc_content(value):
    content = str(value or "")
    content = re.split(r"(?:\n\s*\d*\s*Contents\b|InContents|\sContents\s)", content, maxsplit=1, flags=re.I)[0]
    content = re.sub(r"#+", " ", content)
    content = re.sub(r"\s+", " ", content)
    # OCR may glue a page number with the next section marker: "…… 406.4 沉井" = page 40 + section 6.4.
    content = re.sub(r"([…⋯·.]\s*)(\d{1,3})(?=(\d{1,2}\s*[.．]\s*\d+\s*[\u4e00-\u9fff]))", r"\1\2 ", content)
    return content.strip()


def _is_wrapped_numbered_header(value):
    return bool(re.match(r"^\d+(?:\s*\.\s*\d+)*\s+[\u4e00-\u9fff]", value or ""))


def _find_next_toc_page(cleaned_lines, start_index):
    for cursor in range(start_index, min(len(cleaned_lines), start_index + 5)):
        line = cleaned_lines[cursor].strip()
        if _is_page_number_line(line):
            return int(line), cursor
        if _is_wrapped_numbered_header(line) or line.startswith("附录") or line.startswith("本标准用词说明") or line.startswith("引用标准名录"):
            break
    return None, start_index - 1


def _parse_wrapped_standard_toc(toc_content):
    """Parse TOC text where OCR splits title, leaders and page number into separate lines."""
    content = re.split(r"(?:\n\s*\d*\s*Contents\b|InContents)", toc_content, maxsplit=1, flags=re.I)[0]
    cleaned_lines = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line in {"目", "次", "目次"} or _is_toc_leader_line(line):
            continue
        cleaned_lines.append(line)

    parsed = []
    seen = set()
    cursor = 0
    while cursor < len(cleaned_lines):
        line = cleaned_lines[cursor]
        header = line

        if _is_page_number_line(line) and cursor + 1 < len(cleaned_lines):
            next_line = cleaned_lines[cursor + 1].strip()
            if re.match(r"^[.．]\s*\d+\s+[\u4e00-\u9fff]", next_line):
                header = f"{line}{next_line}"
                cursor += 1
            elif (
                re.match(r"^[.．]\s*\d+\s*$", next_line)
                and cursor + 2 < len(cleaned_lines)
                and re.match(r"^[\u4e00-\u9fff]", cleaned_lines[cursor + 2].strip())
            ):
                header = f"{line}{next_line} {cleaned_lines[cursor + 2].strip()}"
                cursor += 2
            else:
                cursor += 1
                continue
        elif re.fullmatch(r"附录\s*[A-Za-zＡ-Ｚａ-ｚ]", line) and cursor + 1 < len(cleaned_lines):
            header = f"{line} {cleaned_lines[cursor + 1].strip()}"
            cursor += 1

        numbered = re.match(r"^(?P<structure>\d+(?:\s*\.\s*\d+)*)\s+(?P<title>(?!附录)[\u4e00-\u9fff].+)$", header)
        appendix = re.match(r"^(?:\d+\s*)?(?P<structure>附录\s*[A-Za-zＡ-Ｚａ-ｚ])\s*(?P<title>.+)$", header)
        special = re.match(r"^(?P<title>本标准用词说明|引用标准名录|附\s*[:：].+)$", header)

        entry = None
        if numbered:
            entry = {
                "structure": _normalize_toc_structure(numbered.group("structure")),
                "title": _strip_toc_leaders(numbered.group("title")),
            }
        elif appendix:
            structure = _normalize_toc_structure(appendix.group("structure"))
            title = _strip_toc_leaders(appendix.group("title"))
            entry = {
                "structure": structure,
                "title": f"{structure} {title}".strip(),
            }
        elif special:
            entry = {
                "structure": None,
                "title": _strip_toc_leaders(special.group("title")),
            }

        if entry and entry.get("title") and len(entry["title"]) >= 2:
            page, page_cursor = _find_next_toc_page(cleaned_lines, cursor + 1)
            entry["page"] = page
            key = (entry.get("structure"), entry.get("title"))
            if key not in seen:
                seen.add(key)
                parsed.append(entry)
            cursor = max(cursor + 1, page_cursor + 1)
            continue

        cursor += 1

    return parsed


def _parse_inline_standard_toc(toc_content):
    """Parse OCR markdown TOC text where many entries are joined into one line."""
    content = _normalize_toc_content(toc_content)
    if not content:
        return []

    content = re.sub(r"^.*?目\s*次", "", content, count=1)
    marker_pattern = re.compile(
        r"(?<![\w\u4e00-\u9fff])"
        r"(?P<marker>"
        r"附录\s*[A-Za-zＡ-Ｚａ-ｚ](?=\s*[\u4e00-\u9fff])"
        r"|[A-Za-zＡ-Ｚａ-ｚ]\s*\.\s*\d+(?=\s*[\u4e00-\u9fff])"
        r"|\d{1,2}(?:\s*\.\s*\d+)*(?!\s*(?:本(?:规范|标准)用词说明|引用标准名录|附\s*[:：]\s*条文说明))(?=\s*[\u4e00-\u9fff])"
        r"|本(?:规范|标准)用词说明"
        r"|引用标准名录"
        r"|附\s*[:：]\s*条文说明"
        r")"
    )
    matches = list(marker_pattern.finditer(content))
    parsed = []
    seen = set()

    for index, match in enumerate(matches):
        marker = match.group("marker")
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = _strip_toc_leaders(content[body_start:body_end])
        if not body:
            continue

        title_part, page = _extract_trailing_page(body)
        title_part = _strip_toc_leaders(title_part)
        marker_norm = _normalize_toc_structure(marker)
        entry = None

        if re.match(r"^附录", marker_norm):
            entry = {
                "structure": marker_norm,
                "title": f"{marker_norm} {title_part}".strip(),
                "page": page,
            }
        elif re.match(r"^(本(?:规范|标准)用词说明|引用标准名录|附[:：]?条文说明)$", marker_norm):
            title = marker_norm.replace("附:", "附 ").replace("附：", "附 ")
            if title_part:
                title = f"{title} {title_part}".strip()
            entry = {"structure": None, "title": title, "page": page}
        else:
            entry = {
                "structure": marker_norm,
                "title": title_part,
                "page": page,
            }

        if not entry.get("title") or len(entry["title"]) < 2:
            continue
        key = (entry.get("structure"), entry.get("title"))
        if key in seen:
            continue
        seen.add(key)
        parsed.append(entry)

    return parsed


def _parse_standard_toc(toc_content):
    """Best-effort parser for common Chinese GB/JGJ table-of-contents lines."""
    if not toc_content:
        return []

    # GB PDFs often contain Chinese TOC first and English "Contents" afterwards.
    content = re.split(r"(?:\n\s*\d*\s*Contents\b|InContents)", toc_content, maxsplit=1, flags=re.I)[0]
    parsed = []
    seen = set()

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or len(line) < 3:
            continue

        entry = None
        numbered = re.match(r"^(?P<structure>\d+(?:\s*\.\s*\d+)*)\s*(?P<body>(?!附录)[\u4e00-\u9fff].+)$", line)
        appendix = re.match(r"^(?:\d+\s*)?(?P<structure>附录\s*[A-Za-zＡ-Ｚａ-ｚ])\s*(?P<body>.+)$", line)
        special = re.match(r"^(?P<title>本标准用词说明|引用标准名录|附\s*[:：].+)\s*(?P<body>.*)$", line)

        if numbered:
            structure = re.sub(r"\s+", "", numbered.group("structure"))
            body = _strip_toc_leaders(numbered.group("body"))
            title_part, page = _extract_trailing_page(body)
            title = _strip_toc_leaders(title_part)
            entry = {"structure": structure, "title": title, "page": page}
        elif appendix:
            structure = re.sub(r"\s+", "", appendix.group("structure"))
            body = _strip_toc_leaders(appendix.group("body"))
            title_part, page = _extract_trailing_page(body)
            title = f"{structure} {_strip_toc_leaders(title_part)}".strip()
            entry = {"structure": structure, "title": title, "page": page}
        elif special:
            title = _strip_toc_leaders(special.group("title"))
            body = _strip_toc_leaders(special.group("body"))
            body, page = _extract_trailing_page(body)
            if body:
                title = f"{title} {body}".strip()
            entry = {"structure": None, "title": title, "page": page}

        if not entry:
            continue
        if not entry.get("title") or len(entry["title"]) < 2:
            continue
        key = (entry.get("structure"), entry.get("title"))
        if key in seen:
            continue
        seen.add(key)
        parsed.append(entry)

    parsed_with_pages = [item for item in parsed if item.get("page") is not None]
    wrapped = _parse_wrapped_standard_toc(content)
    wrapped_with_pages = [item for item in wrapped if item.get("page") is not None]
    inline = _parse_inline_standard_toc(content)
    inline_with_pages = [item for item in inline if item.get("page") is not None]

    candidates = [
        (parsed, parsed_with_pages),
        (wrapped, wrapped_with_pages),
        (inline, inline_with_pages),
    ]
    best, _best_with_pages = max(candidates, key=lambda pair: (len(pair[1]), len(pair[0])))
    if best:
        return best

    return parsed


def _toc_transformer_with_standard_fallback(toc_content, model=None, original_toc_transformer=None):
    parsed = _parse_standard_toc(toc_content)
    parsed_with_pages = [item for item in parsed if item.get("page") is not None]
    if len(parsed) >= 10 and len(parsed_with_pages) / max(1, len(parsed)) >= 0.75:
        print(f"toc_transformer standard fallback hit: entries={len(parsed)}, with_pages={len(parsed_with_pages)}")
        return parsed
    if original_toc_transformer is None:
        return parsed
    return original_toc_transformer(toc_content, model)


def _preflight_llm(model):
    try:
        response = _pageindex_llm_completion(model, '请只输出 {"status":"ok","text":"总则"}，不要解释。')
    except Exception as exc:
        raise RuntimeError(f"LLM 预检失败：模型 {model} 调用异常: {exc}") from exc

    content = str(response or "").strip()
    if not content:
        raise RuntimeError(
            f"LLM 预检失败：模型 {model} 的流式 chat-completions 返回了空 content。"
            "请更换 PAGEINDEX_MODEL，或检查当前 OpenAI 兼容代理是否能返回文本。"
        )
    if "总则" not in content:
        raise RuntimeError(
            f"LLM 预检失败：模型 {model} 未通过中文流式解码检查，返回: {content[:120]}"
        )
    return content


def _load_registry(registry_path):
    path = Path(registry_path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"注册表必须是 JSON list: {path}")
    return data


def _resolve_input_path(file_path, registry_path=None):
    path = Path(file_path).expanduser()
    if path.is_absolute():
        return path.resolve()

    candidates = []
    if registry_path:
        candidates.append(Path(registry_path).expanduser().resolve().parent / path)
    candidates.extend([
        PROJECT_DIR / path,
        APP_DIR / path,
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (PROJECT_DIR / path).resolve()


def _tree_stats(tree_data):
    roots = tree_data.get("structure") or tree_data.get("tree") or tree_data.get("nodes") or []
    if isinstance(roots, dict):
        roots = [roots]
    if not isinstance(roots, list):
        return 0, 0

    def visit(node, depth):
        if not isinstance(node, dict):
            return 0, depth
        children = node.get("nodes") or node.get("children") or []
        total = 1
        max_depth = depth
        if isinstance(children, list):
            for child in children:
                child_total, child_depth = visit(child, depth + 1)
                total += child_total
                max_depth = max(max_depth, child_depth)
        return total, max_depth

    total_nodes = 0
    max_depth = 0
    for root in roots:
        root_total, root_depth = visit(root, 1)
        total_nodes += root_total
        max_depth = max(max_depth, root_depth)
    return total_nodes, max_depth


def build_tree_for_standard(
    file_path,
    category,
    model="gpt-5.4",
    pageindex_root=None,
    toc_check_pages=None,
    max_pages_per_node=None,
    max_tokens_per_node=None,
    add_node_summary=True,
    add_doc_description=True,
    toc_fix_max_attempts=None,
    ocr_fallback=None,
    ocr_engine=None,
    ocr_max_pages_per_request=None,
):
    if toc_fix_max_attempts is not None:
        os.environ["PAGEINDEX_TOC_FIX_MAX_ATTEMPTS"] = str(toc_fix_max_attempts)
    if ocr_fallback is not None:
        os.environ["PAGEINDEX_OCR_FALLBACK"] = str(ocr_fallback)
    if ocr_engine is not None:
        os.environ["PAGEINDEX_OCR_ENGINE"] = str(ocr_engine)
    if ocr_max_pages_per_request is not None:
        os.environ["PADDLE_MAX_PAGES_PER_REQUEST"] = str(ocr_max_pages_per_request)
    _configure_pageindex_import(pageindex_root)
    page_index = _load_pageindex()
    source_path = Path(file_path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"源文件不存在: {source_path}")
    if source_path.suffix.lower() != ".pdf":
        raise ValueError("当前生产脚本仅接入 PDF PageIndex；Markdown 可后续按 PageIndex 官方 md_to_tree 分支扩展。")

    result = page_index(
        doc=str(source_path),
        model=model,
        toc_check_page_num=toc_check_pages,
        max_page_num_each_node=max_pages_per_node,
        max_token_num_each_node=max_tokens_per_node,
        if_add_node_summary="yes" if add_node_summary else "no",
        if_add_node_text="yes",
        if_add_node_id="yes",
        if_add_doc_description="yes" if add_doc_description else "no",
    )
    return {
        "category": category,
        "source_path": str(source_path),
        "model": model,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pageindex_result": result,
        "structure": result.get("structure", []),
    }


def _write_tree(tree_data, output_dir=TREE_DIR):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_slugify(tree_data['category'])}.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(tree_data, f, ensure_ascii=False, indent=2)
    return output_path


def _iter_jobs(args):
    if args.pdf_path and args.category:
        yield {"pdf_path": str(_resolve_input_path(args.pdf_path)), "category": args.category}
        return

    for item in _load_registry(args.registry):
        pdf_path = item.get("pdf_path") or item.get("path")
        category = item.get("category")
        if not pdf_path or not category:
            print(f"跳过无效注册项: {item}")
            continue
        yield {"pdf_path": str(_resolve_input_path(pdf_path, args.registry)), "category": category}


def main():
    _load_dotenv_if_available()

    parser = argparse.ArgumentParser(description="V9.0 PageIndex 树索引生成器")
    parser.add_argument("--pdf-path", help="单个国标 PDF 路径")
    parser.add_argument("--category", help="单个 PDF 的知识库分类名")
    parser.add_argument("--registry", default=str(REGISTRY_PATH), help="批量注册表 JSON 路径")
    parser.add_argument("--output-dir", default=str(TREE_DIR), help="树索引输出目录")
    parser.add_argument("--model", default=os.getenv("PAGEINDEX_MODEL") or os.getenv("LLM_MODEL") or "gpt-5.4", help="PageIndex 使用的 LLM 模型")
    parser.add_argument("--pageindex-root", default=os.getenv("PAGEINDEX_ROOT"), help="PageIndex 源码目录")
    parser.add_argument("--api-key", default=None, help="覆盖 OPENAI_API_KEY")
    parser.add_argument("--api-base", default=None, help="覆盖 OPENAI_API_BASE")
    parser.add_argument("--no-project-llm", action="store_true", help="不从 auditors.engineering_auditor 继承项目 LLM 代理")
    parser.add_argument("--toc-check-pages", type=int, default=None)
    parser.add_argument("--max-pages-per-node", type=int, default=None)
    parser.add_argument("--max-tokens-per-node", type=int, default=None)
    parser.add_argument(
        "--toc-fix-max-attempts",
        type=int,
        default=None,
        help="限制 PageIndex 目录页码修复轮数；0 表示跳过修复，适合已达到高准确率但 LLM 修复卡住的国标。",
    )
    parser.add_argument("--no-node-summary", action="store_true", help="跳过节点摘要生成，仅保留 title + text；适合大 PDF 首次结构化")
    parser.add_argument("--no-doc-description", action="store_true", help="跳过文档整体描述生成，减少一次大上下文 LLM 调用")
    parser.add_argument(
        "--ocr-fallback",
        choices=["auto", "never", "always"],
        default=os.getenv("PAGEINDEX_OCR_FALLBACK", "auto"),
        help="PageIndex PDF 文本层质量不足时是否先走项目 OCR 引擎生成逐页文本。",
    )
    parser.add_argument(
        "--ocr-engine",
        default=os.getenv("PAGEINDEX_OCR_ENGINE", "auto"),
        help="PageIndex OCR 兜底使用的项目 OCR 引擎，例如 auto/paddle_vl_1.5/pp_structure_v3/rapidocr。",
    )
    parser.add_argument(
        "--ocr-max-pages-per-request",
        type=int,
        default=_safe_int_env("PADDLE_MAX_PAGES_PER_REQUEST", 100),
        help="在线 PaddleOCR 单次最多提交页数；超过后自动分段，默认 100。",
    )
    parser.add_argument("--force", action="store_true", help="即使输出文件已存在也重新生成")
    parser.add_argument("--dry-run", action="store_true", help="仅校验待处理 PDF 与输出路径，不调用 PageIndex")
    parser.add_argument("--limit", type=int, default=0, help="批量模式下仅处理前 N 个任务，0 表示不限制")
    parser.add_argument("--preflight-only", action="store_true", help="仅测试 PageIndex LLM 模型是否能返回文本")
    parser.add_argument("--skip-llm-preflight", action="store_true", help="跳过真实生成前的 LLM 文本返回预检")
    parser.add_argument("--ingest", action="store_true", help="生成后立即灌入 knowledge_base.json")
    parser.add_argument("--ingest-dry-run", action="store_true", help="按灌入逻辑预览候选规则并执行离线门禁，不写入知识库")
    parser.add_argument("--wbs-code", default="AI_AUTO", help="--ingest 时使用的 WBS 编码策略")
    parser.add_argument("--level", type=int, default=1, help="--ingest 时使用的规则等级")
    parser.add_argument("--verify-ingest", action="store_true", help="--ingest 时启用 PageIndex 条目质量门禁")
    parser.add_argument("--verify-sample-n", type=int, default=10, help="质量门禁抽样条数")
    parser.add_argument("--verify-with-llm", action="store_true", help="质量门禁追加 LLM 语义完整性抽检")
    parser.add_argument("--min-verify-accuracy", type=float, default=0.7, help="质量门禁最低抽样通过率")
    parser.add_argument("--include-frontmatter", action="store_true", help="--ingest 时保留前言、目录、封面等前置信息节点")
    parser.add_argument("--keep-legacy", action="store_true", help="--ingest 时保留同源 legacy OCR/切片条目；默认停用以避免重复召回")
    args = parser.parse_args()

    if (args.pdf_path and not args.category) or (args.category and not args.pdf_path):
        parser.error("--pdf-path 和 --category 必须同时提供。")
    if args.ingest and args.ingest_dry_run:
        parser.error("--ingest 与 --ingest-dry-run 不能同时使用。")

    _configure_llm_env(args.api_key, args.api_base, use_project_llm=not args.no_project_llm)

    if args.preflight_only:
        content = _preflight_llm(args.model)
        print(f"LLM 预检通过: {content[:120]}")
        return

    jobs = list(_iter_jobs(args))
    if args.limit > 0:
        jobs = jobs[:args.limit]
    if not jobs:
        raise SystemExit("没有可处理的 PDF。请提供 --pdf-path/--category，或填写 standard_pdf_registry.json。")

    if args.dry_run:
        for job in jobs:
            category = job["category"]
            pdf_path = Path(job["pdf_path"])
            output_path = Path(args.output_dir) / f"{_slugify(category)}.json"
            source_state = "ok" if pdf_path.exists() else "missing"
            output_state = "exists" if output_path.exists() else "pending"
            print(f"[{source_state}] {category} -> {pdf_path} ({output_state}: {output_path})")
        return

    if not args.skip_llm_preflight:
        content = _preflight_llm(args.model)
        print(f"LLM 预检通过: {content[:120]}")

    for job in jobs:
        category = job["category"]
        output_path = Path(args.output_dir) / f"{_slugify(category)}.json"
        if output_path.exists() and not args.force:
            print(f"跳过已存在树索引: {output_path}")
        else:
            print(f"开始生成 PageIndex 树索引: {category}")
            start_time = time.perf_counter()
            tree_data = build_tree_for_standard(
                job["pdf_path"],
                category,
                model=args.model,
                pageindex_root=args.pageindex_root,
                toc_check_pages=args.toc_check_pages,
                max_pages_per_node=args.max_pages_per_node,
                max_tokens_per_node=args.max_tokens_per_node,
                add_node_summary=not args.no_node_summary,
                add_doc_description=not args.no_doc_description,
                toc_fix_max_attempts=args.toc_fix_max_attempts,
                ocr_fallback=args.ocr_fallback,
                ocr_engine=args.ocr_engine,
                ocr_max_pages_per_request=args.ocr_max_pages_per_request,
            )
            output_path = _write_tree(tree_data, args.output_dir)
            node_count, max_depth = _tree_stats(tree_data)
            elapsed = time.perf_counter() - start_time
            print(f"已保存树索引: {output_path} (nodes={node_count}, depth={max_depth}, seconds={elapsed:.1f})")

        if args.ingest_dry_run:
            from rag_engine.kb_manager import (
                build_pageindex_rule_records,
                get_all_rules,
                get_retirable_legacy_rules,
                verify_ingested_rules,
            )

            preview_rules, ingest_meta = build_pageindex_rule_records(
                str(output_path),
                category,
                wbs_code=args.wbs_code,
                level=args.level,
                include_frontmatter=args.include_frontmatter,
                resolve_wbs=False,
            )
            accuracy, failed_items = verify_ingested_rules(
                preview_rules,
                source_pdf_path=job["pdf_path"],
                sample_n=args.verify_sample_n,
                use_llm=False,
            )
            print(
                "灌入 dry-run: "
                f"candidate_rules={len(preview_rules)}, "
                f"source_nodes={ingest_meta['total_nodes']}, "
                f"skipped_frontmatter={ingest_meta['skipped_frontmatter']}, "
                f"offline_accuracy={accuracy:.0%}, "
                f"failed={len(failed_items)}, "
                f"retire_legacy_candidates={0 if args.keep_legacy else len(get_retirable_legacy_rules(get_all_rules(), category))}"
            )
            if failed_items:
                print(f"失败样本: {failed_items[:3]}")

        if args.ingest:
            from rag_engine.kb_manager import ingest_from_tree_index

            ok, msg = ingest_from_tree_index(
                str(output_path),
                category,
                wbs_code=args.wbs_code,
                level=args.level,
                verify=args.verify_ingest,
                source_pdf_path=job["pdf_path"],
                verify_sample_n=args.verify_sample_n,
                verify_use_llm=args.verify_with_llm,
                min_verify_accuracy=args.min_verify_accuracy,
                include_frontmatter=args.include_frontmatter,
                retire_legacy=not args.keep_legacy,
            )
            if ok:
                print(msg)
            else:
                raise SystemExit(msg)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
