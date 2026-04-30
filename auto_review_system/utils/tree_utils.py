"""
PageIndex 树索引遍历工具
========================
统一 kb_manager.py 和 pdf_parser.py 中对 PageIndex 树 JSON 的遍历逻辑。
"""


def tree_roots(tree_data):
    """提取 PageIndex 树 JSON 的根节点列表（兼容多种格式）。"""
    if isinstance(tree_data, list):
        return tree_data
    if not isinstance(tree_data, dict):
        return []
    for key in ("structure", "tree", "nodes"):
        value = tree_data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    return [tree_data]


def tree_children(node):
    """返回节点的子节点列表。"""
    children = node.get("nodes") or node.get("children") or []
    return children if isinstance(children, list) else []


def flatten_tree_leaf_nodes(tree_data):
    """兼容 PageIndex 原生 JSON 与本项目包装 JSON，提取叶节点。"""
    leaves = []
    all_text_nodes = []

    def visit(node, path):
        if not isinstance(node, dict):
            return
        title = str(node.get("title") or node.get("node_title") or node.get("heading") or "未命名节点")
        current_path = path + [title]
        text = str(node.get("text") or node.get("full_text") or node.get("content") or "")
        summary = str(node.get("summary") or node.get("prefix_summary") or "")
        children = tree_children(node)

        if text.strip() or summary.strip():
            copied = dict(node)
            copied["_path"] = current_path
            all_text_nodes.append(copied)

        if children:
            for child in children:
                visit(child, current_path)
        elif text.strip() or summary.strip():
            copied = dict(node)
            copied["_path"] = current_path
            leaves.append(copied)

    for root in tree_roots(tree_data):
        visit(root, [])

    return leaves or all_text_nodes
